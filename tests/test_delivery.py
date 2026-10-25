import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
from espadna.content import approve_plan, create_plan, export_status, invalid_exports, refresh_plan
from espadna.core import bootstrap_owner, connect, revoke_user, set_role, set_sale_status
from espadna.delivery import Bundle, DefiniteRejection, DefiniteRetry, DeliveryUnknown, Media, Receipt, cancel_job, claim_next, enqueue_plan, epoch, job_status, resolve_uncertain, run_once, set_route
from espadna.raster import render_day
from espadna.transports import DryRunSender, TelegramSender
from test_raster import fake_renderer


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 1, 7, 59, tzinfo=ZoneInfo('Asia/Tehran'))

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(':memory:')
        bootstrap_owner(self.db, 1)
        set_role(self.db, 1, 2, 'viewer')
        set_role(self.db, 1, 3, 'admin')
        set_role(self.db, 1, 4, 'viewer')
        for n in range(8):
            self.db.execute('INSERT INTO inventory(phone,sim_type,sim_status,price_internal) VALUES(?,1,2,?)', (f'090000000{n:02}', '76543210'))
        self.plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, self.plan)
        set_route(self.db, 1, 2)
        self.clock = Clock()
        self.sender = DryRunSender()
        self.enqueue()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def enqueue(self, mode='dry_run'):
        return enqueue_plan(self.db, 1, self.plan, mode, self.clock())

    def builder(self, db, actor, plan, day, path, channel=None):
        return render_day(db, actor, plan, day, path, renderer=fake_renderer)

    def tick(self, sender=None, mode='dry_run', builder=None, actor=1, allow_network=False):
        return asyncio.run(run_once(self.db, actor, sender or self.sender, self.root / 'artifacts', mode, allow_network, self.clock, builder or self.builder))

    def due(self):
        self.clock.advance(60)

    def first_job(self, mode='dry_run'):
        return self.db.execute('SELECT * FROM delivery_jobs WHERE mode=? ORDER BY due_at,id LIMIT 1', (mode,)).fetchone()

    def test_tehran_schedule_idempotency_and_dry_run_separation(self):
        self.assertIsNone(self.tick())
        due = self.first_job()['due_at']
        self.assertEqual(datetime.fromtimestamp(due, timezone.utc).isoformat(), '2026-11-01T04:30:00+00:00')
        self.assertEqual(self.enqueue(), {'created': 0, 'updated': 0, 'unchanged': 30})
        self.due()
        result = self.tick()
        self.assertEqual(result['state'], 'simulated')
        self.assertIsNone(self.tick())
        self.assertEqual(len(self.sender.bundles), 1)
        self.assertEqual(self.enqueue()['unchanged'], 30)
        self.assertEqual(self.enqueue('telegram')['created'], 30)
        self.assertNotIn('76543210', str([x.caption for x in self.sender.bundles[0].media]))

    def test_day_expiration_and_no_historical_catch_up(self):
        self.clock.advance(24 * 3600)
        self.assertIsNone(self.tick())
        self.assertEqual(self.first_job()['state'], 'expired')
        self.assertEqual(self.enqueue('telegram')['created'], 29)
        with self.assertRaises(ValueError):
            epoch(datetime(2026, 11, 1))

    def test_exclusive_claim_and_safe_recovery_before_send(self):
        self.due()
        first = claim_next(self.db, 1, now=self.clock())
        self.assertIsNone(claim_next(self.db, 3, now=self.clock()))
        self.clock.advance(301)
        second = claim_next(self.db, 3, now=self.clock())
        self.assertEqual(first['id'], second['id'])
        self.assertNotEqual(first['lease_token'], second['lease_token'])
        self.assertEqual(second['attempts'], 2)

    def test_two_connections_can_claim_a_job_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        path = self.root / 'shared.db'
        db = connect(path)
        self.db.backup(db)
        db.close()
        self.due()
        def claim(actor):
            worker = connect(path)
            try:
                return claim_next(worker, actor, now=self.clock())
            finally:
                worker.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(claim, (1, 3)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_lost_sending_worker_is_quarantined_and_cannot_be_cancelled(self):
        self.due()
        claimed = claim_next(self.db, 1, now=self.clock())
        self.db.execute("UPDATE delivery_jobs SET state='sending' WHERE id=?", (claimed['id'],))
        self.clock.advance(301)
        self.assertIsNone(claim_next(self.db, 1, now=self.clock()))
        self.assertEqual(job_status(self.db, 1, claimed['id'])['state'], 'uncertain')
        with self.assertRaises(ValueError):
            cancel_job(self.db, 1, claimed['id'])
        self.enqueue()
        self.assertEqual(job_status(self.db, 1, claimed['id'])['state'], 'uncertain')
        with self.assertRaises(PermissionError):
            resolve_uncertain(self.db, 3, claimed['id'], True, range(1, 7), self.clock())
        resolve_uncertain(self.db, 1, claimed['id'], True, range(1, 7), self.clock())
        self.assertEqual(job_status(self.db, 1, claimed['id'])['state'], 'simulated')

    def test_ambiguous_result_never_auto_retries(self):
        sender = SimpleNamespace(mode='dry_run', send_bundle=AsyncMock(side_effect=TimeoutError('SECRET_TOKEN')))
        self.due()
        result = self.tick(sender)
        self.assertEqual(result['state'], 'uncertain')
        self.clock.advance(600)
        self.assertIsNone(self.tick(sender))
        sender.send_bundle.assert_awaited_once()
        self.assertNotIn('SECRET_TOKEN', '\n'.join(str(tuple(x)) for x in self.db.execute('SELECT * FROM delivery_attempts')))
        resolve_uncertain(self.db, 1, result['id'], False, now=self.clock())
        self.assertEqual(self.tick()['state'], 'simulated')

    def test_late_confirmed_receipt_resolves_recovered_uncertainty_without_resending(self):
        async def slow_send(bundle):
            self.clock.advance(301)
            self.assertIsNone(claim_next(self.db, 3, now=self.clock()))
            self.assertEqual(self.first_job()['state'], 'uncertain')
            return Receipt(tuple(range(1, 7)))
        sender = SimpleNamespace(mode='dry_run', send_bundle=AsyncMock(side_effect=slow_send))
        self.due()
        self.assertEqual(self.tick(sender)['state'], 'simulated')
        sender.send_bundle.assert_awaited_once()

    def test_wrong_or_incomplete_receipt_is_uncertain(self):
        sender = SimpleNamespace(mode='dry_run', send_bundle=AsyncMock(return_value=Receipt((1,))))
        self.due()
        self.assertEqual(self.tick(sender)['state'], 'uncertain')
        self.assertIsNone(self.tick(sender))
        sender.send_bundle.assert_awaited_once()

    def test_definite_rate_rejection_retries_only_after_requested_delay(self):
        sender = SimpleNamespace(mode='dry_run', send_bundle=AsyncMock(side_effect=DefiniteRetry(120)))
        self.due()
        self.assertEqual(self.tick(sender)['state'], 'retry')
        self.clock.advance(119)
        self.assertIsNone(self.tick())
        self.clock.advance(1)
        result = self.tick()
        self.assertEqual((result['state'], result['attempts']), ('simulated', 2))

    def test_preparation_failures_have_bounded_retries(self):
        def fail(*args, **kwargs):
            raise OSError('private-path-must-not-be-logged')
        self.due()
        for attempt in range(5):
            result = self.tick(builder=fail)
            self.assertEqual(result['attempts'], attempt + 1)
            self.clock.advance(3600)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(len(self.sender.bundles), 0)

    def test_stale_inventory_blocks_until_reviewed_revision_is_enqueued(self):
        set_sale_status(self.db, 1, '09000000000', 'reserved')
        self.due()
        self.assertEqual(self.tick()['state'], 'blocked')
        self.assertEqual(len(self.sender.bundles), 0)
        refresh_plan(self.db, 1, self.plan)
        approve_plan(self.db, 1, self.plan)
        self.assertEqual(self.enqueue()['updated'], 30)
        self.assertEqual(self.tick()['state'], 'simulated')

    def test_disabling_recipient_during_render_prevents_dispatch(self):
        def disable(*args, **kwargs):
            result = self.builder(*args, **kwargs)
            set_route(self.db, 1, 2, False)
            return result
        self.due()
        self.assertEqual(self.tick(builder=disable)['state'], 'blocked')
        self.assertEqual(len(self.sender.bundles), 0)

    def test_revoking_worker_during_render_prevents_dispatch(self):
        def revoke(*args, **kwargs):
            result = self.builder(*args, **kwargs)
            revoke_user(self.db, 1, 3)
            return result
        self.due()
        with self.assertRaises(PermissionError):
            self.tick(builder=revoke, actor=3)
        self.assertEqual(self.first_job()['state'], 'blocked')
        self.assertEqual(len(self.sender.bundles), 0)

    def test_tampering_with_artifact_prevents_dispatch(self):
        def tamper(db, actor, plan, day, path, channel=None):
            result = self.builder(db, actor, plan, day, path)
            (path / 'story-01.png').write_bytes(b'tampered')
            return result
        self.due()
        self.assertEqual(self.tick(builder=tamper)['state'], 'blocked')
        self.assertEqual(len(self.sender.bundles), 0)

    def test_valid_bundle_for_wrong_day_is_not_dispatched(self):
        def wrong_day(db, actor, plan, day, path, channel=None):
            return self.builder(db, actor, plan, '2026-11-02', path)
        self.due()
        self.assertEqual(self.tick(builder=wrong_day)['state'], 'blocked')
        self.assertEqual(len(self.sender.bundles), 0)

    def test_live_mode_is_opt_in_and_delivered_exports_are_visible_to_recipient(self):
        self.enqueue('telegram')
        bot = SimpleNamespace(send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=n) for n in range(1, 7)]))
        sender = TelegramSender(bot)
        self.due()
        with self.assertRaises(PermissionError):
            self.tick(sender, 'telegram')
        with self.assertRaises(PermissionError):
            self.tick(sender, 'telegram', actor=3, allow_network=True)
        bot.send_media_group.assert_not_called()
        result = self.tick(sender, 'telegram', allow_network=True)
        self.assertEqual(result['state'], 'sent')
        args = bot.send_media_group.call_args.kwargs
        self.assertEqual(args['chat_id'], 2)
        self.assertEqual(len(args['media']), 6)
        self.assertTrue(export_status(self.db, 2, result['export_id'])['valid'])
        with self.assertRaises(PermissionError):
            export_status(self.db, 4, result['export_id'])
        set_sale_status(self.db, 1, '09000000000', 'sold')
        self.assertEqual(invalid_exports(self.db, 2)[0]['export_id'], result['export_id'])
        refresh_plan(self.db, 1, self.plan)
        approve_plan(self.db, 1, self.plan)
        self.enqueue('telegram')
        self.assertIsNone(self.tick(sender, 'telegram', allow_network=True))
        bot.send_media_group.assert_awaited_once()

    def test_routes_and_queue_permissions(self):
        for recipient in (-100, '@channel', True):
            with self.assertRaises(ValueError):
                set_route(self.db, 1, recipient)
        with self.assertRaises(PermissionError):
            set_route(self.db, 3, 2)
        with self.assertRaises(PermissionError):
            enqueue_plan(self.db, 2, self.plan, now=self.clock())
        with self.assertRaises(PermissionError):
            job_status(self.db, 4, self.first_job()['id'])
        revoke_user(self.db, 1, 2)
        set_route(self.db, 1, 2, False)
        self.due()
        self.assertEqual(self.tick()['state'], 'blocked')

    def test_runtime_does_not_construct_bot_without_explicit_network_launch(self):
        from unittest.mock import patch
        from espadna.delivery_runtime import run_ticks
        with patch('telegram.Bot') as bot:
            with self.assertRaises(PermissionError):
                asyncio.run(run_ticks(self.db, 1, self.root, 'telegram'))
            bot.assert_not_called()
        with self.assertRaises(ValueError):
            asyncio.run(run_ticks(self.db, 1, self.root, limit=0))


class TransportTests(unittest.TestCase):
    def test_myid_does_not_bootstrap_or_authorize_an_unknown_user(self):
        from espadna.bot import build_application
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'empty.db'
            db = connect(path)
            db.close()
            app = build_application('123456:TEST_ONLY_PLACEHOLDER', path)
            callback = app.handlers[0][0].callback
            msg = SimpleNamespace(text='/myid', reply_text=AsyncMock())
            update = SimpleNamespace(effective_chat=SimpleNamespace(type='private'), effective_user=SimpleNamespace(id=9), effective_message=msg)
            asyncio.run(callback(update, SimpleNamespace(args=[])))
            self.assertIn('9', msg.reply_text.call_args.args[0])
            db = connect(path)
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 0)
            db.close()

    def test_telegram_classifies_server_rejections_and_unknown_network_results(self):
        from telegram.error import BadRequest, NetworkError, RetryAfter
        bundle = Bundle(2, 'synthetic', tuple(Media(f'{n}.png', b'png', 'public caption') for n in range(6)))
        for error, expected in ((RetryAfter(10), DefiniteRetry), (BadRequest('SECRET'), DefiniteRejection), (NetworkError('SECRET'), DeliveryUnknown)):
            bot = SimpleNamespace(send_media_group=AsyncMock(side_effect=error))
            with self.subTest(error=type(error).__name__), self.assertRaises(expected):
                asyncio.run(TelegramSender(bot).send_bundle(bundle))
