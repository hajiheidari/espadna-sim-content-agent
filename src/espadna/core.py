import json
import re
import sqlite3
from pathlib import Path

TYPES = {1: 'دائمی', 2: 'اعتباری'}
STATUSES = {1: 'صفر به‌نام', 2: 'صفر', 3: 'به‌نام کارکرده'}
OPERATORS = {'همراه اول', 'ایرانسل', 'رایتل'}
DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def phone_text(value):
    if not isinstance(value, str):
        raise ValueError('phone must be text; numeric cells are ambiguous')
    value = re.sub(r'\s+', '', value.translate(DIGITS))
    if not re.fullmatch(r'09[0-9]{9}', value):
        raise ValueError('invalid phone format')
    return value


def connect(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1):
        raise ValueError('unsupported schema version')
    db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
    return db


def require(db, actor, roles):
    row = db.execute('SELECT role FROM users WHERE telegram_id=?', (actor,)).fetchone()
    if row is None or row['role'] not in roles:
        raise PermissionError('access denied')
    return row['role']


def audit(db, actor, action, subject, before=None, after=None, batch=None):
    db.execute('INSERT INTO audit(actor,action,subject,before_json,after_json,batch_id) VALUES(?,?,?,?,?,?)',
               (actor, action, subject, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), batch))


def bootstrap_owner(db, actor):
    if type(actor) is not int or actor <= 0:
        raise ValueError('positive Telegram ID required')
    db.execute('BEGIN IMMEDIATE')
    try:
        if db.execute('SELECT 1 FROM users').fetchone():
            raise ValueError('owner already initialized')
        db.execute("INSERT INTO users VALUES(?,'owner')", (actor,))
        audit(db, actor, 'bootstrap', str(actor), after={'role': 'owner'})
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def set_role(db, actor, target, role):
    if type(target) is not int or target <= 0 or role not in ('owner', 'admin', 'viewer'):
        raise ValueError('invalid role or ID')
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'})
        old = db.execute('SELECT role FROM users WHERE telegram_id=?', (target,)).fetchone()
        if old and old['role'] == 'owner' and role != 'owner' and db.execute("SELECT count(*) FROM users WHERE role='owner'").fetchone()[0] == 1:
            raise ValueError('cannot demote last owner')
        db.execute('INSERT INTO users VALUES(?,?) ON CONFLICT(telegram_id) DO UPDATE SET role=excluded.role', (target, role))
        audit(db, actor, 'role', str(target), dict(old) if old else None, {'role': role})
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def set_sale_status(db, actor, phone, status):
    phone = phone_text(phone)
    if status not in ('available', 'reserved', 'sold'):
        raise ValueError('invalid sale status')
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner', 'admin'})
        old = db.execute('SELECT sale_status FROM inventory WHERE phone=?', (phone,)).fetchone()
        if old is None:
            raise ValueError('unknown inventory item')
        if old['sale_status'] != status:
            db.execute('UPDATE inventory SET sale_status=?, revision=revision+1 WHERE phone=?', (status, phone))
            audit(db, actor, 'sale_status', phone, dict(old), {'sale_status': status})
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def public_item(row):
    """Sole DTO for images, captions and channel content. Never serialize DB rows."""
    if row['sale_status'] != 'available':
        raise ValueError('item is unavailable for public content')
    result = {'phone': phone_text(row['phone']), 'sim_type': TYPES[row['sim_type']], 'sim_status': STATUSES[row['sim_status']]}
    if row['operator'] in OPERATORS:
        result['operator'] = row['operator']
    return result


def caption(item):
    # Rebuild an allowlisted DTO even when the caller supplies additional fields.
    clean = public_item(item)
    return '\n'.join(clean.values())
