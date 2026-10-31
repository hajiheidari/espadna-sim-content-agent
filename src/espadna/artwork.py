"""Offline native text + decorative raster assets; never render raw DB rows."""
import base64
from functools import lru_cache
from html import escape
from pathlib import Path
from .core import OPERATORS, STATUSES, TYPES, phone_values
from .templates import template_by_id

ASSETS = Path(__file__).with_name('assets')
DESIGN_VERSION = 'reference-2026-10-25-v1'
DESIGN_STATUS = 'development_accepted'

PATHS = {
    'diamond': 'M3 8 8 2h8l5 6-9 14L3 8Zm0 0h18M8 2l4 20 4-20',
    'heart': 'M12 21C4 16 1 10 4 5c3-3 6-2 8 1 2-3 5-4 8-1 3 5 0 11-8 16Z',
    'check': 'm5 12 4 4L20 5',
    'user': 'M17 7a5 5 0 1 1-10 0 5 5 0 0 1 10 0ZM3 22v-3a9 9 0 0 1 18 0v3H3Z',
    'phone': 'M6 2 2 6c0 8 8 16 16 16l4-4-6-4-3 3-6-6 3-3-4-6Z',
    'message': 'M22 11a10 10 0 0 1-10 10H3l1-5A10 10 0 1 1 22 11ZM7 10h1m3 0h1m3 0h1',
    'document': 'M5 2h10l4 4v16H5V2Zm10 0v5h4M8 11h8m-8 4h8m-8 4h5',
    'crown': 'm2 5 5 5 5-8 5 8 5-5-3 15H5L2 5ZM5 16h14',
    'chart': 'M2 22h20M5 18v-5h4v5m3 0V8h4v10m3 0V3h3v15M3 8l6-4 5 1 7-4',
    'headset': 'M3 13v-2a9 9 0 0 1 18 0v2M3 12h4v8H3v-8Zm14 0h4v8h-4v-8Zm4 6c0 5-4 5-9 5',
    'globe': 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM2 12h20M12 2c-6 6-6 14 0 20 6-6 6-14 0-20Z',
    'pin': 'M20 9c0 6-8 13-8 13S4 15 4 9a8 8 0 1 1 16 0ZM15 9a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
    'send': 'M22 2 2 10l8 3 3 9 9-20ZM10 13 22 2',
    'camera': 'M2 5h20v16H2V5Zm4 0 2-3h8l2 3M17 13a5 5 0 1 1-10 0 5 5 0 0 1 10 0Z',
}


def icon(name, css=''):
    return f'<svg class="icon {css}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{PATHS[name]}"/></svg>'


def monogram():
    # A clean geometric monogram redrawn as editable vector paths, not a crop.
    return '<svg class="monogram" viewBox="0 0 100 120" aria-hidden="true"><defs><linearGradient id="mark" x2="1" y2="1"><stop stop-color="#fff2b3"/><stop offset=".5" stop-color="#e0b54c"/><stop offset="1" stop-color="#926225"/></linearGradient></defs><path fill="url(#mark)" d="M53 4 9 37v28l42-31V18L26 37v13l42-31V4L24 37v12L53 27Zm-6 112 44-33V55L49 86v16l25-19V70L32 101v15l44-33V71L47 93Z"/></svg>'


@lru_cache(maxsize=10)
def asset_url(filename):
    if filename not in {'base.png', 'city.png', 'gift.png', 'watch.png', 'tree.png', 'interior.png', 'Vazirmatn-Regular.ttf', 'Vazirmatn-Bold.ttf'}:
        raise ValueError('unknown bundled asset')
    mime = 'font/ttf' if filename.endswith('.ttf') else 'image/png'
    return 'data:' + mime + ';base64,' + base64.b64encode((ASSETS / filename).read_bytes()).decode('ascii')


def clean_content(content):
    if content['sim_type'] not in TYPES.values() or content['sim_status'] not in STATUSES.values():
        raise ValueError('invalid public content labels')
    _, display = phone_values(content['phone'])
    result = {'phone': display, 'sim_type': content['sim_type'], 'sim_status': content['sim_status']}
    operator = content.get('operator')
    if isinstance(operator, str) and operator in OPERATORS:
        result['operator'] = operator
    return result


def number_card(content, label=None):
    phone = content['phone']
    badge = f'<span class="choice">{escape(label)}</span>' if label else ''
    return f'<div class="number-card">{badge}<div class="phone" dir="ltr">{escape(phone)}</div></div>'


def _points(points, icons=('diamond', 'heart', 'user', 'chart'), steps=False):
    return '<div class="rows">' + ''.join(f'<div class="info-row"><span class="round">{n+1 if steps else icon(icons[n % len(icons)])}</span><span>{escape(text)}</span></div>' for n, text in enumerate(points)) + '</div>'


def _device(content, map_view=False):
    if map_view:
        screen = '<div class="map-lines"></div>' + icon('pin', 'map-pin') + '<div class="map-caption">در ارتباط بمانید</div>'
    else:
        screen = '<div class="call-avatar">' + icon('user') + '</div><div class="call-name">اسپادنا سیم</div><div class="call-label">تماس با یک انتخاب تازه</div>' + number_card(content) + '<div class="call-actions"><span class="decline">' + icon('phone') + '</span><span class="accept">' + icon('phone') + '</span></div><div class="call-action-labels"><span>رد تماس</span><span>پاسخ</span></div>'
    return '<div class="device"><div class="device-notch"></div><div class="device-screen">' + screen + '</div></div>'


def story_markup(item, peers=None):
    content = clean_content(item['content'])
    position = item['position']
    if type(position) is not int or not 1 <= position <= 6:
        raise ValueError('invalid story position')
    template = template_by_id(item.get('template_id', 1))
    peers = [clean_content(x['content']) for x in (peers or [item])]
    unique = {content['phone']: content}
    unique.update({x['phone']: x for x in peers})
    choices = list(unique.values())
    if template.layout in ('compare', 'numbers') and len(choices) < (2 if template.layout == 'compare' else 3):
        raise ValueError('comparison/list requires distinct verified inventory peers')
    title = ''.join(f'<span>{escape(line)}</span>' for line in template.title)
    body = ''
    kind = template.layout
    if kind in ('phone', 'map'):
        body = _device(content, kind == 'map')
    elif kind in ('compare', 'numbers'):
        count = 2 if kind == 'compare' else 3
        body = '<div class="number-stack">' + ''.join(number_card(x, chr(65+n) if kind == 'compare' else None) for n, x in enumerate(choices[:count])) + '</div>'
    elif kind in ('list', 'checks', 'steps', 'info'):
        icons = ('check',) if kind == 'checks' else ('document', 'user', 'check', 'message') if template.id == 5 else ('message', 'document', 'phone', 'headset') if kind == 'info' else ('diamond', 'heart', 'user', 'chart')
        body = _points(template.points, icons, kind == 'steps')
    elif kind == 'grid':
        body = '<div class="feature-grid">' + ''.join(f'<div class="feature">{icon(name)}<span>{escape(text)}</span></div>' for name, text in zip(('crown', 'heart', 'diamond', 'user'), template.points)) + '</div>'
    elif kind == 'chart':
        body = '<div class="gold-bars"><i></i><i></i><i></i><i></i><i></i><span class="rising-line"></span></div>' + _points(template.points, ('check',))
    elif kind == 'operators':
        body = '<div class="operator-options">' + ''.join(f'<div class="operator-option">{icon("globe")}<span>{name}</span></div>' for name in ('همراه اول', 'ایرانسل', 'رایتل')) + '</div><p class="operator-note">گزینه‌های عمومی اپراتور؛ مشخصات هر شماره فقط از موجودی تأیید می‌شود.</p>' + _points(template.points, ('check',))
    elif kind == 'social':
        body = '<div class="social-orbit">' + ''.join(f'<span class="social social-{name}">{icon(name)}</span>' for name in ('message', 'phone', 'send', 'camera')) + '</div><p class="social-note">برای استعلام موجودی و پرسش‌های خرید<br>از راه ارتباطی همین صفحه پیام بدهید.</p>'
    elif kind == 'question':
        body = '<div class="gold-question">؟</div><p class="explanation">' + '<br>'.join(escape(x) for x in template.points) + '</p>'
    elif kind == 'crown':
        body = '<div class="hero-crown">' + icon('crown') + '</div>' + _points(template.points, ('check',))
    elif kind == 'tree':
        body = '<div class="tree-spacer"></div>' + _points(template.points, ('check',))
    elif kind == 'gift':
        body = '<div class="gift-tag">یک انتخاب<br><strong>به‌یادماندنی</strong></div>'
    elif kind == 'brand':
        body = '<div class="brand-hero">' + monogram() + '<strong>اسپادنا سیم</strong><small>ESPADNA SIM</small></div>'
    else:
        body = '<div class="hero-caption">انتخابی برای سلیقهٔ شما</div>'
    # Inventory labels occur only on layouts that display this specific number.
    labels = ''
    if kind in ('phone', 'compare', 'numbers'):
        labels = '<div class="inventory-labels">' + escape(content['sim_type'] + ' · ' + content['sim_status']) + (f'<span>{escape(content["operator"])}</span>' if 'operator' in content else '') + '</div>' if kind == 'phone' else ''
    footer = '<footer class="brand-footer">' + monogram() + '<div><strong>اسپادنا سیم</strong><small>ESPADNA.SIM</small></div></footer>'
    return f'<section class="tile"><article class="story bg-{template.scene} layout-{kind}" data-template="{template.id}" aria-label="قالب {template.id}"><div class="shade"></div><div class="review-index">{template.id}</div><h2>{title}</h2><div class="composition">{body}</div>{labels}<div class="cta">{icon("message")}<span>{escape(template.cta)}</span></div>{footer}</article></section>'


def stylesheet(scenes):
    fonts = ''.join(f"@font-face{{font-family:Vazirmatn;src:url('{asset_url('Vazirmatn-' + name + '.ttf')}') format('truetype');font-weight:{weight};font-display:block}}" for name, weight in [('Regular', 400), ('Bold', 700)])
    # Chromium limits custom-property values to 1 MiB; put large embedded images
    # directly in background-image so offline photo plates are not dropped.
    backgrounds = ''.join(f".bg-{name}{{background-image:url('{asset_url(name+'.png')}')}}" for name in sorted(set(scenes)))
    return fonts + backgrounds + (ASSETS / 'story.css').read_text(encoding='utf-8')


def document(items, title='مجموعهٔ قالب‌های اسپادنا سیم', review=True, pack=None):
    if pack is not None:
        return custom_document(items, pack, title, review)
    # Each item can use only its same-day peers supplied by the caller.
    scenes = [template_by_id(x.get('template_id', 1)).scene for x in items]
    cards = ''.join(story_markup(item, [x for x in items if x.get('day') == item.get('day')]) for item in items)
    header = f'<header><h1>{escape(title)}</h1><p>۳۰ ترکیب مستقل بر اساس مرجع · شماره‌ها از دادهٔ ورودی معتبر · بدون قیمت · نسخهٔ طراحی {DESIGN_VERSION}</p><p>این مجموعه برای بازبینی بصری است؛ تأیید موجودی به معنی تأیید طراحی یا مجوز انتشار نیست.</p></header>' if review else ''
    csp = "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; script-src 'none'; base-uri 'none'; form-action 'none'"
    return '<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="' + csp + '"><title>' + escape(title) + '</title><style>' + stylesheet(scenes) + '</style></head><body' + (' class="single"' if not review else '') + '>' + header + '<main>' + cards + '</main></body></html>'


def custom_document(items, pack, title, review):
    row, manifest, images = pack
    templates = manifest['templates']
    backgrounds = {}
    selected = {}
    cards = []
    for item in items:
        source = template_by_id(item['template_id'])
        template = templates[(source.id - 1) % len(templates)]
        selected[template['id']] = template
        content = clean_content(item['content'])
        name = template['image']
        if name not in backgrounds:
            mime = 'image/jpeg' if name.endswith(('.jpg', '.jpeg')) else 'image/webp' if name.endswith('.webp') else 'image/png'
            backgrounds[name] = 'data:' + mime + ';base64,' + base64.b64encode(images[name]).decode('ascii')
        values = {**content, 'title': '\n'.join(source.title), 'cta': source.cta, 'brand': 'اسپادنا سیم'}
        phone = content['phone']
        values['phone'] = phone
        layers = []
        for layer in template['layers']:
            field = layer['field']
            value = layer['text'] if field == 'text' else values.get(field)
            if not value:
                continue  # An unknown operator stays absent, never guessed.
            phone_class = ' phone' if field == 'phone' else ''
            style = ';'.join(f'{key}:{layer[key]}px' for key in ('width', 'height', 'font_size')).replace('font_size', 'font-size')
            style += f";left:{layer['x']}px;top:{layer['y']}px;color:{layer['color']};text-align:{layer['align']}"
            text = escape(value).replace('\n', '<br>')
            direction = ' dir="ltr"' if field == 'phone' else ''
            layers.append(f'<div class="pack-layer{phone_class}" data-field="{field}"{direction} style="{style}">{text}</div>')
        cls = 'pack-bg-' + str(template['id'])
        cards.append(f'<section class="tile"><article class="story layout-custom {cls}" data-design-kind="custom" data-template="{source.id}" data-pack-template="{template["id"]}"><div class="review-index">{source.id}</div>{"".join(layers)}</article></section>')
    grouped = {}
    for template in selected.values():
        grouped.setdefault((template['image'], template['fit']), []).append(template['id'])
    image_styles = ''.join(','.join(f'.pack-bg-{index}' for index in ids) + f"{{background-image:url('{backgrounds[name]}');background-size:{fit};background-repeat:no-repeat;background-color:#070a0f}}" for (name, fit), ids in grouped.items())
    layer_styles = '.pack-layer{position:absolute;overflow:hidden;direction:rtl;line-height:1.45;font-weight:700;white-space:normal}.pack-layer.phone{direction:ltr;unicode-bidi:isolate;white-space:pre;line-height:1.3}'
    header = f'<header><h1>{escape(title)}</h1><p>پک شخصی: {escape(row["name"])} · نسخهٔ {escape(row["version"])} · تصاویر خصوصی و متن‌های موجودی مستقل</p></header>' if review else ''
    csp = "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; script-src 'none'; base-uri 'none'; form-action 'none'"
    return '<!doctype html><html lang="fa"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="' + csp + '"><title>' + escape(title) + '</title><style>' + stylesheet([]) + image_styles + layer_styles + '</style></head><body' + (' class="single"' if not review else '') + '>' + header + '<main>' + ''.join(cards) + '</main></body></html>'
