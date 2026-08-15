from __future__ import annotations

from copy import deepcopy
from typing import Any


def reconcile(old_facts: dict[str, Any], supplement: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Merge a supplement and return stable business-level changed-fact keys."""
    merged = deepcopy(old_facts)
    changed: list[str] = []
    if supplement.get("marriage_certificate"):
        merged.setdefault("documents", {})["marriage_certificate"] = supplement["marriage_certificate"]
        changed.append("marriage_documents")
        husband = supplement["marriage_certificate"].get("husband")
        wife = supplement["marriage_certificate"].get("wife")
        if {husband, wife} == {old_facts.get("borrower"), old_facts.get("mortgagor")}:
            if merged.get("relation") != "SPOUSE":
                merged["relation"] = "SPOUSE"
                changed.append("relation")
    return merged, changed
