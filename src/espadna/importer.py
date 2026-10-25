"""Validate a complete workbook before an atomic, additive upsert."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, BadZipFile
import openpyxl
from .core import DIGITS, OPERATORS, audit, phone_text, require

MAX_BYTES = 10 * 1024 * 1024
MAX_UNPACKED = 50 * 1024 * 1024
MAX_ROWS = 20000
HEADERS = {'شماره دلخواه': 'phone', 'نوع سیم کارت': 'sim_type', 'وضعیت سیم کارت': 'sim_status',
           'وضعیت سیم': 'sim_status', 'قیمت': 'price_internal', 'اپراتور': 'operator'}


@dataclass(frozen=True)
class Record:
    phone: str
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
    records, errors, total, duplicates = {}, [], 0, 0
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
                        errors.append(f'sheet {sheet_index}: duplicate header')
                    columns[field] = i
            if not {'phone', 'sim_type', 'sim_status', 'operator', 'price_internal'} <= columns.keys():
                errors.append(f'sheet {sheet_index}: missing required headers')
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
                    record = Record(phone_text(v['phone']), code(v['sim_type'], (1, 2)), code(v['sim_status'], (1, 2, 3)), operator, price(v['price_internal']))
                    if record.phone in records:
                        if records[record.phone] != record:
                            raise ValueError('conflicting duplicate')
                        duplicates += 1
                    records[record.phone] = record
                except (ValueError, IndexError):
                    # Do not leak phone, price, sheet title or raw values in diagnostics.
                    errors.append(f'sheet {sheet_index}, row {row_index}: invalid or conflicting inventory data')
        if errors:
            raise ValueError('; '.join(errors[:30]))
        if not records:
            raise ValueError('empty workbook')
        return list(records.values()), {'rows': total, 'unique': len(records), 'duplicates': duplicates, 'sheets': len(wb.sheetnames)}
    finally:
        wb.close()
        stream.close()


def import_workbook(db, actor, path, dry_run=False):
    require(db, actor, {'owner', 'admin'})
    # One bounded in-memory snapshot for parsing and hashing; no inventory copy on disk.
    raw = _read_bytes(path)
    records, stats = _parse_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner', 'admin'})
        counts = {'inserted': 0, 'updated': 0, 'unchanged': 0}
        changes = []
        for record in records:
            new = vars(record)
            old = db.execute('SELECT phone,sim_type,sim_status,operator,price_internal FROM inventory WHERE phone=?', (record.phone,)).fetchone()
            before = dict(old) if old else None
            action = 'inserted' if old is None else ('unchanged' if before == new else 'updated')
            counts[action] += 1
            if action != 'unchanged':
                changes.append((record, before, new))
        if not dry_run:
            batch = db.execute('INSERT INTO import_batches(actor,digest,inserted,updated,unchanged) VALUES(?,?,?,?,?)', (actor, digest, counts['inserted'], counts['updated'], counts['unchanged'])).lastrowid
            for record, before, new in changes:
                db.execute('INSERT INTO inventory(phone,sim_type,sim_status,operator,price_internal) VALUES(?,?,?,?,?) ON CONFLICT(phone) DO UPDATE SET sim_type=excluded.sim_type,sim_status=excluded.sim_status,operator=excluded.operator,price_internal=excluded.price_internal,revision=inventory.revision+1', tuple(vars(record).values()))
                audit(db, actor, 'import', record.phone, before, new, batch)
        db.execute('ROLLBACK' if dry_run else 'COMMIT')
        return {**stats, **counts, 'dry_run': dry_run}
    except Exception:
        db.execute('ROLLBACK')
        raise
