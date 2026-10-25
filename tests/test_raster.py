import json
from pathlib import Path
import struct
import tempfile
import unittest
from espadna.content import approve_plan, create_plan, export_status
from espadna.core import bootstrap_owner, connect, revoke_user, set_role, set_sale_status
from espadna.raster import render_day, verify_bundle


def fake_renderer(html, stage, channel):
    # Orchestration test stub; actual browser/PNG decoding is checked separately.
    names = [f'story-{n:02}.png' for n in range(1, 7)]
    for name in names:
        (stage / name).write_bytes(b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 1080, 1920))
    return names


class RasterTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        bootstrap_owner(self.db, 1)
        set_role(self.db, 1, 2, 'viewer')
        for i in range(6):
            self.db.execute('INSERT INTO inventory(phone,sim_type,sim_status,price_internal) VALUES(?,1,2,?)', (f'090000000{i:02}', '76543210'))
        self.plan = create_plan(self.db, 1, '2026-11')
        approve_plan(self.db, 1, self.plan)
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / 'bundle'

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def render(self, worker=fake_renderer):
        return render_day(self.db, 2, self.plan, '2026-11-01', self.output, renderer=worker)

    def test_bundle_is_tracked_private_and_detects_tampering_or_sale(self):
        result = self.render()
        export_id = result['export_id']
        self.assertEqual(len(list(self.output.iterdir())), 9)
        self.assertTrue(verify_bundle(self.db, 2, export_id, self.output)['usable'])
        manifest = json.loads((self.output / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['design_status'], 'requires_visual_review')
        self.assertNotIn('76543210', (self.output / 'editable.html').read_text(encoding='utf-8'))
        (self.output / 'story-01.png').write_bytes(b'changed')
        self.assertFalse(verify_bundle(self.db, 2, export_id, self.output)['usable'])
        set_sale_status(self.db, 1, '09000000000', 'sold')
        self.assertFalse(export_status(self.db, 2, export_id)['valid'])

    def test_inventory_change_during_render_discards_all_outputs(self):
        def change_inventory(html, stage, channel):
            names = fake_renderer(html, stage, channel)
            set_sale_status(self.db, 1, '09000000000', 'reserved')
            return names
        with self.assertRaises(ValueError):
            self.render(change_inventory)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.db.execute('SELECT state FROM content_exports').fetchone()[0], 'failed')
        self.assertEqual(self.db.execute('SELECT count(*) FROM artifact_files').fetchone()[0], 0)

    def test_revocation_during_render_discards_all_outputs(self):
        def revoke(html, stage, channel):
            names = fake_renderer(html, stage, channel)
            revoke_user(self.db, 1, 2)
            return names
        with self.assertRaises(PermissionError):
            self.render(revoke)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.db.execute('SELECT state FROM content_exports').fetchone()[0], 'failed')

    def test_renderer_failure_and_existing_output_are_safe(self):
        def fail(html, stage, channel):
            raise RuntimeError('simulated renderer failure')
        with self.assertRaises(RuntimeError):
            self.render(fail)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.db.execute('SELECT state FROM content_exports').fetchone()[0], 'failed')
        self.output.mkdir()
        keep = self.output / 'keep.txt'
        keep.write_text('keep', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            self.render()
        self.assertEqual(keep.read_text(encoding='utf-8'), 'keep')

    def test_missing_raster_file_never_becomes_ready(self):
        with self.assertRaises(ValueError):
            self.render(lambda html, stage, channel: [])
        self.assertFalse(self.output.exists())
        self.assertEqual(self.db.execute('SELECT state FROM content_exports').fetchone()[0], 'failed')
