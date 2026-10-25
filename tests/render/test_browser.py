"""Optional real-browser QA: python -m unittest discover -s tests/render -v."""
import os
from pathlib import Path
import tempfile
import unittest
import json
import shutil
from espadna.artwork import ASSETS
from espadna.core import bootstrap_owner, connect
from espadna.content import create_plan, approve_plan
from espadna.packs import import_pack, approve_pack, select_plan_pack
from espadna.raster import render_day, verify_bundle
from espadna.artwork import document
from espadna.preview import gallery_items
from espadna.raster import _png_size, render_html


class BrowserRenderTests(unittest.TestCase):
    def test_all_thirty_templates_render_with_bundled_fonts_and_backgrounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = document(gallery_items(), review=False)
            files = render_html(html, Path(tmp), os.environ.get('ESPADNA_BROWSER_CHANNEL'))
            self.assertEqual(len(files), 30)
            for filename in files:
                with self.subTest(filename=filename):
                    path = Path(tmp) / filename
                    self.assertEqual(_png_size(path), (1080, 1920))
                    # An empty/blank rendering must not pass as a valid screenshot.
                    self.assertGreater(path.stat().st_size, 100000)

    def test_private_pack_overlay_and_static_images_use_real_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input'
            source.mkdir()
            shutil.copyfile(ASSETS / 'base.png', source / 'طرح من.PNG')
            manifest = {'schema_version': 1, 'name': 'Browser QA pack', 'version': '1', 'templates': [
                {'id': 1, 'image': 'طرح من.PNG', 'mode': 'overlay', 'layers': [
                    {'field': 'phone', 'x': 100, 'y': 900, 'width': 880, 'height': 140, 'font_size': 80},
                    {'field': 'title', 'x': 100, 'y': 200, 'width': 880, 'height': 300, 'font_size': 62}]},
                {'id': 2, 'image': 'طرح من.PNG', 'mode': 'static', 'layers': []}]}
            (source / 'pack.json').write_text(json.dumps(manifest), encoding='utf-8')
            db = connect(':memory:')
            try:
                bootstrap_owner(db, 1)
                for n in range(6):
                    db.execute('INSERT INTO inventory(phone,sim_type,sim_status) VALUES(?,1,2)', (f'090000000{n:02}',))
                plan = create_plan(db, 1, '2026-11')
                pack = import_pack(db, 1, source, root / 'store')
                approve_pack(db, 1, pack, True)
                select_plan_pack(db, 1, plan, pack)
                approve_plan(db, 1, plan)
                output = root / 'output'
                result = render_day(db, 1, plan, '2026-11-01', output, os.environ.get('ESPADNA_BROWSER_CHANNEL'))
                self.assertTrue(verify_bundle(db, 1, result['export_id'], output)['usable'])
                self.assertEqual(result['design_status'], 'owner_reviewed')
                self.assertEqual(len(list(output.glob('story-*.png'))), 6)
                # A wholly static pack must work even when no font is used.
                manifest['version'] = '2'
                manifest['templates'] = [{'id': 1, 'image': 'طرح من.PNG', 'mode': 'static', 'layers': []}]
                (source / 'pack.json').write_text(json.dumps(manifest), encoding='utf-8')
                static = import_pack(db, 1, source, root / 'store')
                approve_pack(db, 1, static, True)
                select_plan_pack(db, 1, plan, static)
                approve_plan(db, 1, plan)
                static_result = render_day(db, 1, plan, '2026-11-01', root / 'static-output', os.environ.get('ESPADNA_BROWSER_CHANNEL'))
                self.assertTrue(verify_bundle(db, 1, static_result['export_id'], root / 'static-output')['usable'])
            finally:
                db.close()
