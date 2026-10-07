import unittest

from sql_agent.evaluation import results_match


class ResultsMatchTest(unittest.TestCase):
    def test_exact_ignoring_row_and_column_order(self):
        gold = [("Rock", 2), ("Jazz", 1)]
        self.assertEqual(results_match(gold, [(1, "Jazz"), (2, "Rock")]), "exact")

    def test_numeric_normalization(self):
        self.assertEqual(results_match([(2328.6,)], [(2328.6000001,)]), "exact")
        self.assertEqual(results_match([(3,)], [(3.0,)]), "exact")

    def test_superset_columns(self):
        gold = [("Rock", 2)]
        self.assertEqual(results_match(gold, [(1, "Rock", 2)]), "superset")

    def test_order_matters(self):
        gold = [("2012-01", 1.0), ("2012-02", 2.0)]
        pred = list(reversed(gold))
        self.assertEqual(results_match(gold, pred), "exact")
        self.assertIsNone(results_match(gold, pred, order_matters=True))

    def test_mismatch(self):
        self.assertIsNone(results_match([(1,)], [(2,)]))
        self.assertIsNone(results_match([(1,)], [(1,), (1,)]))
        self.assertIsNone(results_match([("a", 1)], [("a",)]))


if __name__ == "__main__":
    unittest.main()
