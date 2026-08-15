from __future__ import annotations

from ..domain.models import CaseState
from ..planning.planner import build_plan


def create_case(scenario: str) -> CaseState:
    prefix = {
        "normal": "NORMAL",
        "ocr_conflict": "OCR",
        "supplement_replan": "SUPP",
        "policy_version": "POL",
        "loop_guard": "LOOP",
        "architecture_demo": "ARCH",
    }.get(scenario, "DEMO")
    has_marriage = scenario not in {"supplement_replan", "loop_guard", "architecture_demo"}
    has_ocr_conflict = scenario in {"ocr_conflict", "loop_guard", "architecture_demo"}
    case_id = "CASE-ZD-042" if scenario == "architecture_demo" else f"CASE-{prefix}-001"
    state = CaseState(
        case_id=case_id,
        documents=[
            {"document_id": "DOC-01", "type": "borrower_id", "status": "VERIFIED", "fields": {"name": "张三"}},
            {"document_id": "DOC-02", "type": "mortgagor_id", "status": "VERIFIED", "fields": {"name": "李四"}},
            {"document_id": "DOC-03", "type": "property_certificate", "status": "VERIFIED", "fields": {"owner": "李四"}},
            {
                "document_id": "DOC-04",
                "type": "household_register",
                "status": "LOW_CONFIDENCE" if has_ocr_conflict else "VERIFIED",
                "fields": {"name": "张叁" if has_ocr_conflict else "张三"},
                "confidence": 0.61 if has_ocr_conflict else 0.99,
            },
            {
                "document_id": "DOC-05",
                "type": "marriage_certificate",
                "status": "VERIFIED" if has_marriage else "MISSING",
                "fields": {"husband": "张三", "wife": "李四"} if has_marriage else {},
            },
            {"document_id": "DOC-06", "type": "spouse_consent", "status": "VERIFIED", "fields": {"signer": "李四"}},
        ],
        entities={
            "borrower": {"name": "张三", "role": "小微企业实际经营人"},
            "mortgagor": {"name": "李四"},
        },
        business_fields={
            "scenario": scenario,
            "product_type": "宅抵贷",
            "loan_purpose": "企业流动资金",
            "application_amount": 2_800_000,
            "loan_amount": 2_800_000,
            "display_amount": "280 万元",
            "loan_term_months": 60,
            "display_term": "60 个月",
            "company_age_months": 10,
            "property_holding_months": 8,
            "purchase_price": 3_600_000,
            "appraised_value": 5_200_000,
            "valuation_deviation": 0.444,
            "purchase_contract_amount": 1_200_000,
            "payment_control": "ENTRUSTED_PAYMENT",
            "relation": "UNKNOWN",
            "ocr_conflict": has_ocr_conflict,
            "case_date": "2026-08-15",
            **({"exception_tool_plan": ["ocr_retry", "ocr_retry", "document_search"]} if scenario == "loop_guard" else {}),
        },
        audit_plan=build_plan("UNKNOWN"),
    )
    return state
