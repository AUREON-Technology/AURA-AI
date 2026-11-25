// An in-memory sandbox that understands the broker's guest commands, so the HTTP behaviour is
// tested end to end without Railway. It does not prove the guest scripts: those are proven on a
// real sandbox.
import {
  CheckpointNotFound,
  SandboxGone,
  type CheckpointSummary,
  type ExecOutcome,
  type ExecRequest,
  type ProviderSandbox,
  type SandboxProvider,
  type SandboxSummary,
} from "../src/provider.js";

export type RunHandler = (command: string, request: ExecRequest) => Promise<ExecOutcome> | ExecOutcome;

const ok: ExecOutcome = { exitCode: 0, timedOut: false };

/** Split a command line made by `shellQuote` back into words. */
export function words(command: string): string[] {
  const out: string[] = [];
  let current = "";
  let quoted = false;
  let started = false;
  for (let i = 0; i < command.length; i += 1) {
    const ch = command[i]!;
    if (quoted) {
      if (ch === "'") quoted = false;
      else current += ch;
    } else if (ch === "'") {
      quoted = true;
      started = true;
    } else if (ch === "\\") {
      current += command[++i] ?? "";
      started = true;
    } else if (ch === " ") {
      if (started) out.push(current);
      current = "";
      started = false;
    } else {
      current += ch;
      started = true;
    }
  }
  if (started) out.push(current);
  return out;
}

export class FakeSandbox implements ProviderSandbox {
  commands: string[] = [];
  /** Files as root sees them: guest scripts and the staging area. */
  disk = new Map<string, Buffer>();
  /** The agent's workspace, keyed by relative path. */
  workspace = new Map<string, Buffer>();
  destroyed = false;

  constructor(
    readonly id: string,
    private readonly provider: FakeProvider,
  ) {}

  async exec(command: string, request: ExecRequest): Promise<ExecOutcome> {
    if (this.destroyed) throw new SandboxGone(this.id);
    this.commands.push(command);
    const [shell, script, ...args] = words(command);
    if (shell !== "bash") throw new Error(`unexpected command: ${command}`);
    if (script?.endsWith("/fence.sh")) return { exitCode: this.provider.fenceExit, timedOut: false };
    if (script?.endsWith("/run.sh")) {
      if (args[0] === "--cancel") return { exitCode: this.provider.cancelExit, timedOut: false };
      return this.provider.run(command, request);
    }
    if (script?.endsWith("/file.sh")) return this.file(args, request);
    throw new Error(`unexpected script: ${command}`);
  }

  private file(args: string[], request: ExecRequest): ExecOutcome {
    const [operation, path = "", stage = ""] = args;
    if (operation === "usage") {
      const total = [...this.workspace.values()].reduce((sum, data) => sum + data.length, 0);
      request.onStdout?.(this.provider.usageOutput ?? `${total}\n`);
      return ok;
    }
    if (operation === "quiesce") return ok;
    if (path.startsWith("link-out")) return { exitCode: 91, timedOut: false };
    if (operation === "write") {
      const data = this.disk.get(stage);
      if (!data) return { exitCode: 1, timedOut: false };
      this.workspace.set(path, data);
      this.disk.delete(stage);
      return ok;
    }
    if (operation === "read") {
      const data = this.workspace.get(path);
      if (!data) return { exitCode: 92, timedOut: false };
      if (data.length > Number(args[3])) return { exitCode: 93, timedOut: false };
      this.disk.set(stage, data);
      return ok;
    }
    if (operation === "discard") {
      this.disk.delete(path);
      return ok;
    }
    return { exitCode: 2, timedOut: false };
  }

  async writeFile(path: string, data: Uint8Array | AsyncIterable<Uint8Array>): Promise<void> {
    if (data instanceof Uint8Array) {
      this.disk.set(path, Buffer.from(data));
      return;
    }
    const chunks: Uint8Array[] = [];
    for await (const chunk of data) chunks.push(chunk);
    this.disk.set(path, Buffer.concat(chunks));
  }

  async readFile(path: string): Promise<AsyncIterable<Uint8Array>> {
    const data = this.disk.get(path);
    if (!data) throw new Error(`no such file ${path}`);
    return (async function* () {
      yield data;
    })();
  }

  async fileSize(path: string): Promise<number> {
    return this.provider.reportedSize ?? this.disk.get(path)?.length ?? 0;
  }

  async checkpoint(name: string): Promise<void> {
    this.commands.push(`checkpoint ${name}`);
    this.provider.saved.set(name, "2026-11-25T00:00:00.000Z");
  }

  async destroy(): Promise<void> {
    if (this.provider.destroyFailures > 0) {
      this.provider.destroyFailures -= 1;
      throw new Error("destroy failed");
    }
    this.destroyed = true;
    this.provider.sandboxes.delete(this.id);
  }
}

export class FakeProvider implements SandboxProvider {
  sandboxes = new Map<string, FakeSandbox>();
  created: FakeSandbox[] = [];
  requests: { checkpoint?: string }[] = [];
  saved = new Map<string, string>();
  fenceExit = 0;
  cancelExit = 0;
  destroyFailures = 0;
  connectDelayMs = 0;
  connects = 0;
  usageOutput: string | undefined;
  reportedSize: number | undefined;
  run: RunHandler = () => ok;
  private counter = 0;

  async prepare(): Promise<void> {}

  async create(from: { checkpoint?: string }): Promise<ProviderSandbox> {
    this.requests.push(from);
    if (from.checkpoint && !this.saved.has(from.checkpoint)) throw new CheckpointNotFound(from.checkpoint);
    const id = `00000000-0000-4000-8000-${String(++this.counter).padStart(12, "0")}`;
    const sandbox = new FakeSandbox(id, this);
    this.sandboxes.set(id, sandbox);
    this.created.push(sandbox);
    return sandbox;
  }

  async connect(id: string): Promise<ProviderSandbox | null> {
    this.connects += 1;
    if (this.connectDelayMs) await new Promise((resolve) => setTimeout(resolve, this.connectDelayMs));
    return this.sandboxes.get(id) ?? null;
  }

  async destroy(id: string): Promise<void> {
    await this.sandboxes.get(id)?.destroy();
  }

  async list(): Promise<SandboxSummary[]> {
    return [...this.sandboxes.keys()].map((id) => ({
      id,
      status: "RUNNING",
      createdAt: "2026-11-25T00:00:00.000Z",
    }));
  }

  async checkpoints(): Promise<CheckpointSummary[]> {
    return [...this.saved].map(([name, createdAt]) => ({ name, createdAt }));
  }

  async deleteCheckpoint(name: string): Promise<void> {
    this.saved.delete(name);
  }
}
