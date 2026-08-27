import type { CaseState, RequiredMaterialTask } from "../api/contracts";
import { executorLabels, labelOf, materialLabels, roleLabels, statusLabels } from "./labels";

export interface TaskPresentation {
  title: string;
  requirementTitle: string;
  personLabel: string;
  statusLabel: string;
  executorLabel: string;
  technicalId: string;
}

/**
 * 从当前 Case Projection 生成中文任务名。
 *
 * Requirement 标题和人员事实来自后端状态，前端只负责展示组合，不在这里复制
 * RuleEngine 或 Task Planner 的任何业务判断。
 */
export function presentTask(state: CaseState, task: RequiredMaterialTask): TaskPresentation {
  const requirement = state.requirements.find((item) => item.requirement_id === task.requirement_id);
  const person = state.persons.find((item) => item.person_id === task.person_id);
  const roleLabel = labelOf(roleLabels, task.person_role, "对应人员");
  const materialLabel = labelOf(materialLabels, task.material_type, "未知材料");
  const personLabel = person?.name ? `${person.name} · ${roleLabel}` : `${roleLabel} ${task.person_id}`;
  return {
    title: `${roleLabel} · ${materialLabel}齐套核验`,
    requirementTitle: requirement?.title ?? `应交项 ${task.requirement_id}`,
    personLabel,
    statusLabel: labelOf(statusLabels, task.status, "等待审核"),
    executorLabel: labelOf(
      executorLabels,
      task.executor,
      task.status === "AMBIGUOUS"
        ? "材料语义仲裁 Agent + 校验门"
        : task.status === "UNREADABLE"
          ? "异常取证恢复子 Agent"
          : "材料匹配 Worker",
    ),
    technicalId: task.task_id,
  };
}

