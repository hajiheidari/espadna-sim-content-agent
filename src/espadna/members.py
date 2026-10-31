"""Owner-reviewed private employee membership and delivery controls."""
from .core import audit, require


def _clean(value, limit):
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def owners(db):
    return [row[0] for row in db.execute("SELECT telegram_id FROM users WHERE role='owner' AND enabled=1 ORDER BY telegram_id")]


def request_membership(db, telegram_id, username=None, first_name=None):
    if type(telegram_id) is not int or telegram_id <= 0:
        raise ValueError('invalid Telegram user')
    user = db.execute('SELECT role,enabled FROM users WHERE telegram_id=?', (telegram_id,)).fetchone()
    current = db.execute('SELECT status FROM membership_requests WHERE telegram_id=?', (telegram_id,)).fetchone()
    if user and user['enabled'] and user['role'] == 'viewer' and current and current['status'] == 'approved':
        return 'approved'
    if current and current['status'] == 'pending':
        return 'pending'
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('''INSERT INTO membership_requests(telegram_id,username,first_name,status)
                      VALUES(?,?,?,'pending') ON CONFLICT(telegram_id) DO UPDATE SET
                      username=excluded.username,first_name=excluded.first_name,status='pending',
                      requested_at=CURRENT_TIMESTAMP,reviewed_at=NULL,reviewed_by=NULL''',
                   (telegram_id, _clean(username, 64), _clean(first_name, 128)))
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise
    return 'created'


def review_membership(db, actor, telegram_id, approve):
    if type(approve) is not bool:
        raise ValueError('explicit decision required')
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'})
        request = db.execute("SELECT * FROM membership_requests WHERE telegram_id=? AND status='pending'", (telegram_id,)).fetchone()
        if request is None:
            raise ValueError('membership request is no longer pending')
        status = 'approved' if approve else 'rejected'
        if approve:
            existing = db.execute('SELECT role FROM users WHERE telegram_id=?', (telegram_id,)).fetchone()
            if existing and existing['role'] in ('owner', 'admin'):
                raise ValueError('administrators cannot be converted to employees')
            db.execute("INSERT INTO users(telegram_id,role,enabled) VALUES(?,'viewer',1) ON CONFLICT(telegram_id) DO UPDATE SET role='viewer',enabled=1", (telegram_id,))
            db.execute('INSERT INTO employee_settings(recipient,daily_enabled) VALUES(?,1) ON CONFLICT(recipient) DO UPDATE SET updated_at=CURRENT_TIMESTAMP', (telegram_id,))
            db.execute('INSERT INTO delivery_routes(recipient,enabled,created_by) VALUES(?,1,?) ON CONFLICT(recipient) DO UPDATE SET enabled=1', (telegram_id, actor))
        db.execute('UPDATE membership_requests SET status=?,reviewed_at=CURRENT_TIMESTAMP,reviewed_by=? WHERE telegram_id=?', (status, actor, telegram_id))
        audit(db, actor, 'membership_review', str(telegram_id), after={'status': status})
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise
    return status


def add_employee(db, actor, telegram_id):
    request_membership(db, telegram_id)
    return review_membership(db, actor, telegram_id, True)


def set_employee_state(db, actor, telegram_id, state):
    if state not in ('approved', 'disabled', 'removed'):
        raise ValueError('invalid employee state')
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'})
        row = db.execute('SELECT role FROM users WHERE telegram_id=?', (telegram_id,)).fetchone()
        if row is None or row['role'] != 'viewer':
            raise ValueError('unknown employee')
        enabled = state == 'approved'
        db.execute('UPDATE users SET enabled=? WHERE telegram_id=?', (int(enabled), telegram_id))
        daily = db.execute('SELECT daily_enabled FROM employee_settings WHERE recipient=?', (telegram_id,)).fetchone()
        route_enabled = int(enabled and daily is not None and daily['daily_enabled'])
        db.execute('UPDATE delivery_routes SET enabled=? WHERE recipient=?', (route_enabled, telegram_id))
        db.execute('''INSERT INTO membership_requests(telegram_id,status,reviewed_at,reviewed_by)
                      VALUES(?,?,CURRENT_TIMESTAMP,?) ON CONFLICT(telegram_id) DO UPDATE SET
                      status=excluded.status,reviewed_at=CURRENT_TIMESTAMP,reviewed_by=excluded.reviewed_by''',
                   (telegram_id, state, actor))
        audit(db, actor, 'employee_state', str(telegram_id), after={'state': state})
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def set_employee_daily(db, actor, telegram_id, enabled):
    if type(enabled) is not bool:
        raise ValueError('boolean required')
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'})
        require(db, telegram_id, {'viewer'})
        membership = db.execute("SELECT status FROM membership_requests WHERE telegram_id=?", (telegram_id,)).fetchone()
        if membership is None or membership['status'] != 'approved':
            raise ValueError('employee is not active')
        db.execute('INSERT INTO employee_settings(recipient,daily_enabled) VALUES(?,?) ON CONFLICT(recipient) DO UPDATE SET daily_enabled=excluded.daily_enabled,updated_at=CURRENT_TIMESTAMP', (telegram_id, int(enabled)))
        db.execute('INSERT INTO delivery_routes(recipient,enabled,created_by) VALUES(?,?,?) ON CONFLICT(recipient) DO UPDATE SET enabled=excluded.enabled', (telegram_id, int(enabled), actor))
        audit(db, actor, 'employee_daily', str(telegram_id), after={'enabled': enabled})
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def employee_allowed(db, telegram_id):
    row = db.execute('''SELECT 1 FROM users u JOIN membership_requests m ON m.telegram_id=u.telegram_id
                        WHERE u.telegram_id=? AND u.role='viewer' AND u.enabled=1 AND m.status='approved' ''', (telegram_id,)).fetchone()
    return row is not None


def list_employees(db, actor):
    require(db, actor, {'owner'})
    return [dict(row) for row in db.execute('''SELECT m.telegram_id,m.username,m.first_name,m.status,
                 COALESCE(s.daily_enabled,0) daily_enabled
                 FROM membership_requests m LEFT JOIN employee_settings s ON s.recipient=m.telegram_id
                 ORDER BY m.requested_at,m.telegram_id''')]


def daily_globally_enabled(db):
    row = db.execute("SELECT value FROM app_settings WHERE key='daily_employee_delivery'").fetchone()
    return row is not None and row['value'] == '1'


def set_daily_global(db, actor, enabled):
    if type(enabled) is not bool:
        raise ValueError('boolean required')
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner'})
        db.execute("UPDATE app_settings SET value=? WHERE key='daily_employee_delivery'", ('1' if enabled else '0',))
        audit(db, actor, 'daily_global', 'employee_delivery', after={'enabled': enabled})
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise
