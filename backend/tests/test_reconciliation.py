import unittest

from app.planning.reconciliation import reconcile


class ReconciliationTests(unittest.TestCase):
    def test_marriage_certificate_changes_relation(self):
        facts = {"borrower": "张三", "mortgagor": "李四", "relation": "UNKNOWN"}
        merged, changed = reconcile(facts, {"marriage_certificate": {"husband": "张三", "wife": "李四"}})
        self.assertEqual(merged["relation"], "SPOUSE")
        self.assertEqual(changed, ["marriage_documents", "relation"])
        self.assertEqual(facts["relation"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
