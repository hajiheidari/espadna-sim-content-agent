"""Optional real-browser QA: python -m unittest discover -s tests/render -v."""
import os
from pathlib import Path
import tempfile
import unittest
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
