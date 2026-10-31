"""Reference-based editable HTML proofs from live approved inventory."""
from .artwork import document, story_markup
from .content import _payload


def preview_day(db, actor, plan_id, day):
    from datetime import date
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('day must be ISO YYYY-MM-DD')
    from .packs import load_pack
    db.execute('BEGIN')
    try:
        payload, plan, _ = _payload(db, actor, plan_id)
        items = [{**item, 'day': day} for item in payload['items'] if item['scheduled_at'][:10] == day]
        if len(items) != 6:
            raise ValueError('day not present in plan')
        pack = load_pack(db, plan['design_pack_id']) if plan['design_pack_id'] else None
        html = document(items, title=f'شش استوری روزانه — {day}', pack=pack)
        db.execute('COMMIT')
        return html
    except Exception:
        db.execute('ROLLBACK')
        raise


def reference_gallery():
    """Synthetic design review only. Never load inventory/reference phone numbers."""
    return document(gallery_items(), title='بازسازی ۳۰ قالب اسپادنا سیم — شماره‌های کاملاً آزمایشی')


def gallery_items():
    """Clearly synthetic inputs shared by design proofs and browser QA."""
    from .core import TYPES, STATUSES
    items = [{'template_id': n, 'position': (n-1) % 6 + 1, 'day': f'demo-{(n-1)//6}',
              'content': {'phone': f'090000000{n-1:02}', 'sim_type': TYPES[1 if n % 2 else 2],
                          'sim_status': STATUSES[(n-1) % 3 + 1]}}
             for n in range(1, 31)]
    return items
