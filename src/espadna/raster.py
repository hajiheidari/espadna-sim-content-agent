"""Local PNG rendering with a private revision-tracked bundle; no publishing."""
from datetime import date
import hashlib
import json
from pathlib import Path
import struct
import tempfile
from .artwork import DESIGN_VERSION, document
from .content import create_export, export_status
from .core import audit, require


def render_html(html, directory, channel=None):
    """Optional browser worker. Bundle fonts/images are offline data URLs."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ValueError('install the render extra and a local Chromium browser first') from None
    result = []
    with sync_playwright() as worker:
        options = {'headless': True}
        if channel:
            options['channel'] = channel
        browser = worker.chromium.launch(**options)
        try:
            context = browser.new_context(viewport={'width': 1080, 'height': 1920}, device_scale_factor=1, java_script_enabled=False)
            context.route('**/*', lambda route: route.abort() if route.request.url.startswith(('http:', 'https:')) else route.continue_())
            page = context.new_page()
            page.set_content(html, wait_until='load')
            page.evaluate('document.fonts.ready')
            if not page.evaluate("document.fonts.check('700 76px Vazirmatn')"):
                raise ValueError('bundled Persian font did not load')
            cards = page.locator('.story')
            for index in range(cards.count()):
                card = cards.nth(index)
                check = card.evaluate('''card => ({
                    width:card.offsetWidth,height:card.offsetHeight,
                    background:getComputedStyle(card).backgroundImage !== 'none',
                    clipped:[...card.querySelectorAll('.phone')].some(n=>n.scrollWidth>n.clientWidth+1),
                    titleOverlap:!card.classList.contains('layout-brand')&&card.querySelector('h2').getBoundingClientRect().bottom>card.querySelector('.composition').getBoundingClientRect().top,
                    ctaOverlap:!['layout-phone','layout-map','layout-gift','layout-tree','layout-brand','layout-hero'].some(k=>card.classList.contains(k))&&card.querySelector('.composition').getBoundingClientRect().bottom>card.querySelector('.cta').getBoundingClientRect().top,
                    gold:[...card.querySelectorAll('.phone')].every(n=>getComputedStyle(n).color==='rgb(255, 230, 154)'&&getComputedStyle(n).direction==='ltr')
                })''')
                if check['width'] != 1080 or check['height'] != 1920 or not check['background'] or check['clipped'] or not check['gold'] or check['titleOverlap'] or check['ctaOverlap']:
                    raise ValueError('artwork layout validation failed')
                filename = f'story-{index+1:02}.png'
                card.screenshot(path=str(Path(directory) / filename), animations='disabled')
                result.append(filename)
        finally:
            browser.close()
    return result


def _png_size(path):
    with Path(path).open('rb') as file:
        header = file.read(24)
    if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR':
        raise ValueError('invalid raster output')
    return struct.unpack('>II', header[16:24])


def render_day(db, actor, plan_id, day, output, channel=None, renderer=None):
    """Stage privately, then revalidate inventory/role before making a ready bundle.

    Export manifests track the entire monthly snapshot conservatively, including
    same-day comparison peers. Files remain private and are never sent here.
    """
    require(db, actor, {'owner', 'admin', 'viewer'})
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('day must be ISO YYYY-MM-DD')
    target = Path(output).resolve()
    if any((parent / '.git').exists() for parent in target.parents):
        raise ValueError('rendered output must be outside a Git checkout')
    if target.exists():
        raise FileExistsError('output directory already exists')
    if not target.parent.is_dir():
        raise ValueError('output parent must exist')
    export_id = None
    published_files = []
    made_target = False
    try:
        with tempfile.TemporaryDirectory(prefix='espadna-render-', dir=target.parent) as tmp:
            stage = Path(tmp)
            export_id = create_export(db, actor, plan_id, stage / 'inventory.json', pending=True)
            snapshot = json.loads((stage / 'inventory.json').read_text(encoding='utf-8'))
            items = [{**item, 'day': day} for item in snapshot['items'] if item['scheduled_at'][:10] == day]
            if len(items) != 6:
                raise ValueError('day not present in plan')
            html = document(items, title=f'اسپادنا سیم — {day}', review=False)
            (stage / 'editable.html').write_text(html, encoding='utf-8')
            names = (renderer or render_html)(html, stage, channel)
            expected = [f'story-{n:02}.png' for n in range(1, 7)]
            if names != expected or any(_png_size(stage / name) != (1080, 1920) for name in names):
                raise ValueError('expected six 1080x1920 PNG files')
            names += ['editable.html', 'inventory.json']
            hashes = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in names}
            manifest = {'export_id': export_id, 'day': day, 'design_version': DESIGN_VERSION,
                        'design_status': 'requires_visual_review', 'files': hashes}
            (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            hashes['manifest.json'] = hashlib.sha256((stage / 'manifest.json').read_bytes()).hexdigest()
            db.execute('BEGIN IMMEDIATE')
            try:
                require(db, actor, {'owner', 'admin', 'viewer'})
                status = export_status(db, actor, export_id)
                if not status['inventory_current'] or status['state'] != 'pending':
                    raise ValueError('inventory changed during rendering; discard output')
                target.mkdir()  # Exclusive: never overwrite an existing folder.
                made_target = True
                for name in hashes:
                    (stage / name).rename(target / name)
                    published_files.append(target / name)
                    db.execute('INSERT INTO artifact_files VALUES(?,?,?)', (export_id, name, hashes[name]))
                db.execute("UPDATE content_exports SET state='ready' WHERE id=?", (export_id,))
                audit(db, actor, 'render_ready', export_id, after={'day': day, 'design_version': DESIGN_VERSION, 'images': 6})
                db.execute('COMMIT')
            except Exception:
                db.execute('ROLLBACK')
                raise
        return {'export_id': export_id, 'images': 6, 'day': day, 'design_status': 'requires_visual_review'}
    except Exception:
        # Only remove files created by this invocation. Do not recursively delete
        # a user-selected directory or remove any unrelated files.
        for path in published_files:
            path.unlink(missing_ok=True)
        if made_target:
            try:
                target.rmdir()
            except OSError:
                pass
        if export_id:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute("UPDATE content_exports SET state='failed' WHERE id=?", (export_id,))
                audit(db, actor, 'render_failed', export_id)
                db.execute('COMMIT')
            except Exception:
                db.execute('ROLLBACK')
                raise
        raise


def verify_bundle(db, actor, export_id, directory):
    status = export_status(db, actor, export_id)
    rows = db.execute('SELECT filename,digest FROM artifact_files WHERE export_id=?', (export_id,)).fetchall()
    matches = bool(rows)
    root = Path(directory).resolve()
    for row in rows:
        path = root / row['filename']
        # Stored names originate in render_day, but still reject unsafe paths.
        if path.resolve().parent != root or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            matches = False
            continue
        with path.open('rb') as file:
            data = file.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != row['digest']:
            matches = False
    return {**status, 'files_match': matches, 'usable': status['valid'] and matches, 'design_status': 'requires_visual_review'}
