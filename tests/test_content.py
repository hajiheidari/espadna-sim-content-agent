import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from espadna.core import bootstrap_owner, connect, set_role, set_sale_status
from espadna.content import create_plan, inspect_plan, approve_plan, approved_payload, refresh_plan, month_days, create_export, export_status, invalid_exports


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        bootstrap_owner(self.db, 1)
        set_role(self.db, 1, 2, 'viewer')
        set_role(self.db, 1, 3, 'admin')
        self.phones = [f'090000000{i:02}' for i in range(8)]
        for phone in self.phones:
            self.db.execute('INSERT INTO inventory(phone_normalized,phone_display,sim_type,sim_status,operator,price_internal) VALUES(?,?,1,2,NULL,?)', (phone, phone, '87654321.987654'))

    def tearDown(self):
        self.db.close()

    def test_six_per_day_rotation_and_calendar(self):
        plan = create_plan(self.db, 1, '2028-02')
        self.assertEqual(inspect_plan(self.db, 2, plan)['slots'], 29 * 6)
        rows = self.db.execute('SELECT * FROM content_slots WHERE plan_id=? ORDER BY day,position', (plan,)).fetchall()
        for start in range(0, len(rows), 6):
            day = rows[start:start+6]
            self.assertEqual(len({x['phone'] for x in day}), 6)
            self.assertEqual([x['template_id'] for x in day], list(range(((start//6) % 5)*6+1, ((start//6) % 5)*6+7)))
        with self.assertRaises(ValueError):
            create_plan(self.db, 1, '2028-02')

    def test_export_requires_approval_and_never_contains_price(self):
        plan = create_plan(self.db, 3, '2026-11')
        with self.assertRaises(ValueError):
            approved_payload(self.db, 2, plan)
        approve_plan(self.db, 3, plan)
        result = approved_payload(self.db, 2, plan)
        text = json.dumps(result, ensure_ascii=False)
        for forbidden in ('price', '87654321', 'created_by', 'approved_by', 'revision', 'operator'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(result['items'][0]['scheduled_at'], '2026-11-01T08:00:00+03:30')
        self.assertEqual(len(result['items']), 180)

    def test_sale_invalidates_export_and_refresh_replaces_and_requires_approval(self):
        plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, plan)
        set_sale_status(self.db, 1, self.phones[0], 'sold')
        self.assertFalse(inspect_plan(self.db, 2, plan)['ready'])
        with self.assertRaises(ValueError):
            approved_payload(self.db, 2, plan)
        with self.assertRaises(ValueError):
            approve_plan(self.db, 1, plan)
        self.assertGreater(refresh_plan(self.db, 1, plan), 0)
        self.assertEqual(inspect_plan(self.db, 2, plan)['state'], 'draft')
        with self.assertRaises(ValueError):
            approved_payload(self.db, 2, plan)
        approve_plan(self.db, 1, plan)
        self.assertNotIn(self.phones[0], json.dumps(approved_payload(self.db, 2, plan)))

    def test_revision_invalidates_even_after_sale_reversal(self):
        plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, plan)
        set_sale_status(self.db, 1, self.phones[0], 'reserved')
        set_sale_status(self.db, 1, self.phones[0], 'available')
        with self.assertRaises(ValueError):
            approved_payload(self.db, 2, plan)
        refresh_plan(self.db, 1, plan)
        approve_plan(self.db, 1, plan)
        self.assertTrue(inspect_plan(self.db, 2, plan)['ready'])

    def test_refresh_failure_rolls_back(self):
        plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, plan)
        for phone in self.phones[:3]:
            set_sale_status(self.db, 1, phone, 'reserved')
        before = [tuple(x) for x in self.db.execute('SELECT * FROM content_slots')]
        with self.assertRaises(ValueError):
            refresh_plan(self.db, 1, plan)
        self.assertEqual(before, [tuple(x) for x in self.db.execute('SELECT * FROM content_slots')])
        self.assertEqual(inspect_plan(self.db, 2, plan)['state'], 'approved')

    def test_roles_and_dates(self):
        for month in ('2026-00', '2026-13', '2026-1', '1405-01', '2026-11-extra'):
            with self.subTest(month=month), self.assertRaises(ValueError):
                month_days(month)
        with self.assertRaises(PermissionError):
            create_plan(self.db, 2, '2026-11')
        plan = create_plan(self.db, 1, '2026-11')
        for operation in (approve_plan, refresh_plan):
            with self.assertRaises(PermissionError):
                operation(self.db, 2, plan)
        with self.assertRaises(PermissionError):
            inspect_plan(self.db, 99, plan)

    def test_migration_from_v1_preserves_inventory_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.db'
            old = sqlite3.connect(path)
            import espadna.core
            old.executescript(Path(espadna.core.__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            old.execute("INSERT INTO users VALUES(1,'owner')")
            old.execute('INSERT INTO inventory(phone,sim_type,sim_status) VALUES(?,1,2)', (self.phones[0],))
            old.execute("INSERT INTO audit(actor,action,subject) VALUES(1,'test','test')")
            old.commit()
            old.close()
            upgraded = connect(path)
            self.assertEqual(upgraded.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertEqual(upgraded.execute('SELECT phone_normalized FROM inventory').fetchone()[0], self.phones[0])
            self.assertEqual(upgraded.execute('SELECT count(*) FROM audit').fetchone()[0], 1)
            upgraded.close()
            reopened = connect(path)
            self.assertEqual(reopened.execute('SELECT count(*) FROM audit').fetchone()[0], 1)
            reopened.close()

    def test_download_tracking_and_invalidation(self):
        plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, plan)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'private.json'
            export = create_export(self.db, 2, plan, path)
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['export_id'], export)
            self.assertNotIn('price', path.read_text(encoding='utf-8'))
            import hashlib
            self.assertEqual(export_status(self.db, 2, export)['digest'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(invalid_exports(self.db, 2), [])
            set_sale_status(self.db, 1, self.phones[0], 'sold')
            self.assertEqual(invalid_exports(self.db, 2)[0]['export_id'], export)
            refresh_plan(self.db, 1, plan)
            approve_plan(self.db, 1, plan)
            self.assertFalse(export_status(self.db, 2, export)['valid'])
            # Reapproval never makes a downloaded older version valid again.
            new = create_export(self.db, 2, plan, Path(tmp) / 'new.json')
            self.assertTrue(export_status(self.db, 2, new)['valid'])

    def test_export_does_not_overwrite_or_leave_database_records_on_error(self):
        plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, plan)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'existing.json'
            path.write_text('keep me', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                create_export(self.db, 2, plan, path)
            self.assertEqual(path.read_text(encoding='utf-8'), 'keep me')
            self.assertEqual(self.db.execute('SELECT count(*) FROM content_exports').fetchone()[0], 0)
            with self.assertRaises(ValueError):
                create_export(self.db, 1, plan, Path(__file__).resolve().parents[1] / 'private-export.json')

    def test_export_privacy_and_permissions(self):
        plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, plan)
        set_role(self.db, 1, 4, 'viewer')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'one.json'
            export = create_export(self.db, 2, plan, path)
            with self.assertRaises(PermissionError):
                export_status(self.db, 4, export)
            set_sale_status(self.db, 1, self.phones[0], 'sold')
            self.assertEqual(invalid_exports(self.db, 4), [])
            self.assertEqual(len(invalid_exports(self.db, 1)), 1)

    def test_failed_migration_rolls_back_schema_changes(self):
        from unittest.mock import patch
        import espadna.core
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.db'
            old = sqlite3.connect(path)
            old.executescript(Path(espadna.core.__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            old.close()
            def fail_after_change(db, filename):
                db.execute('ALTER TABLE users ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1')
                raise RuntimeError('simulated migration error')
            with patch('espadna.core._run_sql', side_effect=fail_after_change):
                with self.assertRaises(RuntimeError):
                    connect(path)
            old = sqlite3.connect(path)
            self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertNotIn('enabled', [x[1] for x in old.execute('PRAGMA table_info(users)')])
            old.close()
            upgraded = connect(path)
            self.assertEqual(upgraded.execute('PRAGMA user_version').fetchone()[0], 6)
            upgraded.close()

    def test_migration_from_v2_preserves_existing_exports(self):
        import espadna.core
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'v2.db'
            old = sqlite3.connect(path)
            for filename in ('schema.sql', 'migration_002.sql'):
                old.executescript(Path(espadna.core.__file__).with_name(filename).read_text(encoding='utf-8'))
            old.execute("INSERT INTO users(telegram_id,role) VALUES(1,'owner')")
            old.execute("INSERT INTO content_plans(id,month,state,created_by) VALUES(1,'2026-11','approved',1)")
            old.execute("INSERT INTO content_exports(id,actor,plan_id,plan_revision,digest) VALUES('old',1,1,1,'test-digest')")
            old.commit()
            old.close()
            db = connect(path)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertEqual(db.execute("SELECT state,digest FROM content_exports WHERE id='old'").fetchone()['state'], 'ready')
            self.assertEqual(db.execute("SELECT digest FROM content_exports WHERE id='old'").fetchone()[0], 'test-digest')
            db.close()

    def test_migration_from_v3_preserves_builtin_designs(self):
        import espadna.core
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'v3.db'
            old = sqlite3.connect(path)
            for filename in ('schema.sql', 'migration_002.sql', 'migration_003.sql'):
                old.executescript(Path(espadna.core.__file__).with_name(filename).read_text(encoding='utf-8'))
            old.execute("INSERT INTO users(telegram_id,role) VALUES(1,'owner')")
            old.execute("INSERT INTO content_plans(id,month,state,created_by) VALUES(1,'2026-11','approved',1)")
            old.execute("INSERT INTO content_exports(id,actor,plan_id,plan_revision,digest) VALUES('old',1,1,1,'test-digest')")
            old.commit()
            old.close()
            db = connect(path)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertIsNone(db.execute('SELECT design_pack_id FROM content_plans').fetchone()[0])
            self.assertTrue(export_status(db, 1, 'old')['design_current'])
            self.assertIsNone(db.execute('SELECT pack_id FROM design_preferences').fetchone()[0])
            db.close()

    def test_migration_from_v4_preserves_design_selection_and_exports(self):
        import espadna.core
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'v4.db'
            old = sqlite3.connect(path)
            for filename in ('schema.sql', 'migration_002.sql', 'migration_003.sql', 'migration_004.sql'):
                old.executescript(Path(espadna.core.__file__).with_name(filename).read_text(encoding='utf-8'))
            old.execute("INSERT INTO users(telegram_id,role) VALUES(1,'owner')")
            old.execute("INSERT INTO design_packs(id,name,version,digest,manifest_json,storage_path,state,revision,created_by) VALUES('pack','test','1','digest','{}','private-test','approved',2,1)")
            old.execute("INSERT INTO content_plans(id,month,state,created_by,design_pack_id,design_pack_revision) VALUES(1,'2026-11','approved',1,'pack',2)")
            old.execute("INSERT INTO content_exports(id,actor,plan_id,plan_revision,digest,design_pack_id,design_pack_revision,design_digest) VALUES('old',1,1,1,'test-digest','pack',2,'digest')")
            old.commit()
            old.close()
            db = connect(path)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertEqual(db.execute('SELECT design_pack_id FROM content_plans').fetchone()[0], 'pack')
            self.assertTrue(export_status(db, 1, 'old')['valid'])
            self.assertEqual(db.execute('SELECT count(*) FROM delivery_jobs').fetchone()[0], 0)
            db.close()
