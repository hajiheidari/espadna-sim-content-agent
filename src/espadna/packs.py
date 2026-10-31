"""Versioned user artwork in private storage, never executable templates in Git."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import stat
import uuid
import warnings
from zipfile import BadZipFile, ZipFile
from .core import audit, require

MAX_IMAGE = 12 * 1024 * 1024
MAX_PACK = 120 * 1024 * 1024
MAX_MANIFEST = 128 * 1024
FIELDS = {'phone', 'sim_type', 'sim_status', 'operator', 'title', 'cta', 'brand', 'text'}
GOLD = '#ffe69a'


def private_path(path):
    path = Path(path).resolve()
    if any((parent / '.git').exists() for parent in (path, *path.parents)):
        raise ValueError('user design storage/output must be outside a Git checkout')
    return path


def _read(path, limit):
    with Path(path).open('rb') as file:
        raw = file.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('design file exceeds size limit')
    return raw


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 and c != '\n' for c in value):
        raise ValueError('invalid design text')
    return value.strip()


def _int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('invalid layer dimensions')
    return value


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'name', 'version', 'templates'} or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise ValueError('unsupported pack manifest')
    templates = manifest['templates']
    if not isinstance(templates, list) or not 1 <= len(templates) <= 30:
        raise ValueError('pack must contain 1 to 30 templates')
    result = {'schema_version': 1, 'name': _text(manifest['name'], 80), 'version': _text(manifest['version'], 40), 'templates': []}
    for index, template in enumerate(templates, 1):
        if not isinstance(template, dict) or not {'id', 'image', 'mode', 'layers'} <= template.keys() or set(template) - {'id', 'image', 'mode', 'layers', 'fit'}:
            raise ValueError('invalid template specification')
        if type(template['id']) is not int or template['id'] != index:
            raise ValueError('template IDs must be consecutive starting at 1')
        filename = template['image']
        if not isinstance(filename, str) or not 1 <= len(filename) <= 120 or re.search(r'[/\\:\x00-\x1f]', filename) or filename.endswith((' ', '.')) or Path(filename).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            raise ValueError('image must use a flat local PNG/JPEG/WebP filename')
        mode, layers, fit = template['mode'], template['layers'], template.get('fit', 'cover')
        if mode not in ('overlay', 'static') or fit not in ('cover', 'contain') or not isinstance(layers, list) or len(layers) > 10:
            raise ValueError('invalid template mode, fit or layers')
        normalized = []
        for layer in layers:
            allowed = {'field', 'x', 'y', 'width', 'height', 'font_size', 'color', 'align', 'text'}
            required = {'field', 'x', 'y', 'width', 'height', 'font_size'}
            if not isinstance(layer, dict) or not required <= layer.keys() or set(layer) - allowed or not isinstance(layer['field'], str) or layer['field'] not in FIELDS:
                raise ValueError('invalid layer field; prices and arbitrary code are forbidden')
            x, y = _int(layer['x'], 0, 1079), _int(layer['y'], 0, 1919)
            width, height = _int(layer['width'], 20, 1080), _int(layer['height'], 20, 1920)
            if x + width > 1080 or y + height > 1920:
                raise ValueError('layer extends outside 1080x1920 canvas')
            color, align = layer.get('color', GOLD if layer['field'] == 'phone' else '#ffffff'), layer.get('align', 'center')
            if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color) or align not in ('left', 'center', 'right'):
                raise ValueError('invalid layer color/alignment')
            if layer['field'] == 'phone' and color.lower() != GOLD:
                raise ValueError('entire phone number must remain gold')
            item = {'field': layer['field'], 'x': x, 'y': y, 'width': width, 'height': height,
                    'font_size': _int(layer['font_size'], 16, 180), 'color': color.lower(), 'align': align}
            if layer['field'] == 'text':
                item['text'] = _text(layer.get('text'), 200)
            elif 'text' in layer:
                raise ValueError('dynamic fields cannot contain fixed text')
            normalized.append(item)
        if mode == 'static' and normalized:
            raise ValueError('static image must not contain overlay layers')
        if mode == 'overlay' and sum(layer['field'] == 'phone' for layer in normalized) != 1:
            raise ValueError('overlay template requires exactly one dynamic phone layer')
        result['templates'].append({'id': index, 'image': filename, 'mode': mode, 'fit': fit, 'layers': normalized})
    return result


def _load_source(source):
    source = Path(source)
    if source.is_dir():
        root = source.resolve()
        manifest_path = root / 'pack.json'
        if manifest_path.is_symlink() or manifest_path.resolve().parent != root:
            raise ValueError('unsafe manifest path')
        raw = _read(manifest_path, MAX_MANIFEST)
        def read_image(name):
            path = root / name
            if path.is_symlink() or path.resolve().parent != root:
                raise ValueError('unsafe image path')
            return _read(path, MAX_IMAGE)
        manifest = validate_manifest(json.loads(raw))
        images, size = {}, 0
        for template in manifest['templates']:
            name = template['image']
            if name not in images:
                images[name] = read_image(name)
                size += len(images[name])
                if size > MAX_PACK:
                    raise ValueError('pack images exceed size limit')
    else:
        raw = _read(source, MAX_PACK)
        try:
            with ZipFile(BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > 31 or sum(x.file_size for x in infos) > MAX_PACK or len({x.filename for x in infos}) != len(infos):
                    raise ValueError('pack archive limits exceeded')
                if any('/' in x.filename or '\\' in x.filename or x.flag_bits & 1 or stat.S_ISLNK(x.external_attr >> 16) for x in infos):
                    raise ValueError('pack archive must contain flat regular files')
                info = archive.getinfo('pack.json')
                if info.file_size > MAX_MANIFEST:
                    raise ValueError('manifest too large')
                manifest = validate_manifest(json.loads(archive.read(info)))
                expected = {'pack.json'} | {t['image'] for t in manifest['templates']}
                if {x.filename for x in infos} != expected:
                    raise ValueError('archive contains missing/unexpected files')
                images = {}
                for name in expected - {'pack.json'}:
                    if archive.getinfo(name).file_size > MAX_IMAGE:
                        raise ValueError('image too large')
                    images[name] = archive.read(name)
        except (BadZipFile, KeyError):
            raise ValueError('invalid pack archive') from None
    if sum(map(len, images.values())) > MAX_PACK:
        raise ValueError('pack images exceed size limit')
    return manifest, images


def _validate_image(raw, filename):
    from PIL import Image, UnidentifiedImageError
    expected = 'PNG' if filename.lower().endswith('.png') else 'WEBP' if filename.lower().endswith('.webp') else 'JPEG'
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format != expected or getattr(image, 'n_frames', 1) != 1 or not 320 <= image.width <= 8192 or not 320 <= image.height <= 8192 or image.width * image.height > 20_000_000:
                    raise ValueError('unsupported image format, dimensions or animation')
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
                if image.getexif().get(274, 1) != 1:
                    raise ValueError('export image with normalized orientation first')
    except (UnidentifiedImageError, Image.DecompressionBombWarning, Image.DecompressionBombError, OSError):
        raise ValueError('invalid or unsafe pack image') from None


def import_pack(db, actor, source, storage):
    require(db, actor, {'owner'})
    root = private_path(storage)
    manifest, images = _load_source(source)
    image_hashes = {}
    for name, raw in images.items():
        _validate_image(raw, name)
        image_hashes[name] = hashlib.sha256(raw).hexdigest()
    # Saved filenames are content-addressed; source names never become paths.
    saved = {name: 'image-' + image_hashes[name] + Path(name).suffix.lower() for name in images}
    for template in manifest['templates']:
        template['image'] = saved[template['image']]
    manifest['files'] = {saved[name]: image_hashes[name] for name in images}
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    created_files, destination = [], None
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'})
        old = db.execute('SELECT id,digest FROM design_packs WHERE name=? AND version=?', (manifest['name'], manifest['version'])).fetchone()
        if old:
            if old['digest'] != digest:
                raise ValueError('pack version already exists with different content; use a new version')
            db.execute('COMMIT')
            return old['id']
        root.mkdir(parents=True, exist_ok=True)
        pack_id = uuid.uuid4().hex
        destination = root / pack_id
        destination.mkdir()
        for name, raw in images.items():
            path = destination / saved[name]
            if path in created_files:
                continue
            with path.open('xb') as file:
                created_files.append(path)
                file.write(raw)
        db.execute('INSERT INTO design_packs(id,name,version,digest,manifest_json,storage_path,created_by) VALUES(?,?,?,?,?,?,?)',
                   (pack_id, manifest['name'], manifest['version'], digest, canonical, str(destination), actor))
        audit(db, actor, 'design_pack_imported', pack_id, after={'digest': digest, 'templates': len(manifest['templates'])})
        db.execute('COMMIT')
        return pack_id
    except Exception:
        if db.in_transaction:
            db.execute('ROLLBACK')
        for path in created_files:
            path.unlink(missing_ok=True)
        if destination:
            try:
                destination.rmdir()
            except OSError:
                pass
        raise


def pack_row(db, pack_id):
    row = db.execute('SELECT * FROM design_packs WHERE id=?', (pack_id,)).fetchone()
    if row is None:
        raise ValueError('unknown design pack')
    return row


def load_pack(db, pack_id):
    row = pack_row(db, pack_id)
    if row['state'] != 'approved':
        raise ValueError('design pack is not approved for use')
    canonical = row['manifest_json']
    if hashlib.sha256(canonical.encode('utf-8')).hexdigest() != row['digest']:
        raise ValueError('pack manifest changed')
    manifest = json.loads(canonical)
    root = private_path(row['storage_path'])
    images = {}
    for name, expected in manifest['files'].items():
        if not re.fullmatch(r'image-[0-9a-f]{64}\.(png|jpg|jpeg|webp)', name):
            raise ValueError('invalid stored asset name')
        path = root / name
        if path.is_symlink() or path.resolve().parent != root:
            raise ValueError('unsafe stored asset path')
        raw = _read(path, MAX_IMAGE)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('pack image changed; import a new version')
        images[name] = raw
    return row, manifest, images


def approve_pack(db, actor, pack_id, reviewed_no_prices=False):
    """The owner reviews raster pixels too: byte checks cannot detect baked prices."""
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'})
        row = pack_row(db, pack_id)
        if reviewed_no_prices is not True:
            raise ValueError('owner review of images, prices and fixed inventory text is required')
        # Temporarily set state inside the transaction to validate unchanged bytes.
        db.execute("UPDATE design_packs SET state='approved' WHERE id=?", (pack_id,))
        load_pack(db, pack_id)
        if row['state'] != 'approved':
            db.execute('UPDATE design_packs SET revision=revision+1,reviewed_by=? WHERE id=?', (actor, pack_id))
            audit(db, actor, 'design_pack_reviewed', pack_id, after={'digest': row['digest'], 'no_prices_or_fixed_inventory_text': True})
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def disable_pack(db, actor, pack_id):
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'})
        row = pack_row(db, pack_id)
        if row['state'] != 'disabled':
            db.execute("UPDATE design_packs SET state='disabled',revision=revision+1 WHERE id=?", (pack_id,))
            audit(db, actor, 'design_pack_disabled', pack_id)
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def default_pack(db):
    pack_id = db.execute('SELECT pack_id FROM design_preferences WHERE id=1').fetchone()[0]
    return (pack_id, pack_row(db, pack_id)['revision']) if pack_id else (None, None)


def design_ready(db, plan):
    if plan['design_pack_id'] is None:
        return True
    row = pack_row(db, plan['design_pack_id'])
    return row['state'] == 'approved' and row['revision'] == plan['design_pack_revision']


def set_default_pack(db, actor, pack_id):
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'})
        if pack_id:
            load_pack(db, pack_id)
        db.execute('UPDATE design_preferences SET pack_id=? WHERE id=1', (pack_id,))
        audit(db, actor, 'default_design_pack', pack_id or 'builtin')
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def select_plan_pack(db, actor, plan_id, pack_id):
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner', 'admin'})
        plan = db.execute('SELECT * FROM content_plans WHERE id=?', (plan_id,)).fetchone()
        if plan is None:
            raise ValueError('unknown plan')
        row = load_pack(db, pack_id)[0] if pack_id else None
        revision = row['revision'] if row else None
        if (plan['design_pack_id'], plan['design_pack_revision']) != (pack_id, revision):
            db.execute("UPDATE content_plans SET design_pack_id=?,design_pack_revision=?,revision=revision+1,state='draft',approved_by=NULL WHERE id=?", (pack_id, revision, plan_id))
            audit(db, actor, 'plan_design_pack', str(plan_id), after={'pack_id': pack_id, 'revision': revision})
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def list_packs(db, actor):
    role = require(db, actor, {'owner', 'admin', 'viewer'})
    rows = db.execute('SELECT id,name,version,state,revision FROM design_packs' + ('' if role == 'owner' else " WHERE state='approved'") + ' ORDER BY created_at,id').fetchall()
    return [dict(row) for row in rows]
