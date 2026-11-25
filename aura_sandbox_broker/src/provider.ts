// What the broker needs from a sandbox vendor. `railway.ts` is the only real implementation;
// tests use an in-memory one. Everything above this file is vendor-neutral.

export interface ExecRequest {
  timeoutSec: number;
  /** Set in the command's environment, which unprivileged processes cannot read for root. */
  env?: Record<string, string>;
  signal?: AbortSignal;
  onStdout?: (chunk: string) => void;
  onStderr?: (chunk: string) => void;
}

export interface ExecOutcome {
  /** `null` when the transport could not confirm how the command ended. */
  exitCode: number | null;
  timedOut: boolean;
}

/** One running sandbox. Commands and file access run as root: callers confine them. */
export interface ProviderSandbox {
  readonly id: string;
  exec(command: string, request: ExecRequest): Promise<ExecOutcome>;
  writeFile(path: string, data: Uint8Array | AsyncIterable<Uint8Array>, mode?: number): Promise<void>;
  readFile(path: string): Promise<AsyncIterable<Uint8Array>>;
  fileSize(path: string): Promise<number>;
  checkpoint(name: string): Promise<void>;
  destroy(): Promise<void>;
}

export interface SandboxSummary {
  id: string;
  status: string;
  createdAt: string;
}

export interface CheckpointSummary {
  name: string;
  createdAt: string;
}

export interface SandboxProvider {
  /** Build whatever a fresh sandbox boots from. Safe to call repeatedly. */
  prepare(): Promise<void>;
  create(from: { checkpoint?: string }): Promise<ProviderSandbox>;
  /** `null` when no running sandbox has this id. */
  connect(id: string): Promise<ProviderSandbox | null>;
  /** Destroy by id, whatever its status. Idempotent. */
  destroy(id: string): Promise<void>;
  list(): Promise<SandboxSummary[]>;
  checkpoints(): Promise<CheckpointSummary[]>;
  /** Idempotent: deleting a checkpoint that does not exist succeeds. */
  deleteCheckpoint(name: string): Promise<void>;
}

/** The named checkpoint a create asked for does not exist. */
export class CheckpointNotFound extends Error {}

/** The sandbox stopped existing between looking it up and using it. */
export class SandboxGone extends Error {}
