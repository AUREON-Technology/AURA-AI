export type OperatorRole = "User" | "Developer" | "Admin" | "SuperAdmin";

export type Me = {
  uid: string;
  email: string;
  name?: string;
  role: OperatorRole;
  is_admin: boolean;
  is_super_admin: boolean;
};

export type ProviderRow = {
  provider: string;
  state: string;
  healthy: boolean;
  inflight: number;
  requests: number;
  failures: number;
  failure_rate: number;
  latency_ms: number | null;
  ttft_ms: number | null;
  circuit_opens: number;
};

export type PromptRow = {
  id: string;
  versions: string[];
  active: string;
  pinned: string | null;
  checksum: string;
  description?: string;
};

export type AdminStatus = {
  health: { healthy: boolean; environment?: string; index?: string | null };
  providers: ProviderRow[];
  prompts: PromptRow[];
  tools: { name: string; description?: string }[];
  settings: Record<string, unknown>;
  learning: Record<string, unknown>;
  refresh: Record<string, unknown> | null;
  /** "worker": provider and breaker state is this gateway worker's, not the deployment's. */
  providers_scope?: string;
  worker_pid?: number;
};

/** POST /admin/providers/{name}/reset-circuit. The scope fields are optional (older gateways). */
export type ResetCircuitResult = {
  reset: string;
  scope?: string;
  worker_pid?: number;
  note?: string;
};

/** POST /admin/prompts/{id}/pin. `shared`/`propagation_s` are optional (older gateways). */
export type PinResult = {
  pinned: string;
  shared?: boolean;
  propagation_s?: number;
};

export type KnowledgeSource = {
  id: string;
  type: string;
  uri: string;
  enabled: boolean;
  trust_tier: number;
  last_success_at: string | null;
};

export type Approval = {
  id: string;
  kind: string;
  summary: string;
  status: string;
  artefact_digest: string;
  expires_at: string | null;
  decided_by: string | null;
  payload?: Record<string, unknown>;
};

export type ModelVersion = {
  model_id: string;
  version: string;
  stage: string;
  dataset_digest: string;
  artefact_uri?: string;
  canary_percent: number;
  approval_id: string | null;
};

export type AuditEvent = {
  action: string;
  actor: string;
  actor_role: string;
  resource: string;
  outcome: string;
  correlation_id: string;
  at: string | null;
};

export type Operator = {
  email: string;
  auth_subject: string;
  display_name: string;
  role: OperatorRole;
  /** "Access page", an env list (e.g. "ADMIN_EMAILS"), or "roles.json". */
  granted_by?: string | null;
};

/** POST /admin/users/{subject}/forget (api/admin_platform.py, erase_account). */
export type ForgetResult = {
  /** False when any store failed; the users row is then kept. */
  forgotten: boolean;
  counts: Record<string, number>;
  /** Stores that could not be erased. */
  failed?: string[];
  /** What erasure does not reach yet, reported with every result. */
  not_erased?: string[];
};

/** One row of GET /admin/customers (aura_core/billing/service.py BillingService.customers). */
export type Customer = {
  workspace_id: string;
  workspace_name: string | null;
  kind: string | null;
  plan: string;
  seats: number;
  status: string;
  provider: string;
  period_end: string;
  grace_until: string | null;
  last_payment: { status: string; amount_piastres: number; at: string } | null;
};

export type CustomersPage = { customers: Customer[]; next: string | null };
