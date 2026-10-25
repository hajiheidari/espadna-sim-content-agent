"""Local monthly preparation. No Telegram posting or automatic approval."""
import calendar
from datetime import date, datetime, time
import re
from zoneinfo import ZoneInfo
from .core import audit, caption, public_item, require
from .packs import default_pack, design_ready, pack_row


def month_days(month):
    if not isinstance(month, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}', month):
        raise ValueError('month must be Gregorian YYYY-MM')
    year, number = map(int, month.split('-'))
    if not 2000 <= year <= 2100 or not 1 <= number <= 12:
        raise ValueError('month outside supported range')
    return [date(year, number, n) for n in range(1, calendar.monthrange(year, number)[1] + 1)]


def _plan(db, plan_id):
    plan = db.execute('SELECT * FROM content_plans WHERE id=?', (plan_id,)).fetchone()
    if plan is None:
        raise ValueError('unknown plan')
    return plan


def _slots(db, plan_id):
    return db.execute('''SELECT s.*, i.sim_type,i.sim_status,i.operator,i.sale_status,
                         i.revision AS current_revision
                         FROM content_slots s JOIN inventory i ON i.phone=s.phone
                         WHERE s.plan_id=? ORDER BY s.day,s.position''', (plan_id,)).fetchall()


def _stale(row):
    return row['sale_status'] != 'available' or row['inventory_revision'] != row['current_revision']


def create_plan(db, actor, month):
    days = month_days(month)
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner', 'admin'})
        if db.execute('SELECT 1 FROM content_plans WHERE month=?', (month,)).fetchone():
            raise ValueError('month already planned; refresh its existing plan')
        available = db.execute("SELECT phone,revision FROM inventory WHERE sale_status='available' ORDER BY phone").fetchall()
        if len(available) < 6:
            raise ValueError('at least six available numbers required')
        pack_id, pack_revision = default_pack(db)
        if pack_id and pack_row(db, pack_id)['state'] != 'approved':
            raise ValueError('default design pack is unavailable; select another pack')
        plan_id = db.execute('INSERT INTO content_plans(month,created_by,design_pack_id,design_pack_revision) VALUES(?,?,?,?)', (month, actor, pack_id, pack_revision)).lastrowid
        for day_index, day in enumerate(days):
            for position in range(1, 7):
                row = available[(day_index * 6 + position - 1) % len(available)]
                db.execute('INSERT INTO content_slots(plan_id,day,position,template_id,phone,inventory_revision) VALUES(?,?,?,?,?,?)',
                           (plan_id, day.isoformat(), position, (day_index % 5) * 6 + position, row['phone'], row['revision']))
        audit(db, actor, 'plan_created', str(plan_id), after={'month': month, 'slots': len(days) * 6})
        db.execute('COMMIT')
        return plan_id
    except Exception:
        db.execute('ROLLBACK')
        raise


def inspect_plan(db, actor, plan_id):
    require(db, actor, {'owner', 'admin', 'viewer'})
    plan = _plan(db, plan_id)
    rows = _slots(db, plan_id)
    stale = sum(_stale(row) for row in rows)
    return {'id': plan['id'], 'month': plan['month'], 'state': plan['state'],
            'slots': len(rows), 'stale_slots': stale, 'design_ready': design_ready(db, plan), 'design_pack_id': plan['design_pack_id'],
            'ready': plan['state'] == 'approved' and stale == 0 and design_ready(db, plan)}


def refresh_plan(db, actor, plan_id):
    """Replace unavailable numbers, update revisions, and require reapproval."""
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner', 'admin'})
        plan = _plan(db, plan_id)
        pack_revision = None
        if plan['design_pack_id']:
            pack = pack_row(db, plan['design_pack_id'])
            if pack['state'] != 'approved':
                raise ValueError('design pack is unavailable; select another pack')
            pack_revision = pack['revision']
        rows = _slots(db, plan_id)
        available = db.execute("SELECT phone,revision FROM inventory WHERE sale_status='available' ORDER BY phone").fetchall()
        if len(available) < 6:
            raise ValueError('at least six available numbers required')
        used = {}
        # Retain all available assignments first so replacements cannot duplicate
        # a later position within the same day's six stories.
        for row in rows:
            if row['sale_status'] == 'available':
                used.setdefault(row['day'], set()).add(row['phone'])
        changed = 0
        for row in rows:
            if not _stale(row):
                continue
            day_used = used.setdefault(row['day'], set())
            replacement = row if row['sale_status'] == 'available' else next(x for x in available if x['phone'] not in day_used)
            revision = row['current_revision'] if replacement is row else replacement['revision']
            db.execute('UPDATE content_slots SET phone=?,inventory_revision=? WHERE id=?', (replacement['phone'], revision, row['id']))
            day_used.add(replacement['phone'])
            changed += 1
        db.execute("UPDATE content_plans SET state='draft', approved_by=NULL,revision=revision+1,design_pack_revision=? WHERE id=?", (pack_revision, plan_id))
        audit(db, actor, 'plan_refreshed', str(plan_id), after={'changed_slots': changed})
        db.execute('COMMIT')
        return changed
    except Exception:
        db.execute('ROLLBACK')
        raise


def approve_plan(db, actor, plan_id):
    db.execute('BEGIN IMMEDIATE')
    try:
        require(db, actor, {'owner', 'admin'})
        plan = _plan(db, plan_id)
        if not design_ready(db, plan):
            raise ValueError('design pack is unavailable or stale; refresh/select a pack')
        if any(_stale(row) for row in _slots(db, plan_id)):
            raise ValueError('stale plan must be refreshed before approval')
        db.execute("UPDATE content_plans SET state='approved',approved_by=? WHERE id=?", (actor, plan_id))
        audit(db, actor, 'plan_approved', str(plan_id))
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise


def approved_payload(db, actor, plan_id):
    """Read a consistent public-safe snapshot, rechecking live state on each call.

    This prepares a local payload, never sends it. A future sender MUST call this
    again immediately before sending and track delivery/idempotency separately.
    """
    db.execute('BEGIN')
    try:
        payload, _, _ = _payload(db, actor, plan_id)
        db.execute('COMMIT')
        return payload
    except Exception:
        db.execute('ROLLBACK')
        raise


def _payload(db, actor, plan_id):
    require(db, actor, {'owner', 'admin', 'viewer'})
    plan = _plan(db, plan_id)
    rows = _slots(db, plan_id)
    if plan['state'] != 'approved' or not design_ready(db, plan) or any(_stale(row) for row in rows):
        raise ValueError('plan is unapproved or stale')
    zone = ZoneInfo('Asia/Tehran')
    items = []
    for row in rows:
        scheduled = datetime.combine(date.fromisoformat(row['day']), time(8), zone)
        items.append({'slot_id': row['id'], 'template_id': row['template_id'], 'position': row['position'],
                      'scheduled_at': scheduled.isoformat(), 'content': public_item(row), 'caption': caption(row)})
    return {'plan_id': plan_id, 'month': plan['month'], 'timezone': 'Asia/Tehran', 'items': items}, plan, rows


def create_export(db, actor, plan_id, output, pending=False):
    """Track a private export and its inventory revisions. Never overwrite files."""
    from pathlib import Path
    import hashlib
    import json
    import uuid
    path = Path(output).resolve()
    if any((parent / '.git').exists() for parent in path.parents):
        raise ValueError('export must be outside a Git checkout')
    created = False
    db.execute('BEGIN IMMEDIATE')
    try:
        payload, plan, rows = _payload(db, actor, plan_id)
        export_id = uuid.uuid4().hex
        payload['export_id'] = export_id
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        digest = hashlib.sha256(data).hexdigest()
        pack_id = plan['design_pack_id']
        pack_digest = pack_row(db, pack_id)['digest'] if pack_id else None
        db.execute('INSERT INTO content_exports(id,actor,plan_id,plan_revision,digest,state,design_pack_id,design_pack_revision,design_digest) VALUES(?,?,?,?,?,?,?,?,?)', (export_id, actor, plan_id, plan['revision'], digest, 'pending' if pending else 'ready', pack_id, plan['design_pack_revision'], pack_digest))
        snapshots = {(row['phone'], row['inventory_revision']) for row in rows}
        db.executemany('INSERT INTO export_items VALUES(?,?,?)', [(export_id, phone, revision) for phone, revision in snapshots])
        audit(db, actor, 'content_exported', export_id, after={'plan_id': plan_id, 'digest': digest})
        # File creation happens last; rollback removes a partial new output.
        with path.open('xb') as file:
            created = True
            file.write(data)
        db.execute('COMMIT')
        return export_id
    except Exception:
        db.execute('ROLLBACK')
        if created:
            path.unlink(missing_ok=True)
        raise


def export_status(db, actor, export_id):
    role = require(db, actor, {'owner', 'admin', 'viewer'})
    export = db.execute('''SELECT e.*,p.revision AS current_plan_revision,p.state AS plan_state
                            FROM content_exports e JOIN content_plans p ON p.id=e.plan_id
                            WHERE e.id=?''', (export_id,)).fetchone()
    if export is None:
        raise ValueError('unknown export')
    if role == 'viewer' and export['actor'] != actor:
        received = db.execute("SELECT 1 FROM delivery_jobs WHERE export_id=? AND recipient=? AND state='sent'", (export_id, actor)).fetchone()
        if received is None:
            raise PermissionError('access denied')
    changed = db.execute('''SELECT count(*) FROM export_items e JOIN inventory i ON i.phone=e.phone
                            WHERE e.export_id=? AND (i.sale_status!='available' OR i.revision!=e.inventory_revision)''', (export_id,)).fetchone()[0]
    current = changed == 0 and export['plan_revision'] == export['current_plan_revision'] and export['plan_state'] == 'approved'
    design_current = True
    if export['design_pack_id']:
        pack = pack_row(db, export['design_pack_id'])
        design_current = pack['state'] == 'approved' and pack['revision'] == export['design_pack_revision'] and pack['digest'] == export['design_digest']
    return {'export_id': export_id, 'plan_id': export['plan_id'], 'valid': current and design_current and export['state'] == 'ready', 'inventory_current': current, 'design_current': design_current, 'design_pack_id': export['design_pack_id'], 'state': export['state'], 'changed_numbers': changed, 'digest': export['digest']}


def invalid_exports(db, actor):
    role = require(db, actor, {'owner', 'admin', 'viewer'})
    ids = db.execute("SELECT id FROM content_exports WHERE actor=? OR id IN (SELECT export_id FROM delivery_jobs WHERE recipient=? AND state='sent')" if role == 'viewer' else 'SELECT id FROM content_exports', (actor, actor) if role == 'viewer' else ()).fetchall()
    return [status for row in ids if not (status := export_status(db, actor, row['id']))['valid']]
