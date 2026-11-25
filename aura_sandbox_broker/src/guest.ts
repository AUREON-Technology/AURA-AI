// Everything the broker does inside a sandbox. The platform runs commands and file operations as
// root, so nothing a caller supplies may reach it unconfined: commands go through `run.sh`, which
// drops to the agent user, and files cross through a root-only staging folder that `file.sh`
// moves in and out of the workspace as the agent user.
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";

import type { ProviderSandbox } from "./provider.js";

/** The same hosts the Docker sandbox allows (`deploy/sandbox/filter`). A test pins the match. */
export const ALLOWED_HOSTS: readonly string[] = [
  "github.com",
  "api.github.com",
  "codeload.github.com",
  "objects.githubusercontent.com",
  "pypi.org",
  "files.pythonhosted.org",
  "registry.npmjs.org",
];
export const GUEST_SCRIPTS: readonly string[] = ["fence.sh", "run.sh", "file.sh", "confine.py"];

const LIB = "/usr/local/lib/aura";
const STAGING = "/var/lib/aura/staging";
/** The transport's own deadline sits this far beyond the one enforced inside the sandbox. */
const TRANSPORT_GRACE_S = 15;
const SCRIPT_TIMEOUT_S = 120;
const FENCE_TIMEOUT_S = 180;

export class FenceError extends Error {}
export class UnknownExit extends Error {}
export class SandboxUnsafe extends Error {}
export class CancelFailed extends Error {}
export class PathRefused extends Error {}
export class FileNotFound extends Error {}
export class FileTooLarge extends Error {}

export function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export function commandLine(commandId: string, timeoutSec: number, argv: readonly string[]): string {
  return `bash ${LIB}/run.sh ${commandId} ${timeoutSec} -- ${argv.map(shellQuote).join(" ")}`;
}

/** Why `path` is not an acceptable workspace-relative file path, or `null` when it is. */
export function relativePathProblem(path: string): string | null {
  if (!path) return "empty path";
  if (Buffer.byteLength(path) > 4096) return "path too long";
  if (path.includes("\u0000")) return "path contains a null byte";
  if (path.startsWith("/")) return "path must be relative to the workspace";
  if (path.split("/").some((part) => part === "" || part === "." || part === "..")) {
    return "path contains an empty, '.' or '..' part";
  }
  return null;
}

/**
 * Install the broker's own copies of the guest scripts and raise the fence. Runs after every
 * boot: a restored checkpoint has neither firewall nor proxy, and whatever is on its disk is not
 * trusted to rebuild them.
 */
export async function bootstrap(sandbox: ProviderSandbox): Promise<void> {
  for (const name of GUEST_SCRIPTS) {
    // dist/src/guest.js -> the package root.
    const source = await readFile(new URL(`../../guest/${name}`, import.meta.url), "utf8");
    await sandbox.writeFile(`${LIB}/${name}`, Buffer.from(source.replaceAll("\r\n", "\n")), 0o755);
  }
  const command = `bash ${LIB}/fence.sh ${ALLOWED_HOSTS.map(shellQuote).join(" ")}`;
  const outcome = await sandbox.exec(command, { timeoutSec: FENCE_TIMEOUT_S });
  if (outcome.exitCode !== 0 || outcome.timedOut) {
    throw new FenceError(`fence exited ${outcome.exitCode}`);
  }
}

export interface RunOptions {
  commandId: string;
  argv: readonly string[];
  timeoutSec: number;
  signal: AbortSignal;
  onStdout: (chunk: string) => void;
  onStderr: (chunk: string) => void;
}

export async function runCommand(
  sandbox: ProviderSandbox,
  options: RunOptions,
): Promise<{ exitCode: number; timedOut: boolean }> {
  // `run.sh` reports these conditions as a marker line carrying a secret it receives in root's
  // environment. The command can read its own id, but not this, so it cannot forge one.
  const secret = randomBytes(16).toString("hex");
  const markers = {
    fenceMissing: `aura:fence-missing:${secret}`,
    timeout: `aura:timeout:${secret}`,
    cleanupFailed: `aura:cleanup-failed:${secret}`,
  };
  const longest = Math.max(...Object.values(markers).map((marker) => marker.length));
  const reported = new Set<string>();
  let carry = "";
  const outcome = await sandbox.exec(commandLine(options.commandId, options.timeoutSec, options.argv), {
    timeoutSec: options.timeoutSec + TRANSPORT_GRACE_S,
    env: { AURA_MARK: secret },
    signal: options.signal,
    onStdout: options.onStdout,
    onStderr: (chunk) => {
      const seen = carry + chunk;
      let shown = chunk;
      for (const marker of Object.values(markers)) {
        if (seen.includes(marker)) reported.add(marker);
        shown = shown.replaceAll(`${marker}\n`, "");
      }
      carry = seen.slice(-longest);
      if (shown) options.onStderr(shown);
    },
  });
  if (reported.has(markers.cleanupFailed)) throw new SandboxUnsafe("a command's processes survived it");
  if (reported.has(markers.fenceMissing) && outcome.exitCode === 97) {
    throw new FenceError("the network fence is missing");
  }
  if (reported.has(markers.timeout)) return { exitCode: 124, timedOut: true };
  // Without the in-sandbox report, a transport deadline or a signal proves nothing ended.
  if (outcome.exitCode === null || outcome.exitCode < 0 || outcome.timedOut) {
    throw new UnknownExit("the command's end was not confirmed");
  }
  return { exitCode: outcome.exitCode, timedOut: false };
}

export async function cancelCommand(sandbox: ProviderSandbox, commandId: string): Promise<void> {
  const outcome = await sandbox.exec(`bash ${LIB}/run.sh --cancel ${commandId}`, { timeoutSec: 30 });
  if (outcome.exitCode !== 0) throw new CancelFailed(`cancel exited ${outcome.exitCode}`);
}

async function fileScript(sandbox: ProviderSandbox, args: string[]): Promise<string> {
  let output = "";
  const outcome = await sandbox.exec(`bash ${LIB}/file.sh ${args.map(shellQuote).join(" ")}`, {
    timeoutSec: SCRIPT_TIMEOUT_S,
    onStdout: (chunk) => {
      output += chunk;
    },
  });
  switch (outcome.exitCode) {
    case 0:
      return output;
    case 91:
      throw new PathRefused("the path leaves the workspace");
    case 92:
      throw new FileNotFound("no such file in the workspace");
    case 93:
      throw new FileTooLarge("the file is larger than the limit");
    default:
      throw new Error(`file operation ${args[0]} exited ${outcome.exitCode}`);
  }
}

const stagingPath = () => `${STAGING}/${randomBytes(16).toString("hex")}`;

async function discard(sandbox: ProviderSandbox, stage: string): Promise<void> {
  // Best effort: the staging folder is also emptied at every boot and before every checkpoint.
  await fileScript(sandbox, ["discard", stage]).catch(() => undefined);
}

export async function writeWorkspaceFile(
  sandbox: ProviderSandbox,
  path: string,
  data: AsyncIterable<Uint8Array>,
): Promise<void> {
  const stage = stagingPath();
  try {
    await sandbox.writeFile(stage, data);
    await fileScript(sandbox, ["write", path, stage]);
  } finally {
    await discard(sandbox, stage);
  }
}

export async function readWorkspaceFile(
  sandbox: ProviderSandbox,
  path: string,
  maxBytes: number,
): Promise<{ size: number; body: AsyncIterable<Uint8Array>; dispose: () => Promise<void> }> {
  const stage = stagingPath();
  const dispose = () => discard(sandbox, stage);
  try {
    await fileScript(sandbox, ["read", path, stage, String(maxBytes)]);
    const size = await sandbox.fileSize(stage);
    return { size, body: await sandbox.readFile(stage), dispose };
  } catch (error) {
    await dispose();
    throw error;
  }
}

export async function workspaceBytes(sandbox: ProviderSandbox): Promise<number> {
  const answer = (await fileScript(sandbox, ["usage"])).trim();
  // An empty answer would otherwise read as zero and pass any quota.
  if (!/^\d+$/.test(answer)) throw new Error("usage returned no number");
  return Number(answer);
}

/** End the agent's processes and flush the disk, so a checkpoint captures a settled workspace. */
export async function quiesce(sandbox: ProviderSandbox): Promise<void> {
  await fileScript(sandbox, ["quiesce"]);
}
