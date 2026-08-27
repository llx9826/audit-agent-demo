"""Requirement-driven material checklist planning and selective replan."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

from ..domain.models import AtomicRequirement, PersonRole, RequiredMaterialTask


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def build_plan(
    requirements: Iterable[AtomicRequirement | dict[str, Any]] | str | None = None,
    persons: Iterable[PersonRole | dict[str, Any]] | None = None,
) -> list[RequiredMaterialTask]:
    """Compile one deterministic task per applicable person/requirement pair.

    Requirement applicability (product, channel, version and effective date) is
    decided by the deterministic Requirement RuleEngine before this function runs. The planner never
    invents tasks outside material completeness.
    """

    if requirements is None or isinstance(requirements, str):
        return []
    people = list(persons or [])
    tasks: list[RequiredMaterialTask] = []
    for requirement in requirements:
        role = str(_value(requirement, "person_role"))
        requirement_id = str(_value(requirement, "requirement_id"))
        material_type = str(_value(requirement, "material_type"))
        for person in people:
            person_roles = list(_value(person, "roles", []))
            if role not in person_roles:
                continue
            person_id = str(_value(person, "person_id"))
            task_id = f"TASK-{requirement_id.removeprefix('REQ-')}-{person_id}"
            fact_dependencies = [
                f"requirement:{requirement_id}",
                f"person:{person_id}",
                f"role:{role}",
                f"material:{material_type}",
            ]
            tasks.append(RequiredMaterialTask(
                task_id=task_id,
                task_type="required_material",
                status="PENDING",
                depends_on=fact_dependencies,
                fact_dependencies=fact_dependencies,
                task_dependencies=[],
                # Worker 只读页面；相同人员与材料槽位的提交由 Fan-in Gate 串行校验。
                conflict_keys=[f"material_slot:{person_id}:{material_type}"],
                requirement_refs=[requirement_id],
                requirement_id=requirement_id,
                person_id=person_id,
                person_role=role,
                material_type=material_type,
            ))
    return sorted(tasks, key=lambda task: task.task_id)


def impacted_task_ids(
    tasks: list[RequiredMaterialTask],
    changed_facts: list[str],
) -> list[str]:
    changed = set(changed_facts)
    impacted: list[str] = []
    for task in tasks:
        page_dependencies = {f"page:{page_id}" for page_id in task.matched_page_ids}
        fact_dependencies = set(task.fact_dependencies or task.depends_on)
        if changed.intersection(fact_dependencies | page_dependencies):
            impacted.append(task.task_id)
            continue
        for fact in changed:
            if fact.startswith("pages.") and fact.split(".", 2)[1] in task.matched_page_ids:
                impacted.append(task.task_id)
                break
    return impacted


def selective_replan(
    tasks: list[RequiredMaterialTask],
    changed_facts: list[str],
    new_tasks: Iterable[RequiredMaterialTask] | str | None = None,
    resolved_task_ids: set[str] | None = None,
) -> list[RequiredMaterialTask]:
    """Invalidate only impacted results, keep unaffected results reusable."""

    impacted = set(impacted_task_ids(tasks, changed_facts))
    resolved = resolved_task_ids or set()
    revised: list[RequiredMaterialTask] = []
    for task in tasks:
        if task.task_id in resolved:
            revised.append(replace(task, status="MATCHED"))
        elif task.task_id in impacted:
            revised.append(replace(
                task,
                status="INVALIDATED" if task.result else "DIRTY",
                result=None,
                matched_page_ids=[],
                evidence_refs=[],
            ))
        else:
            revised.append(task)
    if new_tasks is not None and not isinstance(new_tasks, str):
        existing = {task.task_id for task in revised}
        revised.extend(task for task in new_tasks if task.task_id not in existing)
    return sorted(revised, key=lambda task: task.task_id)
