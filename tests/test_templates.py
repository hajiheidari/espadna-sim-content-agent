from html.parser import HTMLParser
import unittest
from espadna.artwork import clean_content, story_markup
from espadna.preview import reference_gallery
from espadna.templates import TEMPLATES, template_by_id


class Nodes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.templates = []
        self.external = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-template' in attrs:
            self.templates.append(int(attrs['data-template']))
        for name in ('src', 'href'):
            if attrs.get(name, '').startswith(('http:', 'https:')):
                self.external.append(attrs[name])


class TemplateTests(unittest.TestCase):
    def test_all_thirty_are_distinct_and_gallery_is_offline(self):
        self.assertEqual([t.id for t in TEMPLATES], list(range(1, 31)))
        self.assertEqual(len({(t.title, t.layout, t.scene, t.points) for t in TEMPLATES}), 30)
        html = reference_gallery()
        parser = Nodes()
        parser.feed(html)
        self.assertEqual(parser.templates, list(range(1, 31)))
        self.assertEqual(parser.external, [])
        self.assertNotIn('<script', html)
        self.assertIn("script-src 'none'", html)

    def test_unknown_templates_and_incomplete_comparisons_are_rejected(self):
        for value in (0, 31, True, '1'):
            with self.assertRaises(ValueError):
                template_by_id(value)
        item = {'template_id': 2, 'position': 1, 'content': {'phone': '09000000000', 'sim_type': 'دائمی', 'sim_status': 'صفر'}}
        with self.assertRaises(ValueError):
            story_markup(item)

    def test_source_phone_and_unsupported_claims_are_not_template_content(self):
        import re
        for template in TEMPLATES:
            text = ' '.join((*template.title, *template.points, template.cta))
            self.assertIsNone(re.search(r'09[0-9 ]{9,}', text))
            for claim in ('افزایش ارزش', 'سود', 'تضمین', 'سرمایه', 'تقاضای دائمی'):
                self.assertNotIn(claim, text)

    def test_operator_is_not_taken_from_untrusted_peer_fields(self):
        for value in ('<script>bad</script>', ['همراه اول'], None):
            content = clean_content({'phone': '09000000000', 'sim_type': 'دائمی', 'sim_status': 'صفر', 'operator': value, 'price_internal': 'private'})
            self.assertNotIn('operator', content)
            self.assertNotIn('price_internal', content)
