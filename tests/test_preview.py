import unittest
from espadna.core import connect, bootstrap_owner, set_sale_status
from espadna.content import create_plan, approve_plan
from espadna.preview import preview_day, story_markup


class PreviewTests(unittest.TestCase):
    def test_public_fields_only_and_stale_preview_blocked(self):
        db = connect(':memory:')
        try:
            bootstrap_owner(db, 1)
            for i in range(6):
                db.execute('INSERT INTO inventory(phone,sim_type,sim_status,price_internal) VALUES(?,1,2,?)', (f'090000000{i:02}', '76543210'))
            plan = create_plan(db, 1, '2026-11')
            approve_plan(db, 1, plan)
            html = preview_day(db, 1, plan, '2026-11-01')
            self.assertEqual(html.count('data-template="'), 6)
            self.assertIn('0900 000 0000', html)
            self.assertNotIn('76543210', html)
            self.assertNotIn('<script', html)
            self.assertNotIn('https://', html)
            with self.assertRaises(ValueError):
                preview_day(db, 1, plan, '2026-12-01')
            set_sale_status(db, 1, '09000000000', 'sold')
            with self.assertRaises(ValueError):
                preview_day(db, 1, plan, '2026-11-01')
        finally:
            db.close()

    def test_render_does_not_trust_caption_operator_or_unrecognized_labels(self):
        item = {'position': 1, 'caption': 'SECRET_PRICE', 'content': {'phone': '09000000000', 'sim_type': 'دائمی', 'sim_status': 'صفر', 'operator': '<script>SECRET_PRICE</script>', 'price': 'SECRET_PRICE'}}
        self.assertNotIn('SECRET_PRICE', story_markup(item))
        item['content']['sim_type'] = '<script>SECRET_PRICE</script>'
        with self.assertRaises(ValueError):
            story_markup(item)
