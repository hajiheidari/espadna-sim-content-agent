"""Editable HTML proof sheet. Draft composition, never an approved image asset."""
from html import escape
from .content import approved_payload
from .core import OPERATORS, STATUSES, TYPES, phone_text

STYLE = '''
*{box-sizing:border-box}body{margin:0;background:#090d16;color:#e8e5df;font-family:Tahoma,Arial,sans-serif}
header{max-width:858px;margin:32px auto 24px;padding:0 12px;direction:rtl}
h1{font-size:24px;font-weight:500;margin:0 0 12px}header p{font-size:13px;line-height:1.9;color:#b1b8c6}
main{width:858px;margin:0 auto 40px;display:grid;grid-template-columns:repeat(3,270px);gap:24px}
.tile{width:270px;height:480px;overflow:hidden;position:relative;box-shadow:0 12px 40px #0006}
.story{position:relative;width:1080px;height:1920px;padding:144px 100px;transform:scale(.25);transform-origin:top left;overflow:hidden;
background:radial-gradient(ellipse at 100% 10%,#24334a 0,transparent 55%),linear-gradient(160deg,#101b2c,#070b12 72%)}
.story:before{content:"";position:absolute;inset:65px;border:2px solid #bba16a66;pointer-events:none}
.orb{position:absolute;width:900px;height:900px;left:360px;top:880px;border:2px solid #c8aa6944;border-radius:50%;box-shadow:0 0 0 85px #c8aa6905,0 0 0 170px #c8aa6904}
.brand{font-family:Arial,sans-serif;font-size:34px;letter-spacing:10px;color:#d9c395;position:relative}
.retailer{margin-top:24px;font-size:29px;color:#a7b0bf;direction:rtl;text-align:left}
.counter{position:absolute;right:100px;top:144px;font-size:26px;color:#8e9bb0}
.body{position:relative;margin-top:420px;direction:rtl;text-align:center}
.eyebrow{font-size:29px;color:#bba775;margin-bottom:44px}
h2{font-size:58px;font-weight:400;line-height:1.65;margin:0 0 64px}
.phone{direction:ltr;unicode-bidi:isolate;font-family:Arial,sans-serif;font-variant-numeric:tabular-nums;
font-size:91px;font-weight:500;white-space:nowrap;letter-spacing:2px;color:#e3c57d;line-height:1.3}
.rule{height:2px;width:160px;background:#b89a59;margin:64px auto 52px}
.details{font-size:35px;line-height:1.9;color:#d0d5dd}
.operator{font-size:30px;color:#a4afbf;margin-top:16px}
.footer{position:absolute;bottom:156px;left:100px;right:100px;text-align:center;direction:rtl;font-size:30px;color:#c8ad75}
.draft{position:absolute;bottom:90px;left:100px;right:100px;text-align:center;direction:rtl;font-size:22px;color:#8e9bb0}
@media(max-width:880px){main{width:564px;grid-template-columns:repeat(2,270px)}header{max-width:564px}}
@media print{header{display:none}main{display:block;width:1080px}.tile{width:1080px;height:1920px;break-after:page;box-shadow:none}.story{transform:none}}
'''


def story_markup(item):
    # Never interpolate free-form data into art: reconstruct validated fields.
    content = item['content']
    phone = phone_text(content['phone'])
    if content['sim_type'] not in TYPES.values() or content['sim_status'] not in STATUSES.values():
        raise ValueError('invalid public content labels')
    position = item['position']
    if type(position) is not int or not 1 <= position <= 6:
        raise ValueError('invalid story position')
    spaced = f'{phone[:4]} {phone[4:7]} {phone[7:]}'
    operator = content.get('operator')
    operator_html = f'<div class="operator">{escape(operator)}</div>' if operator in OPERATORS else ''
    return f'''<section class="tile"><article class="story" aria-label="استوری {position}">
<div class="orb"></div><div class="brand">ESPADNA SIM</div><div class="retailer">اسپادنا سیم</div>
<div class="counter">0{position} / 06</div><div class="body"><div class="eyebrow">انتخاب شماره</div>
<h2>شمارهٔ بعدی شما</h2><div class="phone">{spaced}</div><div class="rule"></div>
<div class="details">{escape(content['sim_type'])} · {escape(content['sim_status'])}</div>{operator_html}</div>
<div class="footer">برای استعلام موجودی پیام بدهید</div><div class="draft">طرح آزمایشی — نیازمند تأیید بصری</div>
</article></section>'''


def preview_day(db, actor, plan_id, day):
    from datetime import date
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('day must be ISO YYYY-MM-DD')
    payload = approved_payload(db, actor, plan_id)
    items = [item for item in payload['items'] if item['scheduled_at'][:10] == day]
    if len(items) != 6:
        raise ValueError('day not present in plan')
    return '<!doctype html><html lang="fa"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>پیش‌نمایش اسپادنا سیم</title><style>' + STYLE + '</style><header><h1>پیش‌نمایش شش استوری روزانه</h1><p>' + escape(day) + ' · این چیدمان صرفاً آزمایشی است؛ تطبیق با ۳۰ قالب مرجع هنوز انجام نشده است. هر استوری ۱۰۸۰ × ۱۹۲۰ و متن‌ها قابل ویرایش‌اند.</p></header><main>' + ''.join(story_markup(item) for item in items) + '</main></html>'
