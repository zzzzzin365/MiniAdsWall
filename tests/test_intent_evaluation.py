import unittest
from collections import Counter
from types import SimpleNamespace

from core.intent_recognizer import IntentCategory
from evaluation.evaluator import DEFAULT_INTENT_CASES, IntentEvaluator


class _ControlledRecognizer:
    def __init__(self, force_one_error: bool = False):
        self._force_one_error = force_one_error
        self._labels = {
            case.message: case.expected_intent
            for case in DEFAULT_INTENT_CASES
        }

    async def recognize(self, message: str):
        label = self._labels[message]
        if self._force_one_error and message == DEFAULT_INTENT_CASES[0].message:
            label = IntentCategory.OTHER.value
        return SimpleNamespace(
            intent=IntentCategory(label),
            confidence=1.0,
            reasoning="test stub",
        )


class FixedIntentDatasetTest(unittest.IsolatedAsyncioTestCase):
    def test_dataset_is_complete_and_balanced(self):
        label_counts = Counter(
            case.expected_intent
            for case in DEFAULT_INTENT_CASES
        )
        variants = {case.variant for case in DEFAULT_INTENT_CASES}

        self.assertEqual(len(DEFAULT_INTENT_CASES), 260)
        self.assertEqual(set(label_counts), {category.value for category in IntentCategory})
        self.assertEqual(set(label_counts.values()), {20})
        self.assertEqual(
            variants,
            {"standard", "colloquial", "typo", "ambiguous", "out_of_scope"},
        )

    async def test_metrics_include_all_classes_and_confusion_matrix(self):
        metrics = await IntentEvaluator(
            _ControlledRecognizer(force_one_error=True)
        ).evaluate(DEFAULT_INTENT_CASES)

        self.assertEqual(metrics["accuracy"], round(259 / 260, 4))
        self.assertEqual(len(metrics["per_class"]), 13)
        self.assertTrue(
            all(item["support"] == 20 for item in metrics["per_class"].values())
        )
        self.assertEqual(metrics["confusion_matrix"]["query"]["other"], 1)
        self.assertEqual(
            metrics["confusions"][0],
            {"expected": "query", "predicted": "other", "count": 1},
        )


if __name__ == "__main__":
    unittest.main()
