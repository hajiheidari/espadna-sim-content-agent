"""Durable private delivery queue. Ambiguous network results are never retried."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo
from .content import _payload
from .core import audit, require
from .packs import private_path
from .raster import render_day, verify_bundle

LEASE_SECONDS = 300
MAX_ATTEMPTS = 5
MODES = ('dry_run', 'telegram')


@dataclass(frozen=True)
class Media:
    filename: str
    data: bytes
    caption: str


@dataclass(frozen=True)
class Bundle:
    recipient: int
    key: str
    media: tuple[Media, ...]


@dataclass(frozen=True)
class Receipt:
    message_ids: tuple[int, ...]


class DefiniteRetry(Exception):
    """Transport has a definite rejection before delivery, e.g. HTTP 429."""
    def __init__(self, delay=60):
        self.delay = max(1, min(int(delay), 86400))


class DefiniteRejection(Exception):
    """Transport definitively rejected the request; operator action is needed."""


class DeliveryUnknown(Exception):
    """Request may have arrived. Quarantine instead of risking duplicate sends."""


def utc_now():
    return datetime.now(timezone.utc)


def epoch(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('an aware datetime is required')
    return int(value.timestamp())


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


def set_route(db, actor, recipient, enabled=True):
    if type(recipient) is not int or recipient <= 0 or type(enabled) is not bool:
        raise ValueError('private numeric Telegram user ID required')
    with transaction(db):
        require(db, actor, {'owner'})
        if enabled:
            require(db, recipient, {'owner', 'admin', 'viewer'})
        elif db.execute('SELECT 1 FROM users WHERE telegram_id=?', (recipient,)).fetchone() is None:
            raise ValueError('unknown recipient')
        db.execute('INSERT INTO delivery_routes(recipient,enabled,created_by) VALUES(?,?,?) ON CONFLICT(recipient) DO UPDATE SET enabled=excluded.enabled', (recipient, int(enabled), actor))
        audit(db, actor, 'delivery_route', str(recipient), after={'enabled': enabled})


def _recipient_enabled(db, recipient):
    return db.execute('SELECT 1 FROM delivery_routes r JOIN users u ON u.telegram_id=r.recipient WHERE r.recipient=? AND r.enabled=1 AND u.enabled=1', (recipient,)).fetchone() is not None


def enqueue_plan(db, actor, plan_id, mode='dry_run', now=None):
    if mode not in MODES:
        raise ValueError('unknown delivery mode')
    now = now or utc_now()
    epoch(now)
    zone = ZoneInfo('Asia/Tehran')
    today = now.astimezone(zone).date()
    counts = {'created': 0, 'updated': 0, 'unchanged': 0}
    with transaction(db):
        require(db, actor, {'owner', 'admin'})
        if mode == 'telegram':
            from .members import daily_globally_enabled
            if not daily_globally_enabled(db):
                raise PermissionError('daily employee delivery is awaiting owner activation')
        payload, plan, _ = _payload(db, actor, plan_id)
        if mode == 'telegram':
            recipients = db.execute('''SELECT r.recipient FROM delivery_routes r
                JOIN users u ON u.telegram_id=r.recipient
                JOIN membership_requests m ON m.telegram_id=r.recipient
                JOIN employee_settings s ON s.recipient=r.recipient
                WHERE r.enabled=1 AND u.enabled=1 AND u.role='viewer' AND m.status='approved' AND s.daily_enabled=1
                ORDER BY r.recipient''').fetchall()
        else:
            recipients = db.execute('SELECT r.recipient FROM delivery_routes r JOIN users u ON u.telegram_id=r.recipient WHERE r.enabled=1 AND u.enabled=1 ORDER BY r.recipient').fetchall()
        if not recipients:
            raise ValueError('owner must configure an active private delivery route first')
        days = sorted({item['scheduled_at'][:10] for item in payload['items']})
        for day in days:
            day_date = date.fromisoformat(day)
            if day_date < today:
                continue  # No surprise historical catch-up deliveries.
            due = epoch(datetime.combine(day_date, time(8), zone))
            expires = epoch(datetime.combine(day_date + timedelta(days=1), time(), zone))
            for recipient in recipients:
                target = recipient['recipient']
                old = db.execute('SELECT * FROM delivery_jobs WHERE plan_id=? AND day=? AND recipient=? AND mode=?', (plan_id, day, target, mode)).fetchone()
                if old is None:
                    job_id = uuid.uuid4().hex
                    db.execute('INSERT INTO delivery_jobs(id,plan_id,plan_revision,day,recipient,mode,due_at,expires_at,next_attempt_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)', (job_id, plan_id, plan['revision'], day, target, mode, due, expires, due, actor))
                    counts['created'] += 1
                elif old['state'] in ('queued', 'retry', 'blocked', 'failed', 'expired', 'cancelled') and (old['plan_revision'] != plan['revision'] or old['state'] in ('blocked', 'failed', 'expired', 'cancelled')):
                    db.execute("UPDATE delivery_jobs SET plan_revision=?,state='queued',attempts=0,next_attempt_at=due_at,lease_token=NULL,lease_until=NULL,export_id=NULL,bundle_path=NULL,error_code=NULL WHERE id=?", (plan['revision'], old['id']))
                    counts['updated'] += 1
                else:
                    counts['unchanged'] += 1
        audit(db, actor, 'delivery_enqueued', str(plan_id), after={**counts, 'mode': mode})
    return counts


def _job(db, job_id):
    row = db.execute('SELECT * FROM delivery_jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        raise ValueError('unknown delivery job')
    return row


def claim_next(db, actor, mode='dry_run', now=None):
    if mode not in MODES:
        raise ValueError('unknown delivery mode')
    stamp = epoch(now or utc_now())
    with transaction(db):
        require(db, actor, {'owner', 'admin'})
        # A lease lost before network dispatch is safe to retry. Once dispatch
        # starts, loss of the worker is ambiguous and must not auto-retry.
        lost = db.execute("SELECT id,lease_token,state FROM delivery_jobs WHERE mode=? AND state IN ('leased','sending') AND lease_until<=?", (mode, stamp)).fetchall()
        for row in lost:
            state = 'retry' if row['state'] == 'leased' else 'uncertain'
            db.execute('UPDATE delivery_jobs SET state=?,next_attempt_at=?,error_code=? WHERE id=?', (state, stamp, 'worker_lost', row['id']))
            db.execute('UPDATE delivery_attempts SET state=?,finished_at=?,error_code=? WHERE token=?', (state, stamp, 'worker_lost', row['lease_token']))
            audit(db, actor, 'delivery_lease_lost', row['id'], after={'state': state})
        expired = db.execute("UPDATE delivery_jobs SET state='expired',error_code='day_expired' WHERE mode=? AND state IN ('queued','retry') AND expires_at<=?", (mode, stamp)).rowcount
        failed = db.execute("UPDATE delivery_jobs SET state='failed',error_code='attempt_limit' WHERE mode=? AND state IN ('queued','retry') AND attempts>=?", (mode, MAX_ATTEMPTS)).rowcount
        if expired or failed:
            audit(db, actor, 'delivery_sweep', mode, after={'expired': expired, 'failed': failed})
        row = db.execute("SELECT * FROM delivery_jobs WHERE mode=? AND state IN ('queued','retry') AND due_at<=? AND next_attempt_at<=? AND expires_at>? ORDER BY due_at,id LIMIT 1", (mode, stamp, stamp, stamp)).fetchone()
        if row is None:
            return None
        token = uuid.uuid4().hex
        db.execute("UPDATE delivery_jobs SET state='leased',attempts=attempts+1,lease_token=?,lease_until=?,error_code=NULL WHERE id=?", (token, stamp + LEASE_SECONDS, row['id']))
        db.execute("INSERT INTO delivery_attempts(token,job_id,actor,state,started_at) VALUES(?,?,?,'leased',?)", (token, row['id'], actor, stamp))
        audit(db, actor, 'delivery_claimed', row['id'], after={'mode': mode})
        return dict(_job(db, row['id']))


def expire_waiting(db, actor, mode, now=None):
    """Expire old queued work without contacting Telegram or claiming a job."""
    if mode not in MODES:
        raise ValueError('unknown delivery mode')
    stamp = epoch(now or utc_now())
    with transaction(db):
        require(db, actor, {'owner', 'admin'})
        count = db.execute("UPDATE delivery_jobs SET state='expired',error_code='day_expired' WHERE mode=? AND state IN ('queued','retry') AND expires_at<=?", (mode, stamp)).rowcount
        if count:
            audit(db, actor, 'delivery_expired', mode, after={'count': count})


def _validate_job(db, actor, job_id, token, stamp):
    row = _job(db, job_id)
    require(db, actor, {'owner'} if row['mode'] == 'telegram' else {'owner', 'admin'})
    if row['state'] != 'leased' or row['lease_token'] != token or row['lease_until'] <= stamp:
        raise ValueError('delivery lease is no longer owned')
    if stamp < row['due_at'] or stamp >= row['expires_at']:
        raise ValueError('delivery is outside its scheduled day')
    if row['mode'] == 'telegram':
        from .members import daily_globally_enabled
        if not daily_globally_enabled(db):
            raise PermissionError('daily employee delivery is disabled')
    if not _recipient_enabled(db, row['recipient']):
        raise PermissionError('recipient route or role is disabled')
    payload, plan, _ = _payload(db, actor, row['plan_id'])
    if plan['revision'] != row['plan_revision']:
        raise ValueError('plan changed; enqueue the reviewed revision again')
    return row, payload


async def deliver_manual(db, actor, plan_id, day, sender, artifact_root, now=None, builder=None, channel=None):
    """Send one explicitly requested day to an approved employee and record the result."""
    from .members import employee_allowed
    if not employee_allowed(db, actor):
        raise PermissionError('approved employee required')
    stamp = epoch(now or utc_now())
    date.fromisoformat(day)
    with transaction(db):
        recent = db.execute("SELECT 1 FROM manual_delivery_requests WHERE recipient=? AND day=? AND state IN ('preparing','sending') AND created_at>?",
                            (actor, day, stamp - 600)).fetchone()
        if recent:
            raise ValueError('the same request is already being prepared')
        payload, plan, _ = _payload(db, actor, plan_id)
        if not any(item['scheduled_at'][:10] == day for item in payload['items']):
            raise ValueError('day is not available in the approved plan')
        request_id = uuid.uuid4().hex
        db.execute("INSERT INTO manual_delivery_requests(id,recipient,plan_id,day,state,created_at) VALUES(?,?,?,?, 'preparing',?)",
                   (request_id, actor, plan_id, day, stamp))
        audit(db, actor, 'manual_delivery_requested', request_id, after={'day': day})
    root = private_path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'manual-{request_id}'
    sending = False
    try:
        result = (builder or render_day)(db, actor, plan_id, day, path, channel=channel)
        if not verify_bundle(db, actor, result['export_id'], path)['usable']:
            raise ValueError('manual delivery artifact is stale or changed')
        payload, _, _ = _payload(db, actor, plan_id)
        items = [item for item in payload['items'] if item['scheduled_at'][:10] == day]
        if len(items) != 6:
            raise ValueError('six current stories required')
        media = []
        for index, item in enumerate(items, 1):
            name = f'story-{index:02}.png'
            data = (path / name).read_bytes()
            expected = db.execute('SELECT digest FROM artifact_files WHERE export_id=? AND filename=?', (result['export_id'], name)).fetchone()
            if expected is None or len(data) > 32 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected['digest']:
                raise ValueError('manual delivery image changed')
            media.append(Media(name, data, item['caption']))
        with transaction(db):
            if not employee_allowed(db, actor):
                raise PermissionError('employee was disabled during preparation')
            db.execute("UPDATE manual_delivery_requests SET state='sending',export_id=? WHERE id=?", (result['export_id'], request_id))
        sending = True
        receipt = await sender.send_bundle(Bundle(actor, request_id, tuple(media)))
        if (not isinstance(receipt, Receipt) or len(receipt.message_ids) != 6 or len(set(receipt.message_ids)) != 6
                or any(type(value) is not int or value <= 0 for value in receipt.message_ids)):
            raise DeliveryUnknown()
        with transaction(db):
            db.execute("UPDATE manual_delivery_requests SET state='sent',finished_at=?,receipt_json=? WHERE id=?",
                       (epoch(utc_now()), json.dumps(list(receipt.message_ids)), request_id))
            audit(db, actor, 'manual_delivery_result', request_id, after={'state': 'sent'})
        return {'id': request_id, 'state': 'sent', 'day': day, 'message_ids': receipt.message_ids}
    except Exception as exc:
        state = 'uncertain' if sending else ('blocked' if isinstance(exc, (ValueError, PermissionError, DefiniteRejection)) else 'failed')
        with transaction(db):
            db.execute('UPDATE manual_delivery_requests SET state=?,finished_at=?,error_code=? WHERE id=?',
                       (state, epoch(utc_now()), 'send_unknown' if sending else 'prepare_failed', request_id))
            audit(db, actor, 'manual_delivery_result', request_id, after={'state': state})
        raise


def _problem(db, actor, job_id, token, stamp, state, code, delay=60):
    with transaction(db):
        row = _job(db, job_id)
        if row['lease_token'] != token or row['state'] not in ('leased', 'sending'):
            return
        if state == 'retry' and (row['attempts'] >= MAX_ATTEMPTS or stamp + delay >= row['expires_at']):
            state = 'failed' if row['attempts'] >= MAX_ATTEMPTS else 'expired'
        db.execute('UPDATE delivery_jobs SET state=?,error_code=?,next_attempt_at=? WHERE id=?', (state, code, stamp + delay, job_id))
        db.execute('UPDATE delivery_attempts SET state=?,finished_at=?,error_code=? WHERE token=?', (state, stamp, code, token))
        audit(db, actor, 'delivery_result', job_id, after={'state': state, 'code': code})


def _complete(db, actor, job_id, token, stamp, receipt):
    encoded = json.dumps(list(receipt.message_ids))
    with transaction(db):
        row = _job(db, job_id)
        db.execute("UPDATE delivery_attempts SET state='confirmed',finished_at=?,receipt_json=? WHERE token=?", (stamp, encoded, token))
        if row['lease_token'] != token or row['state'] not in ('sending', 'uncertain'):
            audit(db, actor, 'delivery_late_receipt', job_id)
            return
        state = 'simulated' if row['mode'] == 'dry_run' else 'sent'
        db.execute('UPDATE delivery_jobs SET state=?,receipt_json=?,error_code=NULL WHERE id=?', (state, encoded, job_id))
        audit(db, actor, 'delivery_result', job_id, after={'state': state})


async def run_once(db, actor, sender, artifact_root, mode='dry_run', allow_network=False, clock=None, builder=None, channel=None):
    """One bounded tick. A timer/process must call this; no background service here."""
    if mode not in MODES or sender.mode != mode:
        raise ValueError('sender mode does not match queue mode')
    if mode == 'telegram':
        require(db, actor, {'owner'})
        if allow_network is not True:
            raise PermissionError('real delivery requires explicit network launch')
    clock = clock or utc_now
    root = private_path(artifact_root)
    job = claim_next(db, actor, mode, clock())
    if job is None:
        return None
    job_id, token = job['id'], job['lease_token']
    sending = False
    try:
        with transaction(db):
            _validate_job(db, actor, job_id, token, epoch(clock()))
        root.mkdir(parents=True, exist_ok=True)
        path = root / f'delivery-{job_id}-{token}'
        result = (builder or render_day)(db, actor, job['plan_id'], job['day'], path, channel=channel)
        with transaction(db):
            row, payload = _validate_job(db, actor, job_id, token, epoch(clock()))
            if not verify_bundle(db, actor, result['export_id'], path)['usable']:
                raise ValueError('delivery artifact is stale or changed')
            export = db.execute('SELECT plan_id,plan_revision FROM content_exports WHERE id=?', (result['export_id'],)).fetchone()
            with (path / 'manifest.json').open('rb') as file:
                manifest_raw = file.read(1024 * 1024 + 1)
            if len(manifest_raw) > 1024 * 1024:
                raise ValueError('delivery manifest is too large')
            manifest = json.loads(manifest_raw)
            if export['plan_id'] != row['plan_id'] or export['plan_revision'] != row['plan_revision'] or manifest['export_id'] != result['export_id'] or manifest['day'] != row['day']:
                raise ValueError('artifact does not match queued plan/day')
            items = [x for x in payload['items'] if x['scheduled_at'][:10] == row['day']]
            if len(items) != 6:
                raise ValueError('six current story captions required')
            media = []
            for index, item in enumerate(items, 1):
                name = f'story-{index:02}.png'
                expected = db.execute('SELECT digest FROM artifact_files WHERE export_id=? AND filename=?', (result['export_id'], name)).fetchone()
                with (path / name).open('rb') as file:
                    data = file.read(32 * 1024 * 1024 + 1)
                if expected is None or len(data) > 32 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected['digest']:
                    raise ValueError('delivery image changed')
                media.append(Media(name, data, item['caption']))
            bundle = Bundle(row['recipient'], job_id, tuple(media))
            stamp = epoch(clock())
            # Recheck clock after hashing; bytes are now immutable for transport.
            _validate_job(db, actor, job_id, token, stamp)
            db.execute("UPDATE delivery_jobs SET state='sending',export_id=?,bundle_path=?,lease_until=? WHERE id=?", (result['export_id'], str(path), stamp + LEASE_SECONDS, job_id))
            db.execute("UPDATE delivery_attempts SET state='sending' WHERE token=?", (token,))
            audit(db, actor, 'delivery_dispatch_started', job_id, after={'mode': mode})
        sending = True
        receipt = await sender.send_bundle(bundle)
        if not isinstance(receipt, Receipt) or len(receipt.message_ids) != 6 or len(set(receipt.message_ids)) != 6 or any(type(x) is not int or x <= 0 for x in receipt.message_ids):
            raise DeliveryUnknown()
        _complete(db, actor, job_id, token, epoch(clock()), receipt)
    except DefiniteRetry as exc:
        _problem(db, actor, job_id, token, epoch(clock()), 'retry', 'rate_limited', exc.delay)
    except DefiniteRejection:
        _problem(db, actor, job_id, token, epoch(clock()), 'blocked', 'recipient_rejected')
    except Exception as exc:
        # Never store exception text, Telegram URLs, tokens, paths or payloads.
        if sending:
            _problem(db, actor, job_id, token, epoch(clock()), 'uncertain', 'send_unknown')
        elif epoch(clock()) >= job['expires_at']:
            _problem(db, actor, job_id, token, epoch(clock()), 'expired', 'day_expired')
        elif isinstance(exc, (ValueError, PermissionError)):
            _problem(db, actor, job_id, token, epoch(clock()), 'blocked', 'preflight_failed')
        else:
            _problem(db, actor, job_id, token, epoch(clock()), 'retry', 'prepare_failed', min(60 * 2 ** (job['attempts'] - 1), 3600))
    return job_status(db, actor, job_id)


def job_status(db, actor, job_id):
    role = require(db, actor, {'owner', 'admin', 'viewer'})
    row = _job(db, job_id)
    if role == 'viewer' and row['recipient'] != actor:
        raise PermissionError('access denied')
    return {key: row[key] for key in ('id', 'plan_id', 'day', 'recipient', 'mode', 'due_at', 'expires_at', 'state', 'attempts', 'next_attempt_at', 'export_id', 'error_code')}


def list_jobs(db, actor):
    role = require(db, actor, {'owner', 'admin', 'viewer'})
    rows = db.execute('SELECT id FROM delivery_jobs' + (' WHERE recipient=?' if role == 'viewer' else '') + ' ORDER BY due_at,id LIMIT 100', (actor,) if role == 'viewer' else ()).fetchall()
    return [job_status(db, actor, row['id']) for row in rows]


def cancel_job(db, actor, job_id):
    with transaction(db):
        require(db, actor, {'owner', 'admin'})
        row = _job(db, job_id)
        if row['state'] in ('sending', 'uncertain', 'sent', 'simulated'):
            raise ValueError('cannot cancel a dispatched or ambiguous delivery')
        db.execute("UPDATE delivery_jobs SET state='cancelled',error_code='operator_cancelled' WHERE id=?", (job_id,))
        audit(db, actor, 'delivery_cancelled', job_id)


def resolve_uncertain(db, actor, job_id, delivered, receipt_ids=(), now=None):
    """Explicit owner reconciliation; never infer that a timeout means failure."""
    stamp = epoch(now or utc_now())
    if type(delivered) is not bool:
        raise ValueError('explicit delivery result required')
    if delivered and (len(receipt_ids) != 6 or len(set(receipt_ids)) != 6 or any(type(x) is not int or x <= 0 for x in receipt_ids)):
        raise ValueError('six confirmed Telegram message IDs required')
    with transaction(db):
        require(db, actor, {'owner'})
        row = _job(db, job_id)
        if row['state'] != 'uncertain':
            raise ValueError('delivery is not uncertain')
        state = ('simulated' if row['mode'] == 'dry_run' else 'sent') if delivered else ('retry' if stamp < row['expires_at'] else 'expired')
        db.execute('UPDATE delivery_jobs SET state=?,receipt_json=?,next_attempt_at=?,lease_token=NULL,error_code=? WHERE id=?', (state, json.dumps(list(receipt_ids)) if delivered else None, stamp, None if delivered else 'owner_confirmed_not_delivered', job_id))
        audit(db, actor, 'delivery_reconciled', job_id, after={'delivered': delivered})
