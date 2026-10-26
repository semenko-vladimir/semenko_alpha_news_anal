import json
import unittest
import uuid
from copy import deepcopy
from pathlib import Path

try:
    from backend.profiles.catalog import load_public_catalog, SECTORS
except ImportError:
    load_public_catalog, SECTORS = None, {}


class PublicCatalogTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('.tmp/tests/final-catalog') / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.source = Path('data/catalog/companies.json')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root)

    def load(self, data):
        self.assertTrue(callable(load_public_catalog), 'Public catalog loader is not implemented')
        path = self.root / 'catalog.json'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return load_public_catalog(path)

    def test_eleven_real_public_profiles_have_independent_ids_sources_and_dated_metrics(self):
        self.assertTrue(callable(load_public_catalog), 'Public catalog loader is not implemented')
        profiles = load_public_catalog(self.source)
        self.assertEqual(len(profiles), 11)
        self.assertEqual(len({p.category for p in profiles}), 11)
        for profile in profiles:
            self.assertTrue(profile.company_id.startswith('PUB_'))
            self.assertEqual(profile.features['profile_origin'], 'public')
            self.assertIn(profile.category, SECTORS)
            self.assertTrue(profile.source_refs)
            self.assertTrue(all(s['uri'].startswith('https://') and s['synthetic'] is False for s in profile.source_refs))
            for metric in profile.features['metrics']:
                self.assertTrue(all(metric.get(k) for k in ['name','unit','period','source_id']))
            self.assertNotIn('flags', profile.features)

    def test_duplicate_identity_unknown_sector_and_nonofficial_source_fail_closed(self):
        self.assertTrue(self.source.exists(), 'Verified public profiles are not yet supplied')
        data = json.loads(self.source.read_text(encoding='utf-8'))
        for mutation in ['duplicate','missing_profile','sector','source','missing_period']:
            changed = deepcopy(data)
            if mutation == 'duplicate': changed['companies'].append(deepcopy(changed['companies'][0]))
            elif mutation == 'missing_profile': changed['companies'].pop()
            elif mutation == 'sector': changed['companies'][0]['sector_id'] = 'MADE_UP'
            elif mutation == 'source': changed['companies'][0]['sources'][0]['uri'] = 'https://127.0.0.1/secret'
            else:
                candidate = next(c for c in changed['companies'] if c['metrics'])
                candidate['metrics'][0]['period'] = None
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.load(changed)

    def test_public_fields_do_not_invent_revenue_units_or_case_identity(self):
        self.assertTrue(callable(load_public_catalog), 'Public catalog loader is not implemented')
        profiles = load_public_catalog(self.source)
        self.assertTrue(all(not p.company_id.startswith('U_') for p in profiles))
        bank = next(p for p in profiles if p.company_id == 'PUB_SBER')
        self.assertEqual(bank.features['eligibility'], 'unknown')
        self.assertFalse(any(m['name'] == 'Выручка' for m in bank.features['metrics']))

    def test_new_sector_topics_rank_own_operational_news_over_another_sector(self):
        from backend.mvp.relevance import source_relevance
        cases={
            'ENERGY':'Нефтепереработка: график загрузки мощностей изменится.',
            'MATERIALS':'Металлургия: отгрузки никеля и поставки руды изменились.',
            'INDUSTRIALS':'Контейнерные перевозки: график рейсов и логистика.',
            'CONSUMER_DISCRETIONARY':'Электроника: поставки бытовой техники на склады.',
            'CONSUMER_STAPLES':'Продовольственные магазины: поставки продуктов и графики смен.',
            'HEALTH_CARE':'Клиники: загрузка медицинских мощностей и смены персонала.',
            'FINANCIALS':'Банковские операции: обработка платежей и доступность сервиса.',
            'INFORMATION_TECHNOLOGY':'Кибербезопасность: сроки внедрения программного обеспечения.',
            'COMMUNICATION_SERVICES':'Телекоммуникации: мощности сети и сроки подключения.',
            'UTILITIES':'Электроэнергетика: загрузка генерации и ремонт мощностей.',
            'REAL_ESTATE':'Девелопмент: поставки материалов на строительные площадки.',
        }
        for sector,text in cases.items():
            with self.subTest(sector=sector):
                own=source_relevance(text,sector,'COO')
                self.assertGreater(own['score'],0)
                self.assertIn(own['basis'],{'profile_category_only','profile_category_and_role'})

    def test_social_programs_and_securities_orders_do_not_become_it_or_retail(self):
        from backend.mvp.relevance import source_relevance
        self.assertEqual(source_relevance('Социальные программы компании отмечены наградами.','INFORMATION_TECHNOLOGY','COO')['score'],0)
        self.assertNotIn(source_relevance('Инсайдер имел доступ к торговым поручениям клиентов биржи.','CONSUMER_STAPLES','COO')['basis'],{'profile_category_only','profile_category_and_role'})


if __name__ == '__main__': unittest.main()
