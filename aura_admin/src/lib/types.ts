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
};
