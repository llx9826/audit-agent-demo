import unittest
from datetime import date

from app.rag.hybrid import HybridPolicyRetriever, PolicyRule, demo_policy_trace


class RagTests(unittest.TestCase):
    def test_higher_similarity_old_version_is_filtered(self):
        retriever = HybridPolicyRetriever([
            PolicyRule("V1", "宅抵贷审核办法", 1, date(2024, 1, 1), "宅抵贷", "旧版", .91, .89),
            PolicyRule("V2", "宅抵贷审核办法", 2, date(2025, 1, 1), "宅抵贷", "新版", .86, .93),
        ])
        result = retriever.search(case_date=date(2026, 8, 15), product="宅抵贷")
        self.assertEqual(result[0]["rule"].rule_id, "V2")

    def test_trace_explains_why_high_similarity_v1_is_not_grounding(self):
        trace = demo_policy_trace()
        by_id = {item["rule_id"]: item for item in trace["candidates"]}
        self.assertGreater(by_id["DEMO-COST-2025"]["dense_score"], by_id["NFRA-2026-COST-01"]["dense_score"])
        self.assertFalse(by_id["DEMO-COST-2025"]["eligible"])
        self.assertEqual(by_id["DEMO-COST-2025"]["filter_reason"], "VERSION_INACTIVE")
        self.assertEqual(trace["final_rule"], "NFRA-2026-COST-01")
        self.assertEqual(trace["retrieval"]["score_source"], "LOCAL_CORPUS_RUNTIME")
        self.assertTrue(by_id["NFRA-2026-COST-01"]["source_url"].startswith("https://www.nfra.gov.cn/"))


if __name__ == "__main__":
    unittest.main()
