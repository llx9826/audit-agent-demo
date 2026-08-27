import unittest

from app.orchestration.association_evidence import (
    PageFieldAssociationEvidenceExtractor,
    ToolAssociationEvidenceExtractor,
)
from app.orchestration.stages.association import (
    dispatch_pages,
    extract_evidence,
    extract_page,
    select_pages,
)
from app.tools import ToolObservation, ToolRegistry, ToolSpec


class AssociationEvidenceTests(unittest.TestCase):
    def test_page_selector_send_workers_and_fan_in_keep_only_relevant_pages(self):
        state = {
            "case_id": "CASE-A",
            "thread_id": "THREAD-A",
            "case_version": 1,
            "plan_version": 1,
            "persons": [{"person_id": "P01", "name": "张某", "roles": [], "confirmed": False}],
            "pages": [
                {
                    "page_id": "PAGE-ID", "bundle_id": "B1", "domain": "身份与主体证明",
                    "material_type": "identity_document", "owner_person_id": "P01",
                    "confidence": 0.99, "evidence_refs": ["EV-ID"],
                    "extracted_fields": {"person_id": "P01", "person_name": "张某", "role_signals": ["BORROWER"]},
                },
                {
                    "page_id": "PAGE-OTHER", "bundle_id": "B2", "domain": "声明、附件与其他材料",
                    "material_type": "supporting_page", "owner_person_id": None,
                    "confidence": 0.9, "evidence_refs": [], "extracted_fields": {},
                },
            ],
            "pending_events": [],
        }
        state.update(select_pages(state))
        sends = dispatch_pages(state)
        self.assertEqual(state["association_page_ids"], ["PAGE-ID"])
        self.assertEqual(len(sends), 1)
        self.assertNotIn("pages", sends[0].arg)

        worker = extract_page(
            sends[0].arg,
            evidence_extractor=PageFieldAssociationEvidenceExtractor(),
        )
        state["association_evidence_results"] = worker["association_evidence_results"]
        merged = extract_evidence(state)
        self.assertEqual(merged["identity_mentions"][0]["person_id"], "P01")
        self.assertEqual(merged["role_signals"][0]["role"], "BORROWER")
        self.assertEqual(merged["material_owner_signals"][0]["page_id"], "PAGE-ID")

    def test_real_tool_extractor_uses_registry_contract_and_structured_metadata(self):
        registry = ToolRegistry()
        captured = {}

        def handler(_arguments, runtime):
            captured.update(runtime.values)
            return ToolObservation(
                result="structured",
                confidence=0.97,
                evidence_refs=["EV-VLM"],
                metadata={"fields": {
                    "person_id": "P09", "person_name": "李某",
                    "role_signals": ["MORTGAGOR"], "owner_person_id": "P09",
                }},
            )

        registry.register(ToolSpec(
            name="vlm_extract", version="1", description="extract",
            provider_type="LOCAL", provider_name="vlm-service",
            supported_intents=["ASSOCIATION:IDENTITY_ROLE_EXTRACTION"],
            side_effect="STATE_PROPOSAL",
        ), handler)
        extractor = ToolAssociationEvidenceExtractor(registry)
        result = extractor.extract(case_id="CASE-REAL", page={
            "page_id": "PAGE-9", "bundle_id": "B9", "domain": "身份与主体证明",
            "material_type": "identity_document", "preview_url": "/pages/9",
        })

        self.assertEqual(result.person_id, "P09")
        self.assertEqual(result.role_signals, ["MORTGAGOR"])
        self.assertEqual(result.provider, "LOCAL:vlm-service")
        self.assertEqual(captured["page"]["page_id"], "PAGE-9")
        self.assertNotIn("owner_person_id", captured["page"])


if __name__ == "__main__":
    unittest.main()
