// The broker's private HTTP API. Callers name a sandbox, a command, a file or a checkpoint;
// they choose nothing about how a sandbox is made. Logs carry ids and outcomes only.
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { once } from "node:events";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { setTimeout as delay } from "node:timers/promises";

import {
  CancelFailed,
  FenceError,
  FileNotFound,
  FileTooLarge,
  PathRefused,
  SandboxUnsafe,
  UnknownExit,
  bootstrap,
  cancelCommand,
  quiesce,
  readWorkspaceFile,
  relativePathProblem,
  runCommand,
  workspaceBytes,
  writeWorkspaceFile,
} from "./guest.js";
import { CappedOutput } from "./output.js";
import { CheckpointNotFound, SandboxGone, type ProviderSandbox, type SandboxProvider } from "./provider.js";

export interface Limits {
  outputHead: number;
  outputTail: number;
  fileBytes: number;
  destroyRetryMs: number;
}

export interface BrokerOptions {
  brokerToken: string;
  provider: SandboxProvider;
  log: (entry: Record<string, unknown>) => void;
  limits?: Partial<Limits>;
}

const MIB = 1024 * 1024;
const DEFAULT_LIMITS: Limits = {
  outputHead: 3 * MIB,
  outputTail: MIB,
  fileBytes: 100 * MIB,
  destroyRetryMs: 2000,
};
const DESTROY_ATTEMPTS = 4;
const MAX_JSON_BYTES = 256 * 1024;
const MAX_ARGV_BYTES = 64 * 1024;
const MAX_ARGS = 1000;
const MAX_TIMEOUT_S = 900;
const SANDBOX_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const COMMAND_ID = /^[0-9a-f]{32}$/;
/** One per saved Engineer workspace. The prefix also avoids Railway's reserved 64-hex names. */
const CHECKPOINT = /^ws-[0-9a-f]{32}$/;

class HttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}

function asHttpError(error: unknown): HttpError {
  if (error instanceof HttpError) return error;
  if (error instanceof PathRefused) return new HttpError(400, "path_refused");
  if (error instanceof FileNotFound) return new HttpError(404, "file_not_found");
  if (error instanceof FileTooLarge) return new HttpError(413, "file_too_large");
  if (error instanceof CheckpointNotFound) return new HttpError(404, "checkpoint_not_found");
  if (error instanceof SandboxGone) return new HttpError(404, "sandbox_not_found");
  if (error instanceof CancelFailed) return new HttpError(502, "cancel_failed");
  return new HttpError(502, "provider_error");
}

const errorName = (error: unknown) => (error instanceof Error ? error.constructor.name : "unknown");

function send(response: ServerResponse, status: number, body?: unknown): void {
  if (body === undefined) {
    response.writeHead(status).end();
    return;
  }
  const text = JSON.stringify(body);
  response.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(text),
  });
  response.end(text);
}

/** A JSON object with only the `allowed` keys: an unknown setting is refused, not ignored. */
async function readJson(request: IncomingMessage, allowed: string[]): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request as AsyncIterable<Buffer>) {
    size += chunk.length;
    if (size <= MAX_JSON_BYTES) chunks.push(chunk);
  }
  if (size > MAX_JSON_BYTES) throw new HttpError(400, "invalid_request");
  let value: unknown;
  try {
    value = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  } catch {
    throw new HttpError(400, "invalid_request");
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new HttpError(400, "invalid_request");
  }
  if (Object.keys(value).some((key) => !allowed.includes(key))) throw new HttpError(400, "invalid_request");
  return value as Record<string, unknown>;
}

function checkpointName(value: unknown): string {
  if (typeof value !== "string" || !CHECKPOINT.test(value)) throw new HttpError(400, "invalid_request");
  return value;
}

function commandRequest(body: Record<string, unknown>): { argv: string[]; timeoutSec: number } {
  const { command, timeout_s: timeoutSec } = body;
  const valid =
    Array.isArray(command) &&
    command.length > 0 &&
    command.length <= MAX_ARGS &&
    command.every((part) => typeof part === "string" && !part.includes("\u0000")) &&
    command.reduce((sum: number, part: string) => sum + Buffer.byteLength(part), 0) <= MAX_ARGV_BYTES &&
    Number.isInteger(timeoutSec) &&
    (timeoutSec as number) >= 1 &&
    (timeoutSec as number) <= MAX_TIMEOUT_S;
  if (!valid) throw new HttpError(400, "invalid_request");
  return { argv: command as string[], timeoutSec: timeoutSec as number };
}

export function createBroker(options: BrokerOptions): Server {
  const { provider, log } = options;
  const limits: Limits = { ...DEFAULT_LIMITS, ...options.limits };
  const digest = (value: string) => createHash("sha256").update(value).digest();
  const expected = digest(`Bearer ${options.brokerToken}`);
  /** Connections already looked up. A cache only: losing it costs one API call. */
  const known = new Map<string, ProviderSandbox>();
  /** Commands in progress, so a cancel can end the transport too. */
  const running = new Map<string, AbortController>();
  /** Sandboxes being checkpointed: nothing may change their disk meanwhile. */
  const saving = new Set<string>();

  async function find(id: string): Promise<ProviderSandbox | null> {
    if (!SANDBOX_ID.test(id)) throw new HttpError(404, "sandbox_not_found");
    const cached = known.get(id);
    if (cached) return cached;
    const sandbox = await provider.connect(id);
    if (sandbox) known.set(id, sandbox);
    return sandbox;
  }

  async function require(id: string): Promise<ProviderSandbox> {
    const sandbox = await find(id);
    if (!sandbox) throw new HttpError(404, "sandbox_not_found");
    return sandbox;
  }

  /** Do something with a sandbox, forgetting a connection that turned out to be bad. */
  async function using<T>(id: string, action: (sandbox: ProviderSandbox) => Promise<T>): Promise<T> {
    const sandbox = await require(id);
    try {
      return await action(sandbox);
    } catch (error) {
      if (["provider_error", "sandbox_not_found"].includes(asHttpError(error).code)) known.delete(id);
      throw error;
    }
  }

  /**
   * Destroy a sandbox that must not be used, retrying, and logging rather than hiding a failure.
   * One that survives is still unusable: it has no ready mark, or its caller was told to stop.
   */
  async function discardUnsafe(sandbox: ProviderSandbox, why: string): Promise<void> {
    log({ event: why, sandbox_id: sandbox.id });
    known.delete(sandbox.id);
    for (let attempt = 1; attempt <= DESTROY_ATTEMPTS; attempt += 1) {
      try {
        await provider.destroy(sandbox.id);
        return;
      } catch (error) {
        log({ event: "destroy_failed", sandbox_id: sandbox.id, attempt, error: errorName(error) });
        if (attempt < DESTROY_ATTEMPTS) await delay(limits.destroyRetryMs);
      }
    }
  }

  const refuseWhileSaving = (id: string) => {
    if (saving.has(id)) throw new HttpError(409, "sandbox_busy");
  };

  async function create(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const body = await readJson(request, ["checkpoint"]);
    const from = body.checkpoint === undefined ? {} : { checkpoint: checkpointName(body.checkpoint) };
    const sandbox = await provider.create(from);
    try {
      await bootstrap(sandbox);
    } catch (error) {
      log({ event: "fence_error", sandbox_id: sandbox.id, error: errorName(error) });
      await discardUnsafe(sandbox, "fence_failed");
      throw new HttpError(502, "fence_failed");
    }
    // A caller that gave up will never learn this id, so nobody would ever destroy it.
    if (response.destroyed) {
      await discardUnsafe(sandbox, "create_abandoned");
      return;
    }
    known.set(sandbox.id, sandbox);
    log({ event: "sandbox_created", sandbox_id: sandbox.id, restored: "checkpoint" in from });
    send(response, 201, { id: sandbox.id });
  }

  async function exec(request: IncomingMessage, response: ServerResponse, id: string): Promise<void> {
    const commandId = randomBytes(16).toString("hex");
    const controller = new AbortController();
    let sandbox: ProviderSandbox | undefined;
    let cancelling: Promise<boolean> | undefined;
    /** End the command inside the sandbox, once. False when that could not be confirmed. */
    const stop = (): Promise<boolean> =>
      (cancelling ??= cancelCommand(sandbox!, commandId).then(
        () => true,
        (error: unknown) => {
          log({ event: "cancel_failed", sandbox_id: id, command_id: commandId, error: errorName(error) });
          return false;
        },
      ));
    // Listen before the first await: a caller that leaves during it must still be noticed.
    response.on("close", () => {
      if (response.writableFinished) return;
      controller.abort();
      if (sandbox) void stop();
    });

    const { argv, timeoutSec } = commandRequest(await readJson(request, ["command", "timeout_s"]));
    refuseWhileSaving(id);
    const found = await require(id);
    if (controller.signal.aborted || response.destroyed) return;
    refuseWhileSaving(id);
    sandbox = found;
    const started = Date.now();
    const line = (value: Record<string, unknown>) => {
      if (!response.destroyed) response.write(`${JSON.stringify(value)}\n`);
    };
    const stream = (type: "stdout" | "stderr") =>
      new CappedOutput(limits.outputHead, limits.outputTail, (data) => line({ type, data }));
    const stdout = stream("stdout");
    const stderr = stream("stderr");

    response.writeHead(200, { "content-type": "application/x-ndjson", "cache-control": "no-store" });
    running.set(commandId, controller);
    line({ type: "started", command_id: commandId });
    try {
      const outcome = await runCommand(sandbox, {
        commandId,
        argv,
        timeoutSec,
        signal: controller.signal,
        onStdout: (chunk) => stdout.push(chunk),
        onStderr: (chunk) => stderr.push(chunk),
      });
      const truncated = [stdout.finish(), stderr.finish()].includes(true);
      line({ type: "exit", exit_code: outcome.exitCode, timed_out: outcome.timedOut, truncated });
      log({
        event: "command_finished", sandbox_id: id, command_id: commandId,
        exit_code: outcome.exitCode, timed_out: outcome.timedOut, ms: Date.now() - started,
      });
    } catch (error) {
      stdout.finish();
      stderr.finish();
      let code = asHttpError(error).code;
      if (error instanceof FenceError) {
        code = "fence_missing";
        await discardUnsafe(sandbox, code);
      } else if (error instanceof SandboxUnsafe) {
        code = "sandbox_unsafe";
        await discardUnsafe(sandbox, code);
      } else if (error instanceof SandboxGone) {
        known.delete(id);
      } else {
        // The command may still be running: end it, or end the sandbox.
        if (error instanceof UnknownExit || controller.signal.aborted) code = "exit_unknown";
        else known.delete(id);
        if (!(await stop())) await discardUnsafe(sandbox, "command_not_stopped");
      }
      line({ type: "error", error: code });
      log({ event: "command_failed", sandbox_id: id, command_id: commandId, error: code, cause: errorName(error) });
    } finally {
      running.delete(commandId);
    }
    response.end();
  }

  async function putFile(
    request: IncomingMessage, response: ServerResponse, id: string, path: string,
  ): Promise<void> {
    if (Number(request.headers["content-length"] ?? 0) > limits.fileBytes) {
      throw new HttpError(413, "file_too_large");
    }
    refuseWhileSaving(id);
    let received = 0;
    let tooLarge = false;
    async function* bounded(): AsyncIterable<Uint8Array> {
      // Leaving the loop must not destroy the request, or the refusal could not be sent.
      for await (const chunk of request.iterator({ destroyOnReturn: false }) as AsyncIterable<Buffer>) {
        received += chunk.length;
        if (received > limits.fileBytes) {
          tooLarge = true;
          throw new FileTooLarge("upload over the limit");
        }
        yield chunk;
      }
    }
    try {
      await using(id, (sandbox) => writeWorkspaceFile(sandbox, path, bounded()));
    } catch (error) {
      // The vendor's client may wrap the error thrown from the stream; the flag does not lie.
      throw tooLarge ? new HttpError(413, "file_too_large") : error;
    }
    send(response, 204);
  }

  async function getFile(response: ServerResponse, id: string, path: string): Promise<void> {
    refuseWhileSaving(id);
    await using(id, async (sandbox) => {
      const file = await readWorkspaceFile(sandbox, path, limits.fileBytes);
      try {
        response.writeHead(200, { "content-type": "application/octet-stream", "content-length": file.size });
        // A body longer or shorter than declared would corrupt the next response on this
        // connection, so the connection is broken instead.
        let sent = 0;
        for await (const chunk of file.body) {
          sent += chunk.length;
          if (response.destroyed || sent > file.size) break;
          if (!response.write(chunk)) await Promise.race([once(response, "drain"), once(response, "close")]);
        }
        if (sent === file.size) response.end();
        else response.destroy();
      } finally {
        await file.dispose();
      }
    });
  }

  async function route(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const url = new URL(request.url ?? "/", "http://broker");
    const method = request.method ?? "GET";
    if (method === "GET" && url.pathname === "/health") return send(response, 200, { status: "ok" });
    if (!timingSafeEqual(digest(request.headers.authorization ?? ""), expected)) {
      throw new HttpError(401, "unauthorized");
    }
    const [version, collection, id, action, commandId, verb, ...extra] = url.pathname.split("/").slice(1);
    if (version !== "v1" || extra.length > 0) throw new HttpError(404, "not_found");

    if (collection === "checkpoints" && action === undefined) {
      if (id === undefined && method === "GET") {
        return send(response, 200, { checkpoints: await provider.checkpoints() });
      }
      if (id !== undefined && method === "DELETE") {
        await provider.deleteCheckpoint(checkpointName(id));
        log({ event: "checkpoint_deleted" });
        return send(response, 204);
      }
    }
    if (collection !== "sandboxes") throw new HttpError(404, "not_found");
    if (id === undefined) {
      if (method === "GET") return send(response, 200, { sandboxes: await provider.list() });
      if (method === "POST") return create(request, response);
      throw new HttpError(404, "not_found");
    }
    if (action === undefined && method === "DELETE") {
      if (!SANDBOX_ID.test(id)) throw new HttpError(404, "sandbox_not_found");
      known.delete(id);
      await provider.destroy(id);
      log({ event: "sandbox_destroyed", sandbox_id: id });
      return send(response, 204);
    }
    if (action === "exec" && commandId === undefined && method === "POST") return exec(request, response, id);
    if (action === "exec" && verb === "cancel" && method === "POST") {
      if (!COMMAND_ID.test(commandId ?? "")) throw new HttpError(404, "not_found");
      try {
        // Inside the sandbox first, so the command's own end can still be reported.
        await using(id, (sandbox) => cancelCommand(sandbox, commandId!));
      } finally {
        running.get(commandId!)?.abort();
      }
      return send(response, 204);
    }
    if (commandId !== undefined) throw new HttpError(404, "not_found");
    if (action === "files" && (method === "GET" || method === "PUT")) {
      const path = url.searchParams.get("path") ?? "";
      if (relativePathProblem(path) !== null) throw new HttpError(400, "invalid_request");
      return method === "GET" ? getFile(response, id, path) : putFile(request, response, id, path);
    }
    if (action === "usage" && method === "GET") {
      return send(response, 200, { bytes: await using(id, workspaceBytes) });
    }
    if (action === "checkpoint" && method === "POST") {
      const name = checkpointName((await readJson(request, ["name"])).name);
      refuseWhileSaving(id);
      saving.add(id);
      try {
        await using(id, async (sandbox) => {
          await quiesce(sandbox);
          await sandbox.checkpoint(name);
        });
      } finally {
        saving.delete(id);
      }
      log({ event: "checkpoint_saved", sandbox_id: id });
      return send(response, 204);
    }
    throw new HttpError(404, "not_found");
  }

  return createServer((request, response) => {
    route(request, response).catch((error: unknown) => {
      const refusal = asHttpError(error);
      // The vendor's message can quote paths or credentials, so only the error's type is logged.
      log({ event: "request_refused", status: refusal.status, error: refusal.code, cause: errorName(error) });
      request.resume();
      if (response.headersSent) response.destroy();
      else send(response, refusal.status, { error: refusal.code });
    });
  });
}
