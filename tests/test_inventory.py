import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import openpyxl
from espadna.core import bootstrap_owner, connect, set_role, set_sale_status, caption, phone_text
from espadna.importer import import_workbook, parse_workbook

# Synthetic format-only identifiers; no production workbook or customer records.
PHONE = '09000000000'
OTHER = '09000000001'
HEADER = [' شماره دلخواه', 'نوع سیم کارت', 'وضعیت سیم', 'قیمت', 'شرایط فروش ', 'اپراتور']


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.xlsx'
        self.db = connect(':memory:')
        bootstrap_owner(self.db, 1)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def book(self, rows=None, second=None):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(HEADER)
        for row in rows if rows is not None else [[PHONE, 1, 2, 12345, '=ignored()', 'همراه اول']]:
            ws.append(row)
        if second is not None:
            ws = wb.create_sheet('second')
            ws.append(HEADER)
            for row in second:
                ws.append(row)
        wb.save(self.path)
        wb.close()
        return self.path

    def test_incremental_idempotent_no_delete_no_sale_reset(self):
        self.book([[PHONE, 1, 2, 12345, 1, 'همراه اول'], [OTHER, 2, 1, 0, 2, None]])
        self.assertEqual(import_workbook(self.db, 1, self.path)['inserted'], 2)
        set_sale_status(self.db, 1, PHONE, 'sold')
        self.book()
        self.assertEqual(import_workbook(self.db, 1, self.path)['unchanged'], 1)
        self.book([[PHONE, 2, 3, 999, 'anything', None]])
        self.assertEqual(import_workbook(self.db, 1, self.path)['updated'], 1)
        row = self.db.execute('SELECT * FROM inventory WHERE phone=?', (PHONE,)).fetchone()
        self.assertEqual((row['sale_status'], row['sim_status'], row['phone']), ('sold', 3, PHONE))
        self.assertEqual(self.db.execute('SELECT count(*) FROM inventory').fetchone()[0], 2)

    def test_duplicate_across_sheets(self):
        row = [PHONE, 1, 2, 12345, 1, 'همراه اول']
        self.book([row], [row])
        self.assertEqual(import_workbook(self.db, 1, self.path)['duplicates'], 1)
        row2 = list(row)
        row2[1] = 2
        self.book([row], [row2])
        with self.assertRaises(ValueError):
            import_workbook(self.db, 1, self.path)
        self.assertEqual(self.db.execute('SELECT sim_type FROM inventory').fetchone()[0], 1)

    def test_validation_atomicity(self):
        for index, value in [(0, 9000000000), (1, True), (1, 3), (2, 4), (3, -1), (3, '=1+1'), (5, 'unverified')]:
            with self.subTest(index=index, value=value):
                good = [PHONE, 1, 2, 0, None, 'همراه اول']
                bad = [OTHER, 1, 2, 0, None, 'همراه اول']
                bad[index] = value
                self.book([good, bad])
                with self.assertRaises(ValueError):
                    import_workbook(self.db, 1, self.path)
                self.assertEqual(self.db.execute('SELECT count(*) FROM inventory').fetchone()[0], 0)
                self.assertEqual(self.db.execute('SELECT count(*) FROM import_batches').fetchone()[0], 0)

    def test_dry_run_and_audit(self):
        self.book()
        self.assertEqual(import_workbook(self.db, 1, self.path, True)['inserted'], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM inventory').fetchone()[0], 0)
        import_workbook(self.db, 1, self.path)
        self.assertEqual(self.db.execute("SELECT count(*) FROM audit WHERE action='import'").fetchone()[0], 1)
        with self.assertRaises(Exception):
            self.db.execute('DELETE FROM audit')

    def test_roles_and_last_owner(self):
        set_role(self.db, 1, 2, 'viewer')
        self.book()
        for actor in [2, 99]:
            with self.assertRaises(PermissionError):
                import_workbook(self.db, actor, self.path)
            with self.assertRaises(PermissionError):
                set_role(self.db, actor, 3, 'admin')
        with self.assertRaises(ValueError):
            set_role(self.db, 1, 1, 'viewer')
        set_role(self.db, 1, 3, 'admin')
        import_workbook(self.db, 3, self.path)
        set_sale_status(self.db, 3, PHONE, 'reserved')
        with self.assertRaises(PermissionError):
            set_sale_status(self.db, 2, PHONE, 'sold')

    def test_public_boundary(self):
        self.book()
        import_workbook(self.db, 1, self.path)
        row = self.db.execute('SELECT * FROM inventory').fetchone()
        text = caption(row)
        self.assertNotIn('12345', text)
        self.assertNotIn('price', text)
        self.assertIn(PHONE, text)
        set_sale_status(self.db, 1, PHONE, 'reserved')
        with self.assertRaises(ValueError):
            caption(self.db.execute('SELECT * FROM inventory').fetchone())

    def test_phone_digits_and_spacing(self):
        self.assertEqual(phone_text('۰۹۰۰ ۰۰۰ ۰۰۰۰'), PHONE)
        for value in ['9000000000', '09-000000000', '=09000000000']:
            with self.assertRaises(ValueError):
                phone_text(value)

    def test_database_error_rolls_back_inventory_batch_and_audit(self):
        self.book()
        before = self.db.execute('SELECT count(*) FROM audit').fetchone()[0]
        with patch('espadna.importer.audit', side_effect=RuntimeError('simulated write failure')):
            with self.assertRaises(RuntimeError):
                import_workbook(self.db, 1, self.path)
        for table in ('inventory', 'import_batches'):
            self.assertEqual(self.db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM audit').fetchone()[0], before)

    def test_blank_operator_is_not_inferred(self):
        self.book([[PHONE, 1, 2, None, None, None]])
        import_workbook(self.db, 1, self.path)
        from espadna.core import public_item
        result = public_item(self.db.execute('SELECT * FROM inventory').fetchone())
        self.assertNotIn('operator', result)
        self.assertEqual(set(result), {'phone', 'sim_type', 'sim_status'})

    def test_archive_and_row_limits_and_missing_headers(self):
        self.book()
        with patch('espadna.importer.MAX_BYTES', 1):
            with self.assertRaises(ValueError):
                parse_workbook(self.path)
        with patch('espadna.importer.MAX_UNPACKED', 1):
            with self.assertRaises(ValueError):
                parse_workbook(self.path)
        with patch('espadna.importer.MAX_ROWS', 0):
            with self.assertRaises(ValueError):
                parse_workbook(self.path)
        wb = openpyxl.Workbook()
        wb.active.append(['unknown'])
        wb.save(self.path)
        wb.close()
        with self.assertRaises(ValueError):
            import_workbook(self.db, 1, self.path)

    def test_bot_denies_before_download(self):
        from espadna.bot import build_application
        from types import SimpleNamespace
        import asyncio
        dbpath = Path(self.tmp.name) / 'bot.db'
        db = connect(dbpath)
        bootstrap_owner(db, 1)
        set_role(db, 1, 2, 'viewer')
        db.close()
        app = build_application('123456:TEST_ONLY_PLACEHOLDER', dbpath)
        callback = app.handlers[0][1].callback
        for actor, chat_type in [(2, 'private'), (99, 'private'), (1, 'group')]:
            msg = SimpleNamespace(reply_text=AsyncMock(), document=None)
            update = SimpleNamespace(effective_chat=SimpleNamespace(type=chat_type), effective_user=SimpleNamespace(id=actor), effective_message=msg)
            context = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock()))
            asyncio.run(callback(update, context))
            context.bot.get_file.assert_not_called()
            msg.reply_text.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
