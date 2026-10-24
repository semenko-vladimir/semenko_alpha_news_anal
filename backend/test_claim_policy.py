"""Behavior at both production validation boundaries, using fictional data only."""
import unittest

from backend.analysis.pipeline import AnalysisInput, AnalysisPipeline, Evidence
from backend.mvp.engine import NoExtraTools, OneAttempt
from backend.dialogue import DialogueContext, DialogueService, DialogueSource, SQLiteDialogueStore


def candidate(text):
    return {'kind': 'ai_inference', 'claim': text, 'evidence_refs': ['news-1'],
            'exposure_status': 'unknown', 'bank_source_id': None}


def data():
    return AnalysisInput('synthetic-company', 'COO', 'synthetic-event',
        (Evidence('news-1', 'news-source', 'news', 'Вымышленная задержка отгрузки; остатки и связь неизвестны.'),))


class ClaimPolicyTests(unittest.TestCase):
    def test_categorical_consequences_are_rejected_despite_unknown_flag(self):
        for text in (
            'Рост срока отгрузки до шести недель создаст дефицит запасов',
            'Изменение условий приведёт к кассовому разрыву компании.',
            'У компании вырастут расходы на финансирование.',
            'The delay will cause an inventory shortage.',
            'У компании образовался дефицит запасов.',
            'Компания испытывает дефицит запасов.',
            'The company has an inventory shortage.',
        ):
            with self.subTest(text=text):
                self.assertIn('unsupported_categorical_impact', AnalysisPipeline._validate(candidate(text), data()))

    def test_uncertainty_does_not_license_another_categorical_clause(self):
        for text in (
            'Возможны изменения. Компания понесёт убытки.',
            'Может возникнуть задержка, но она приведёт к дефициту.',
            'При наличии связи возможны задержки; запасов точно не хватит.',
            'Возможно, компания гарантированно получит убыток.',
            'Компания может изменить график и понесёт убытки.',
            'Не может быть сомнений: компания понесёт убытки.',
            'Дефицит запасов гарантирован.',
        ):
            with self.subTest(text=text):
                self.assertIn('unsupported_categorical_impact', AnalysisPipeline._validate(candidate(text), data()))

    def test_conditional_scenarios_and_unknowns_remain_valid(self):
        for text in (
            'При наличии связи задержка может привести к дефициту; остатки неизвестны.',
            'Возможен риск роста потребности в оборотном капитале.',
            'The delay may increase inventory risk if the company is exposed.',
            'Связь с событием и величина финансового эффекта неизвестны.',
            'Возможно, у компании вырастут расходы.',
            'Компания, вероятно, понесёт убытки.',
        ):
            with self.subTest(text=text):
                self.assertEqual(AnalysisPipeline._validate(candidate(text), data()), [])

    def test_single_assigned_candidate_is_stopped_without_a_second_generation(self):
        class SavedClient:
            calls = 0

            def generate(self, context):
                self.calls += 1
                return candidate('Задержка создаст дефицит запасов.')

        client = SavedClient()
        wrapped = OneAttempt(client, data())
        result = AnalysisPipeline(wrapped, NoExtraTools()).run(data())
        self.assertIsNone(result.validated)
        self.assertIn('unsupported_categorical_impact', wrapped.rejection)
        self.assertEqual(client.calls, 1)

    def test_dialogue_does_not_show_categorical_text_or_repeat_it(self):
        class SavedModel:
            calls = 0

            def generate(self, context):
                self.calls += 1
                return {'blocks': [{'kind': 'ai_inference', 'text': 'Задержка приведёт к дефициту.',
                                    'evidence_refs': ['news-source']}], 'unknowns': []}

        store = SQLiteDialogueStore(':memory:')
        self.addCleanup(store.close)
        store.register(DialogueContext('synthetic-company', 'COO', 'ins-1', 'ev-1', 'snap-1',
            (DialogueSource('news-source', 'synthetic:1', 'news', 'Вымышленная задержка.', 'synthetic-company'),)))
        model = SavedModel()
        service = DialogueService(model, None, store)
        scope = dict(company_id='synthetic-company', role_id='COO', insight_id='ins-1',
                     text='Какие риски?', idempotency_key='same-question')
        result = service.ask_message(**scope)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.error_code, 'unsupported_categorical_impact')
        self.assertEqual(result.blocks, [])
        replay = service.ask_message(**scope)
        self.assertEqual(replay, result)
        self.assertEqual(model.calls, 1)


if __name__ == '__main__':
    unittest.main()
