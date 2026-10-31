import asyncio
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from espadna.content import approve_plan, create_plan
from espadna.core import bootstrap_owner, connect
from espadna.delivery import deliver_manual, enqueue_plan
from espadna.members import (add_employee, daily_globally_enabled, employee_allowed,
                             list_employees, request_membership, review_membership,
                             set_daily_global, set_employee_daily, set_employee_state)
from espadna.transports import DryRunSender
from test_raster import fake_renderer


class MembershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(self.root / 'test.db')
        bootstrap_owner(self.db, 1)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def inventory_and_plan(self):
        for index in range(6):
            phone = f'091000000{index:02}'
            display = f'{phone[:4]} {phone[4:7]} {phone[7:]}'
            self.db.execute('INSERT INTO inventory(phone_normalized,phone_display,sim_type,sim_status,operator) VALUES(?,?,1,2,?)',
                            (phone, display, 'همراه اول'))
        plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, plan)
        return plan

    def test_join_request_owner_review_and_employee_controls(self):
        self.assertEqual(request_membership(self.db, 200, 'test_staff', 'Test'), 'created')
        self.assertEqual(request_membership(self.db, 200, 'test_staff', 'Test'), 'pending')
        self.assertFalse(employee_allowed(self.db, 200))
        self.assertEqual(review_membership(self.db, 1, 200, True), 'approved')
        self.assertTrue(employee_allowed(self.db, 200))
        row = list_employees(self.db, 1)[0]
        self.assertEqual((row['telegram_id'], row['status'], row['daily_enabled']), (200, 'approved', 1))
        set_employee_daily(self.db, 1, 200, False)
        self.assertEqual(list_employees(self.db, 1)[0]['daily_enabled'], 0)
        set_employee_state(self.db, 1, 200, 'disabled')
        self.assertFalse(employee_allowed(self.db, 200))
        set_employee_state(self.db, 1, 200, 'approved')
        self.assertTrue(employee_allowed(self.db, 200))
        set_employee_state(self.db, 1, 200, 'removed')
        self.assertFalse(employee_allowed(self.db, 200))
        self.assertEqual(self.db.execute('SELECT count(*) FROM membership_requests WHERE telegram_id=200').fetchone()[0], 1)

    def test_rejection_does_not_grant_viewer_role(self):
        request_membership(self.db, 201)
        self.assertEqual(review_membership(self.db, 1, 201, False), 'rejected')
        self.assertFalse(employee_allowed(self.db, 201))
        self.assertIsNone(self.db.execute('SELECT role FROM users WHERE telegram_id=201').fetchone())

    def test_global_daily_gate_stays_off_until_explicit_owner_activation(self):
        plan = self.inventory_and_plan()
        add_employee(self.db, 1, 200)
        self.assertFalse(daily_globally_enabled(self.db))
        now = datetime(2026, 11, 1, 7, 0, tzinfo=ZoneInfo('Asia/Tehran'))
        with self.assertRaises(PermissionError):
            enqueue_plan(self.db, 1, plan, 'telegram', now)
        set_daily_global(self.db, 1, True)
        result = enqueue_plan(self.db, 1, plan, 'telegram', now)
        self.assertEqual(result['created'], 30)
        due = self.db.execute("SELECT due_at FROM delivery_jobs WHERE mode='telegram' ORDER BY day LIMIT 1").fetchone()[0]
        self.assertEqual(datetime.fromtimestamp(due, ZoneInfo('Asia/Tehran')).strftime('%H:%M'), '08:00')

    def test_authorized_test_account_receives_six_original_manual_stories(self):
        plan = self.inventory_and_plan()
        add_employee(self.db, 1, 200)
        sender = DryRunSender()

        def builder(db, actor, plan_id, day, path, channel=None):
            from espadna.raster import render_day
            return render_day(db, actor, plan_id, day, path, renderer=fake_renderer)

        result = asyncio.run(deliver_manual(self.db, 200, plan, '2026-11-01', sender,
                                            self.root / 'manual',
                                            datetime(2026, 11, 1, 8, 1, tzinfo=ZoneInfo('Asia/Tehran')),
                                            builder))
        self.assertEqual(result['state'], 'sent')
        self.assertEqual(len(sender.bundles), 1)
        self.assertEqual(len(sender.bundles[0].media), 6)
        self.assertTrue(all(item.filename.endswith('.png') for item in sender.bundles[0].media))
        self.assertNotIn('76543210', str(sender.bundles[0].media))
        self.assertEqual(self.db.execute("SELECT state FROM manual_delivery_requests WHERE id=?", (result['id'],)).fetchone()[0], 'sent')

    def test_bot_start_submits_request_and_owner_button_approves_test_account(self):
        from espadna.bot import build_application
        app = build_application('123456:TEST_ONLY_PLACEHOLDER', self.root / 'test.db')
        command = app.handlers[0][0].callback
        msg = SimpleNamespace(text='/start', reply_text=AsyncMock())
        applicant = SimpleNamespace(id=200, username='test_staff', first_name='Test')
        bot = SimpleNamespace(send_message=AsyncMock())
        update = SimpleNamespace(effective_chat=SimpleNamespace(type='private'), effective_user=applicant, effective_message=msg)
        asyncio.run(command(update, SimpleNamespace(args=[], bot=bot)))
        self.assertIn('ارسال شد', msg.reply_text.call_args.args[0])
        owner_call = bot.send_message.call_args
        self.assertEqual(owner_call.kwargs['chat_id'], 1)
        buttons = owner_call.kwargs['reply_markup'].inline_keyboard[0]
        self.assertEqual([button.callback_data for button in buttons], ['member:approve:200', 'member:reject:200'])

        membership_handler = next(handler for handler in app.handlers[1] if handler.callback.__name__ == 'membership_callback')
        query = SimpleNamespace(data='member:approve:200', answer=AsyncMock(), edit_message_text=AsyncMock())
        owner_update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(type='private'),
                                       effective_user=SimpleNamespace(id=1), effective_message=SimpleNamespace())
        asyncio.run(membership_handler.callback(owner_update, SimpleNamespace(bot=bot)))
        self.assertTrue(employee_allowed(self.db, 200))
        query.edit_message_text.assert_awaited()


if __name__ == '__main__':
    unittest.main()
