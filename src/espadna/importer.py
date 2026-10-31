"""Validate a complete workbook before an atomic, additive upsert."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import uuid
from zipfile import ZipFile, BadZipFile
import openpyxl
from .core import DIGITS, OPERATORS, audit, phone_values, require

MAX_BYTES = 10 * 1024 * 1024
MAX_UNPACKED = 50 * 1024 * 1024
MAX_ROWS = 20000
HEADERS = {'شماره دلخواه': 'phone', 'نوع سیم کارت': 'sim_type', 'وضعیت سیم کارت': 'sim_status',
           'وضعیت سیم': 'sim_status', 'قیمت': 'price_internal', 'اپراتور': 'operator'}


@dataclass(frozen=True)
class Record:
    phone_normalized: str
    phone_display: str
    sim_type: int
    sim_status: int
    operator: str | None
    price_internal: str | None


def normalize(value):
    return ' '.join(str(value or '').replace('ي', 'ی').replace('ك', 'ک').replace('\u200c', ' ').split())


def code(value, allowed):
    if isinstance(value, bool):
        raise ValueError('invalid enum')
    value = str(value).translate(DIGITS).strip()
    if value not in {str(x) for x in allowed}:
        raise ValueError('invalid enum')
    return int(value)


def price(value):
    if value is None or value == '':
        return None
    try:
        p = Decimal(str(value).translate(DIGITS))
        if not p.is_finite() or p < 0:
            raise ValueError('invalid internal price')
        # Bound exponent expansion before formatting untrusted decimal strings.
        # Decimal.normalize() uses the active precision and can round prices.
        if p.adjusted() > 23 or p.as_tuple().exponent < -6:
            raise ValueError('internal price exceeds precision limits')
        text = format(p, 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
        return '0' if p == 0 else text
    except InvalidOperation:
        raise ValueError('invalid internal price') from None


def parse_workbook(path):
    return _parse_bytes(_read_bytes(path))


def _read_bytes(path):
    # A bounded read avoids trusting a separate stat when a file changes.
    with Path(path).open('rb') as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('workbook too large')
    return raw


def _parse_bytes(raw):
    try:
        with ZipFile(BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED or len(z.infolist()) > 2000:
                raise ValueError('workbook archive limits exceeded')
    except BadZipFile:
        raise ValueError('invalid xlsx') from None
    # An in-memory stream also prevents lazy worksheet iterators from retaining
    # an OS file handle on validation failure (notably on Windows).
    stream = BytesIO(raw)
    wb = openpyxl.load_workbook(stream, read_only=True, data_only=False, keep_links=False)
    records, locations, errors, duplicate_rows, total, duplicates = {}, {}, [], [], 0, 0
    try:
        for sheet_index, sheet in enumerate(wb, 1):
            if sheet.max_column is None or sheet.max_row is None or sheet.max_column > 100 or sheet.max_row > MAX_ROWS + 1:
                raise ValueError('worksheet dimensions exceed limits')
            rows = sheet.iter_rows()
            header = next(rows, ())
            columns = {}
            for i, cell in enumerate(header):
                field = HEADERS.get(normalize(cell.value))
                if field:
                    if field in columns:
                        errors.append(f'sheet {sheet.title!r}: duplicate header')
                    columns[field] = i
            if not {'phone', 'sim_type', 'sim_status', 'operator', 'price_internal'} <= columns.keys():
                errors.append(f'sheet {sheet.title!r}: missing required headers')
                continue
            for row_index, row in enumerate(rows, 2):
                if not any(row[i].value is not None for i in columns.values()):
                    continue
                total += 1
                if total > MAX_ROWS:
                    raise ValueError('row limit exceeded')
                try:
                    cells = {field: row[i] for field, i in columns.items()}
                    if any(c.data_type in ('f', 'e') for c in cells.values()):
                        raise ValueError('formula/error in inventory fields')
                    v = {k: c.value for k, c in cells.items()}
                    operator = normalize(v['operator']) or None
                    if operator is not None and operator not in OPERATORS:
                        raise ValueError('unverified operator')
                    normalized, display = phone_values(v['phone'])
                    record = Record(normalized, display, code(v['sim_type'], (1, 2)), code(v['sim_status'], (1, 2, 3)), operator, price(v['price_internal']))
                    if record.phone_normalized in records:
                        if records[record.phone_normalized] != record:
                            first_sheet, first_row = locations[record.phone_normalized]
                            raise ValueError(f'conflicting duplicate; first at sheet {first_sheet!r}, row {first_row}')
                        duplicates += 1
                        duplicate_rows.append({'sheet': sheet.title, 'row': row_index})
                    records[record.phone_normalized] = record
                    locations.setdefault(record.phone_normalized, (sheet.title, row_index))
                except (ValueError, IndexError) as exc:
                    # Do not leak phone, price, sheet title or raw values in diagnostics.
                    detail = str(exc) if str(exc).startswith('conflicting duplicate;') else 'invalid inventory data'
                    errors.append(f'sheet {sheet.title!r}, row {row_index}: {detail}')
        if errors:
            raise ValueError('; '.join(errors[:30]))
        if not records:
            raise ValueError('empty workbook')
        return list(records.values()), {'rows': total, 'unique': len(records), 'duplicates': duplicates,
                                        'duplicate_rows': duplicate_rows, 'sheets': len(wb.sheetnames)}
    finally:
        wb.close()
        stream.close()


def _inventory_preview(db, records, stats, mode):
    if mode not in ('merge', 'full'):
        raise ValueError('invalid inventory sync mode')
    incoming = {record.phone_normalized: record for record in records}
    existing = {row['phone_normalized']: row for row in db.execute(
        'SELECT phone_normalized,phone_display,sim_type,sim_status,operator,price_internal,sale_status,active FROM inventory')}
    counts = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'removed': 0}
    for phone, record in incoming.items():
        old = existing.get(phone)
        if old is None:
            counts['inserted'] += 1
            continue
        fields = vars(record)
        changed = not old['active'] or any(old[key] != value for key, value in fields.items())
        counts['updated' if changed else 'unchanged'] += 1
    if mode == 'full':
        counts['removed'] = sum(row['active'] for phone, row in existing.items() if phone not in incoming)
    return {**stats, **counts, 'mode': mode}


def preview_workbook(db, actor, path, mode='merge'):
    require(db, actor, {'owner'} if mode == 'full' else {'owner', 'admin'})
    raw = _read_bytes(path)
    records, stats = _parse_bytes(raw)
    return _inventory_preview(db, records, stats, mode)


def _backup_database(db, backup_dir):
    source = Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve()
    if not source.is_file():
        raise ValueError('database backup requires a file database')
    root = Path(backup_dir).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any((parent / '.git').exists() for parent in root.parents):
        raise ValueError('database backup must be outside Git')
    target = root / f'espadna-before-full-sync-{uuid.uuid4().hex}.db'
    destination = sqlite3.connect(target)
    try:
        db.backup(destination)
    finally:
        destination.close()
    os.chmod(target, 0o600)
    return target


def _apply_records(db, actor, records, stats, mode, expected=None, backup_dir=None):
    require(db, actor, {'owner'} if mode == 'full' else {'owner', 'admin'})
    stats = dict(stats)
    digest = stats.pop('_digest', None)
    report = _inventory_preview(db, records, stats, mode)
    if expected is not None and report != expected:
        raise ValueError('inventory changed after preview; create a new preview')
    backup = _backup_database(db, backup_dir) if mode == 'full' else None
    incoming = {record.phone_normalized: record for record in records}
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'} if mode == 'full' else {'owner', 'admin'})
        # Recheck under the write lock so confirmation always applies exactly the previewed change set.
        locked_report = _inventory_preview(db, records, stats, mode)
        if locked_report != report:
            raise ValueError('inventory changed after preview; create a new preview')
        batch = db.execute('INSERT INTO import_batches(actor,digest,inserted,updated,unchanged,mode,removed) VALUES(?,?,?,?,?,?,?)',
                           (actor, digest or '', report['inserted'], report['updated'], report['unchanged'], mode, report['removed'])).lastrowid
        for record in records:
            old = db.execute('SELECT phone_normalized,phone_display,sim_type,sim_status,operator,price_internal,sale_status,active FROM inventory WHERE phone_normalized=?',
                             (record.phone_normalized,)).fetchone()
            values = tuple(vars(record).values())
            if old is None:
                db.execute('INSERT INTO inventory(phone_normalized,phone_display,sim_type,sim_status,operator,price_internal,active) VALUES(?,?,?,?,?,?,1)', values)
                audit(db, actor, 'import', record.phone_normalized, None, {**vars(record), 'active': 1}, batch)
            elif not old['active'] or any(old[key] != value for key, value in vars(record).items()):
                before = dict(old)
                db.execute('''UPDATE inventory SET phone_display=?,sim_type=?,sim_status=?,operator=?,price_internal=?,active=1,revision=revision+1
                              WHERE phone_normalized=?''', (record.phone_display, record.sim_type, record.sim_status, record.operator,
                                                           record.price_internal, record.phone_normalized))
                after = {**vars(record), 'sale_status': old['sale_status'], 'active': 1}
                audit(db, actor, 'import', record.phone_normalized, before, after, batch)
        if mode == 'full':
            for old in db.execute('SELECT phone_normalized,sale_status,active FROM inventory WHERE active=1').fetchall():
                if old['phone_normalized'] not in incoming:
                    db.execute('UPDATE inventory SET active=0,revision=revision+1 WHERE phone_normalized=?', (old['phone_normalized'],))
                    audit(db, actor, 'sync_deactivate', old['phone_normalized'], dict(old),
                          {'sale_status': old['sale_status'], 'active': 0}, batch)
        for record in records:
            stored = db.execute('SELECT phone_display,sale_status,active FROM inventory WHERE phone_normalized=?', (record.phone_normalized,)).fetchone()
            if stored is None or stored['phone_display'] != record.phone_display or not stored['active']:
                raise ValueError('inventory sync verification failed')
        db.execute('COMMIT')
        return {**report, 'dry_run': False, 'backup': str(backup) if backup else None}
    except Exception:
        db.execute('ROLLBACK')
        raise


def import_workbook(db, actor, path, dry_run=False):
    require(db, actor, {'owner', 'admin'})
    # One bounded in-memory snapshot for parsing and hashing; no inventory copy on disk.
    raw = _read_bytes(path)
    records, stats = _parse_bytes(raw)
    stats['_digest'] = hashlib.sha256(raw).hexdigest()
    report = _inventory_preview(db, records, {k: v for k, v in stats.items() if k != '_digest'}, 'merge')
    if dry_run:
        return {**report, 'dry_run': True}
    return _apply_records(db, actor, records, stats, 'merge')


def save_sync_upload(db, actor, path):
    require(db, actor, {'owner', 'admin'})
    raw = _read_bytes(path)
    _parse_bytes(raw)  # Reject before retaining the private workbook.
    preview_id = uuid.uuid4().hex
    db.execute('INSERT INTO inventory_sync_previews(id,actor,workbook,digest) VALUES(?,?,?,?)',
               (preview_id, actor, raw, hashlib.sha256(raw).hexdigest()))
    return preview_id


def prepare_saved_sync(db, actor, preview_id, mode):
    require(db, actor, {'owner'} if mode == 'full' else {'owner', 'admin'})
    row = db.execute("SELECT * FROM inventory_sync_previews WHERE id=? AND actor=? AND state IN ('uploaded','previewed')", (preview_id, actor)).fetchone()
    if row is None:
        raise ValueError('unknown or expired inventory upload')
    records, stats = _parse_bytes(row['workbook'])
    report = _inventory_preview(db, records, stats, mode)
    db.execute("UPDATE inventory_sync_previews SET mode=?,preview_json=?,state='previewed' WHERE id=?",
               (mode, json.dumps(report, ensure_ascii=False, sort_keys=True), preview_id))
    return report


def apply_saved_sync(db, actor, preview_id, backup_dir):
    row = db.execute("SELECT * FROM inventory_sync_previews WHERE id=? AND actor=? AND state='previewed'", (preview_id, actor)).fetchone()
    if row is None:
        raise ValueError('unknown or unconfirmed inventory preview')
    mode = row['mode']
    require(db, actor, {'owner'} if mode == 'full' else {'owner', 'admin'})
    records, stats = _parse_bytes(row['workbook'])
    stats['_digest'] = row['digest']
    expected = json.loads(row['preview_json'])
    result = _apply_records(db, actor, records, stats, mode, expected=expected,
                            backup_dir=backup_dir if mode == 'full' else None)
    db.execute("UPDATE inventory_sync_previews SET workbook=x'',state='applied' WHERE id=?", (preview_id,))
    return result


def cancel_saved_sync(db, actor, preview_id):
    require(db, actor, {'owner', 'admin'})
    changed = db.execute("UPDATE inventory_sync_previews SET workbook=x'',state='cancelled' WHERE id=? AND actor=? AND state IN ('uploaded','previewed')",
                         (preview_id, actor)).rowcount
    if not changed:
        raise ValueError('unknown inventory upload')
