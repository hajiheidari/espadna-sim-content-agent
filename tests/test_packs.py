import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from zipfile import ZipFile
from espadna.artwork import ASSETS, document
from espadna.content import approve_plan, approved_payload, create_export, create_plan, export_status, inspect_plan, refresh_plan
from espadna.core import bootstrap_owner, connect, set_role
from espadna.packs import approve_pack, disable_pack, import_pack, list_packs, load_pack, select_plan_pack, set_default_pack, validate_manifest
from espadna.raster import render_day, verify_bundle
from test_raster import fake_renderer


def example_manifest():
    return {'schema_version': 1, 'name': 'Private test pack', 'version': '1', 'templates': [
        {'id': 1, 'image': 'one.png', 'mode': 'overlay', 'layers': [
            {'field': 'phone', 'x': 100, 'y': 900, 'width': 880, 'height': 140, 'font_size': 80},
            {'field': 'operator', 'x': 100, 'y': 1100, 'width': 880, 'height': 90, 'font_size': 40},
        ]}]}


class PackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        shutil.copyfile(ASSETS / 'base.png', self.source / 'one.png')
        self.manifest = example_manifest()
        self.save()
        self.storage = self.root / 'private-store'
        self.db = connect(':memory:')
        bootstrap_owner(self.db, 1)
        set_role(self.db, 1, 2, 'viewer')
        set_role(self.db, 1, 3, 'admin')
        for i in range(6):
            phone = f'090000000{i:02}'
            display = f'{phone[:4]} {phone[4:7]} {phone[7:]}'
            self.db.execute('INSERT INTO inventory(phone_normalized,phone_display,sim_type,sim_status,price_internal) VALUES(?,?,1,2,?)', (phone, display, '76543210'))
        self.plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, self.plan)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def save(self):
        (self.source / 'pack.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def imported(self, approved=True):
        pack = import_pack(self.db, 1, self.source, self.storage)
        if approved:
            approve_pack(self.db, 1, pack, True)
        return pack

    def test_owner_only_import_review_and_private_listing(self):
        for actor in (2, 3, 99):
            with self.assertRaises(PermissionError):
                import_pack(self.db, actor, self.source, self.storage)
        pack = self.imported(False)
        self.assertEqual(list_packs(self.db, 2), [])
        with self.assertRaises(ValueError):
            approve_pack(self.db, 1, pack)
        with self.assertRaises(PermissionError):
            approve_pack(self.db, 3, pack, True)
        with self.assertRaises(ValueError):
            select_plan_pack(self.db, 1, self.plan, pack)
        approve_pack(self.db, 1, pack, True)
        self.assertEqual(list_packs(self.db, 2)[0]['id'], pack)
        self.assertNotIn('storage_path', list_packs(self.db, 2)[0])

    def test_switching_pack_requires_reapproval_and_invalidates_old_export(self):
        old = create_export(self.db, 2, self.plan, self.root / 'old.json')
        pack = self.imported()
        with self.assertRaises(PermissionError):
            select_plan_pack(self.db, 2, self.plan, pack)
        select_plan_pack(self.db, 3, self.plan, pack)
        self.assertEqual(inspect_plan(self.db, 2, self.plan)['state'], 'draft')
        self.assertFalse(export_status(self.db, 2, old)['valid'])
        with self.assertRaises(ValueError):
            approved_payload(self.db, 2, self.plan)
        approve_plan(self.db, 3, self.plan)
        self.assertTrue(inspect_plan(self.db, 2, self.plan)['ready'])

    def test_default_applies_only_to_new_plans_and_never_silently_falls_back(self):
        pack = self.imported()
        with self.assertRaises(PermissionError):
            set_default_pack(self.db, 3, pack)
        set_default_pack(self.db, 1, pack)
        new = create_plan(self.db, 3, '2026-12')
        self.assertIsNone(inspect_plan(self.db, 2, self.plan)['design_pack_id'])
        self.assertEqual(inspect_plan(self.db, 2, new)['design_pack_id'], pack)
        disable_pack(self.db, 1, pack)
        with self.assertRaises(ValueError):
            create_plan(self.db, 1, '2027-01')
        set_default_pack(self.db, 1, None)
        self.assertIsInstance(create_plan(self.db, 1, '2027-01'), int)

    def test_disable_and_reenable_never_revalidates_old_downloads(self):
        pack = self.imported()
        select_plan_pack(self.db, 1, self.plan, pack)
        approve_plan(self.db, 1, self.plan)
        export = create_export(self.db, 2, self.plan, self.root / 'old.json')
        disable_pack(self.db, 1, pack)
        self.assertFalse(export_status(self.db, 2, export)['valid'])
        approve_pack(self.db, 1, pack, True)
        self.assertFalse(inspect_plan(self.db, 2, self.plan)['design_ready'])
        with self.assertRaises(ValueError):
            approve_plan(self.db, 1, self.plan)
        refresh_plan(self.db, 1, self.plan)
        approve_plan(self.db, 1, self.plan)
        self.assertFalse(export_status(self.db, 2, export)['valid'])

    def test_import_is_idempotent_and_existing_version_is_immutable(self):
        pack = self.imported()
        self.assertEqual(import_pack(self.db, 1, self.source, self.storage), pack)
        self.assertEqual(len(list(self.storage.iterdir())), 1)
        self.manifest['templates'][0]['layers'][0]['y'] = 800
        self.save()
        with self.assertRaises(ValueError):
            import_pack(self.db, 1, self.source, self.storage)
        self.manifest['version'] = '2'
        self.save()
        self.assertNotEqual(import_pack(self.db, 1, self.source, self.storage), pack)

    def test_zip_import_and_archive_path_traversal_rejection(self):
        archive = self.root / 'pack.zip'
        with ZipFile(archive, 'w') as zip_file:
            for name in ('pack.json', 'one.png'):
                zip_file.write(self.source / name, name)
        pack = import_pack(self.db, 1, archive, self.storage)
        self.assertTrue(pack)
        with ZipFile(archive, 'w') as zip_file:
            zip_file.writestr('../one.png', b'bad')
            zip_file.write(self.source / 'pack.json', 'pack.json')
        with self.assertRaises(ValueError):
            import_pack(self.db, 1, archive, self.storage)
        self.assertFalse((self.root / 'one.png').exists())

    def test_bad_image_and_git_storage_rejected_without_database_changes(self):
        with self.assertRaises(ValueError):
            import_pack(self.db, 1, self.source, Path(__file__).resolve().parents[1] / 'private')
        (self.source / 'one.png').write_bytes(b'not a PNG')
        with self.assertRaises(ValueError):
            import_pack(self.db, 1, self.source, self.storage)
        self.assertFalse(self.storage.exists())
        self.assertEqual(self.db.execute('SELECT count(*) FROM design_packs').fetchone()[0], 0)

    def test_layer_validation_rejects_prices_code_and_out_of_bounds(self):
        for key, value in [('field', 'price_internal'), ('field', ['phone']), ('x', True), ('x', 1070), ('color', '#ffffff'), ('color', 'url(http://example.invalid)')]:
            manifest = copy.deepcopy(self.manifest)
            manifest['templates'][0]['layers'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_manifest(manifest)
        for filename in ('../one.png', 'https://example.invalid/one.png', 'one.svg'):
            manifest = copy.deepcopy(self.manifest)
            manifest['templates'][0]['image'] = filename
            with self.assertRaises(ValueError):
                validate_manifest(manifest)

    def test_custom_render_uses_live_public_fields_and_escapes_literal_text(self):
        self.manifest['templates'][0]['layers'].append({'field': 'text', 'text': '<script>literal</script>', 'x': 100, 'y': 300, 'width': 880, 'height': 200, 'font_size': 50})
        self.save()
        pack = self.imported()
        select_plan_pack(self.db, 1, self.plan, pack)
        approve_plan(self.db, 1, self.plan)
        payload = approved_payload(self.db, 2, self.plan)
        html = document(payload['items'][:6], review=False, pack=load_pack(self.db, pack))
        from espadna.preview import preview_day
        self.assertIn('data-design-kind="custom"', preview_day(self.db, 2, self.plan, '2026-11-01'))
        self.assertIn('0900 000 0000', html)
        self.assertNotIn('76543210', html)
        self.assertNotIn('data-field="operator"', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;literal&lt;/script&gt;', html)
        self.assertNotIn(str(self.storage), html)
        result = render_day(self.db, 2, self.plan, '2026-11-01', self.root / 'bundle', renderer=fake_renderer)
        self.assertTrue(verify_bundle(self.db, 2, result['export_id'], self.root / 'bundle')['usable'])

    def test_static_ready_made_images_work_without_number_overlays(self):
        self.manifest['templates'][0].update(mode='static', layers=[])
        self.save()
        pack = self.imported()
        select_plan_pack(self.db, 1, self.plan, pack)
        approve_plan(self.db, 1, self.plan)
        html = document(approved_payload(self.db, 2, self.plan)['items'][:6], review=False, pack=load_pack(self.db, pack))
        self.assertIn('layout-custom', html)
        self.assertNotIn('data-field="phone"', html)

    def test_pack_file_tampering_before_or_during_render_is_blocked(self):
        pack = self.imported()
        select_plan_pack(self.db, 1, self.plan, pack)
        approve_plan(self.db, 1, self.plan)
        row, manifest, _ = load_pack(self.db, pack)
        path = Path(row['storage_path']) / manifest['templates'][0]['image']
        def tamper(html, stage, channel):
            names = fake_renderer(html, stage, channel)
            path.write_bytes(b'tampered')
            return names
        with self.assertRaises(ValueError):
            render_day(self.db, 2, self.plan, '2026-11-01', self.root / 'bundle', renderer=tamper)
        self.assertFalse((self.root / 'bundle').exists())
        self.assertEqual(self.db.execute('SELECT state FROM content_exports').fetchone()[0], 'failed')
        with self.assertRaises(ValueError):
            load_pack(self.db, pack)

    def test_audit_failure_rolls_back_import_and_removes_private_copies(self):
        from unittest.mock import patch
        with patch('espadna.packs.audit', side_effect=RuntimeError('simulated database failure')):
            with self.assertRaises(RuntimeError):
                self.imported(False)
        self.assertEqual(self.db.execute('SELECT count(*) FROM design_packs').fetchone()[0], 0)
        self.assertEqual(list(self.storage.iterdir()), [])

    def test_telegram_zip_import_is_owner_only_and_never_uses_network(self):
        import asyncio
        import os
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from espadna.bot import build_application
        archive = self.root / 'telegram.zip'
        with ZipFile(archive, 'w') as zip_file:
            for name in ('pack.json', 'one.png'):
                zip_file.write(self.source / name, name)
        path = self.root / 'bot.db'
        db = connect(path)
        bootstrap_owner(db, 1)
        set_role(db, 1, 3, 'admin')
        db.close()
        app = build_application('123456:TEST_ONLY_PLACEHOLDER', path)
        callback = app.handlers[0][1].callback
        async def download(destination):
            shutil.copyfile(archive, destination)
        with patch.dict(os.environ, {'ESPADNA_PACK_STORE': str(self.storage)}):
            for actor in (3, 1):
                msg = SimpleNamespace(reply_text=AsyncMock(), document=SimpleNamespace(file_name='pack.zip', file_size=archive.stat().st_size, file_id='test'))
                update = SimpleNamespace(effective_chat=SimpleNamespace(type='private'), effective_user=SimpleNamespace(id=actor), effective_message=msg)
                context = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=AsyncMock(side_effect=download)))))
                asyncio.run(callback(update, context))
                if actor == 3:
                    context.bot.get_file.assert_not_called()
                else:
                    context.bot.get_file.assert_awaited_once()
        db = connect(path)
        self.assertEqual(db.execute('SELECT count(*) FROM design_packs').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT state FROM design_packs').fetchone()[0], 'draft')
        db.close()
