export interface PersonRole {
  person_id: string;
  name: string;
  roles: string[];
  confirmed: boolean;
  source: string;
}

export interface PageAsset {
  page_id: string;
  bundle_id: string;
  page_number: number;
  domain: string;
  material_type: string | null;
  owner_person_id: string | null;
  status: string;
  thumbnail_url: string | null;
  preview_url: string | null;
  extracted_fields: Record<string, unknown>;
  confidence: number | null;
  evidence_refs: string[];
}

export interface AtomicRequirement {
  requirement_id: string;
  title: string;
  checklist_version: number;
  effective_from: string;
  person_role: string;
  material_type: string;
  source_document: string;
  source_section: string;
  atomic_requirement: string;
  evidence_id: string | null;
}

export interface RequiredMaterialTask {
  task_id: string;
  task_type: string;
  status: string;
  depends_on: string[];
  fact_dependencies?: string[];
  task_dependencies?: string[];
  conflict_keys?: string[];
  requirement_refs?: string[];
  executor?: string;
  execution_group?: string;
  result_version?: number;
  requirement_id: string;
  person_id: string;
  person_role: string;
  material_type: string;
  matched_page_ids: string[];
  evidence_refs: string[];
  result?: {
    conclusion: string;
    confidence: number;
    case_version: number;
    plan_version: number;
    result_version?: number;
  } | null;
}

export interface HumanCandidateOption extends Record<string, unknown> {
  candidate_id?: string;
  page_ids?: string[];
  proposed_person_id?: string;
  proposed_material_type?: string;
}

export interface HumanTaskRequest extends Record<string, unknown> {
  type: string;
  action: HumanAction;
  task_id: string;
  human_task_id?: string;
  page_id?: string;
  person_id?: string;
  material_type?: string;
  requirement_id?: string;
  title?: string;
  rationale_summary?: string;
  reason?: string;
  reason_code?: string;
  candidate_page_ids?: string[];
  candidate_options?: HumanCandidateOption[];
  evidence_refs?: string[];
  requirement_grounding?: RequirementGrounding | null;
}

export interface RequirementGrounding {
  task_id: string;
  requirement_id: string;
  issue_status: string;
  evidence_id: string;
  child_chunk_id: string;
  parent_chunk_id: string | null;
  source_document: string;
  source_section: string;
  source_url: string | null;
  atomic_requirement: string;
  retrieval_scores: { dense: number; bm25: number; rrf: number; rerank: number | null };
}

export interface CaseState {
  case_id: string;
  thread_id: string;
  case_version: number;
  plan_version: number;
  persons: PersonRole[];
  person_entities?: Array<{
    person_id: string;
    display_name: string;
    status: string;
    mention_ids: string[];
    evidence_refs: string[];
  }>;
  identity_mentions?: Array<Record<string, unknown>>;
  role_signals?: Array<Record<string, unknown>>;
  role_bindings?: Array<{
    binding_id: string;
    person_id: string;
    role: string;
    status: string;
    confidence: number;
    evidence_refs: string[];
  }>;
  material_owner_bindings?: Array<Record<string, unknown>>;
  pages: PageAsset[];
  requirements: AtomicRequirement[];
  audit_plan: RequiredMaterialTask[];
  material_matches: Array<Record<string, unknown>>;
  human_tasks: Array<Record<string, unknown>>;
  supplement_requests: Array<Record<string, unknown>>;
  completeness_status: string;
  evidence_ledger: Array<Record<string, unknown>>;
  changed_facts: string[];
  dirty_tasks: string[];
  invalidated_tasks: string[];
  ready_task_ids?: string[];
  task_dispatch_id?: string | null;
  replan_decisions: Array<{
    task_id: string;
    operation: "KEEP" | "RERUN" | "INVALIDATE" | "ADD" | "RESOLVED";
    before?: string;
    after?: string;
  }>;
  pending_human_request: HumanTaskRequest | null;
  active_node: string | null;
  current_task_id: string | null;
  problem_tasks: Array<Record<string, unknown>>;
  audit_assignment: Record<string, unknown> | null;
  audit_decision: Record<string, unknown> | null;
  audit_gate: Record<string, unknown> | null;
  association_assignment: Record<string, unknown> | null;
  association_decision: Record<string, unknown> | null;
  association_gate: Record<string, unknown> | null;
  supplement_groundings: RequirementGrounding[];
  status: string;
  business_fields: {
    product_type?: string;
    channel?: string;
    case_date?: string;
    material_manifest?: {
      image_count: number;
      bundle_count: number;
      domain_count: number;
      domains: Array<{ name: string; count: number }>;
    };
  };
}

export interface AuditEvent {
  event_id: string;
  seq: number;
  event_type: string;
  actor: string;
  timestamp: string;
  thread_id?: string | null;
  run_id?: string | null;
  checkpoint_id?: string | null;
  payload: {
    node?: string;
    task_id?: string | null;
    action?: string;
    tool?: string | null;
    observation?: unknown;
    state_diff?: Record<string, unknown>;
    evidence_refs?: string[];
    [key: string]: unknown;
    payload?: Record<string, unknown>;
  };
}

export interface RagCandidate {
  requirement_id: string;
  title: string;
  person_role: string;
  material_type: string;
  checklist_version: number;
  source_document: string;
  source_section: string;
  atomic_requirement: string;
  effective_from: string;
  dense_score: number;
  dense_rank: number | null;
  bm25_score: number;
  bm25_rank: number | null;
  rrf_score: number;
  rrf_rank: number | null;
  rerank_score: number | null;
  rerank_rank: number | null;
  eligible: boolean;
  selected: boolean;
  filter_reasons: string[];
  evidence_id: string;
  child_chunk_id: string;
  metadata: Record<string, unknown>;
}

export interface RagTrace {
  trace_type?: string;
  trigger?: string;
  reason?: string;
  original_query: string | null;
  rewritten_query: string | null;
  retrieval: {
    strategy: string;
    channel_backend: string | null;
    reranker: string | null;
    candidate_count: number;
    eligible_count: number;
  };
  pipeline: Array<Record<string, unknown> & { stage: string }>;
  candidates: RagCandidate[];
  selected: RagCandidate[];
  final_requirements: string[];
  problem_task_ids?: string[];
  groundings?: RequirementGrounding[];
}

export interface KnowledgeCitation {
  requirement_id: string;
  child_chunk_id: string;
  title: string;
  source_document: string;
  source_section: string;
  source_url: string | null;
  region: string | null;
  atomic_requirement: string;
  parent_chunk_id: string | null;
  parent_title: string | null;
  parent_text: string | null;
}

export interface KnowledgeResult {
  question: string;
  status: "ANSWERED" | "INSUFFICIENT_EVIDENCE" | "CLARIFY" | "REFUSE";
  intent: {
    route: "ACCEPT" | "CLARIFY" | "REFUSE";
    primary_intent: string | null;
    answer_modes: string[];
    query_modes: string[];
    entities: {
      regions: string[];
      branches: string[];
      marriage_statuses: string[];
      person_roles: string[];
      product: string | null;
      material_domain_code?: string | null;
      material_domain: string | null;
      material_type: string | null;
      case_date: string | null;
    };
    confidence: number;
    reason_code: string;
    user_message: string;
    router: string;
  };
  applied_filters: Record<string, string | string[]>;
  answer: string;
  citations: KnowledgeCitation[];
  citation_validation: { status: string; cited_chunk_ids: string[]; format_repaired?: boolean };
  trace: RagTrace;
}

export interface KnowledgeEvent {
  seq: number;
  event_type: "KNOWLEDGE_RUN_STARTED" | "KNOWLEDGE_STAGE_COMPLETED" | "KNOWLEDGE_RUN_COMPLETED" | "KNOWLEDGE_RUN_FAILED";
  run_id: string;
  timestamp: string;
  payload: Record<string, unknown> & { stage: string };
}

export interface KnowledgeRun {
  run_id: string;
  question: string;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  created_at: string;
  finished_at?: string | null;
  error?: string | null;
  result?: KnowledgeResult | null;
  stream_url: string;
}

export interface KnowledgeBuildReport {
  backend: string;
  record_count: number;
  products: string[];
  regions: string[];
  source_type: string;
  chunking: string;
  indexing: string;
  offline_build?: {
    source_count: number;
    child_chunk_count: number;
    contextualizer: string;
    tokenizer: string;
  } | null;
  catalog_link?: {
    official_requirement_count: number;
    official_linked_count: number;
    source_chunk_count: number;
  } | null;
  online_index?: {
    backend: string;
    collection: string;
    record_count: number;
    dense_model: string;
    sparse_model: string;
    reranker_model: string;
  } | null;
  stages: string[];
  supported_intents: string[];
  query_modes: string[];
  suggested_questions: string[];
}

export type AuditRunStatusValue = "QUEUED" | "RUNNING" | "PAUSED" | "COMPLETED" | "FAILED";

export interface AuditRun {
  run_id: string;
  case_id: string;
  thread_id: string;
  status: AuditRunStatusValue;
  after_seq: number;
  created_at: string;
  finished_at?: string | null;
  error?: string | null;
  stream_url: string;
}

export type HumanAction = "CONFIRM_ASSOCIATION" | "RESOLVE_ASSOCIATION_EVIDENCE" | "CONFIRM_OWNER" | "REVIEW_IMAGE" | "REQUEST_SUPPLEMENT" | "SUPPLEMENT_RECEIVED";

export interface HumanResumeCommand {
  event_id: string;
  action: HumanAction;
  task_id: string;
  page_id?: string;
  person_id?: string;
  person_name?: string;
  roles?: string[];
  material_type?: string;
  page?: Record<string, unknown>;
  selected_candidate_id?: string;
  selected_candidate_ids?: string[];
  reason_code?: string;
  operator_id?: string;
}
