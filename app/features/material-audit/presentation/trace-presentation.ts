import {
  actionLabels,
  exceptionTypeLabels,
  gateOutcomeLabels,
  labelOf,
  materialLabels,
  reasonLabels,
  roleLabels,
  toolLabels,
} from "./labels";

export function presentAction(value: unknown): string {
  return labelOf(actionLabels, value);
}

export function presentGateOutcome(value: unknown): string {
  return labelOf(gateOutcomeLabels, value);
}

export function presentTool(value: unknown): string {
  return labelOf(toolLabels, value);
}

export function presentMaterial(value: unknown): string {
  return labelOf(materialLabels, value, "未知材料");
}

export function presentRole(value: unknown): string {
  return labelOf(roleLabels, value, "未知角色");
}

export function presentExceptionType(value: unknown): string {
  return labelOf(exceptionTypeLabels, value, "等待异常分类");
}

export function presentReason(value: unknown): string {
  return labelOf(reasonLabels, value);
}

export function presentFact(value: string): string {
  const [kind, ...rest] = value.split(":");
  const factValue = rest.join(":");
  return ({
    page: `影像页 ${factValue}`,
    person: `人员 ${factValue}`,
    role: `角色 ${labelOf(roleLabels, factValue, factValue)}`,
    material: `材料 ${labelOf(materialLabels, factValue, factValue)}`,
    requirement: `应交项 ${factValue}`,
    supplement: `补件任务 ${factValue}`,
  } as Record<string, string>)[kind] ?? value;
}
