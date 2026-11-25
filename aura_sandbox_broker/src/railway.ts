import { Sandbox, SandboxNotFoundError, type SandboxTemplate } from "railway";

import type { Config } from "./config.js";
import { CheckpointNotFound, SandboxGone, type ProviderSandbox, type SandboxProvider } from "./provider.js";

/** The slice of the official SDK's static surface the broker uses; injectable for tests. */
export type Sdk = Pick<
  typeof Sandbox,
  "template" | "create" | "connect" | "list" | "checkpoints" | "deleteCheckpoint"
>;

// Fixed policy. No caller can change any of it.
const REGION = "us-west2";
const SIZE = { cpu: 2, memoryGB: 2 };
/** A safety net for a crashed caller; Railway destroys an idle sandbox after this. */
const IDLE_MINUTES = 120;
/** The Docker sandbox's tools, plus what the fence needs. The image's own python and node live
 * under /root, out of the agent user's reach, so the system packages are installed. */
const PACKAGES = [
  "curl", "ca-certificates", "openssl", "iproute2", "procps", "util-linux",
  "git", "python3", "python3-pip", "python3-venv", "nodejs", "npm", "build-essential",
];

export function createRailwayProvider(config: Config, sdk: Sdk = Sandbox): SandboxProvider {
  const connection = {
    token: config.railwayToken,
    authType: "project-token" as const,
    // Always explicit: see `loadConfig`.
    environmentId: config.environmentId,
    // Pinned for the same reason: the SDK would read these from the environment, and verbose
    // logging prints request detail.
    endpoint: "https://backboard.railway.com/graphql/v2",
    tcpProxyWsEndpoint: "wss://ssh.railway.com:2226/ws/exec",
    verbose: false,
  };
  // No `env`, no `domains`, and ISOLATED: a sandbox gets no variable, no inbound address and no
  // route to a private network. `region` is added only for the template: the SDK rebuilds the
  // template from the create options, while a checkpoint boots where it was captured.
  const creation = () => ({
    ...connection,
    networkIsolation: "ISOLATED" as const,
    idleTimeoutMinutes: IDLE_MINUTES,
    resources: { ...SIZE },
  });

  let template: Promise<SandboxTemplate> | null = null;
  const built = (): Promise<SandboxTemplate> => {
    template ??= sdk
      .template()
      .withPackages(...PACKAGES)
      .run("id aura >/dev/null 2>&1 || useradd -m -u 10001 -s /bin/bash aura")
      .build({ ...connection, region: REGION })
      .catch((error: unknown) => {
        template = null;
        throw error;
      });
    return template;
  };

  const find = async (name: string) => (await sdk.checkpoints(connection)).find((c) => c.key === name);

  return {
    async prepare() {
      await built();
    },
    async create({ checkpoint }) {
      if (checkpoint === undefined) {
        return wrap(await sdk.create(await built(), { ...creation(), region: REGION }));
      }
      if (!(await find(checkpoint))) throw new CheckpointNotFound(checkpoint);
      return wrap(await sdk.create(checkpoint, creation()));
    },
    async connect(id) {
      try {
        const sandbox = await sdk.connect(id, connection);
        return sandbox.status === "RUNNING" ? wrap(sandbox) : null;
      } catch (error) {
        if (error instanceof SandboxNotFoundError) return null;
        throw error;
      }
    },
    async destroy(id) {
      try {
        // No status filter: a sandbox still being created must be destroyable too.
        const sandbox = await sdk.connect(id, connection);
        if (sandbox.status !== "DESTROYED") await sandbox.destroy();
      } catch (error) {
        if (!(error instanceof SandboxNotFoundError)) throw error;
      }
    },
    async list() {
      return (await sdk.list(connection))
        .filter((row) => row.status !== "DESTROYED")
        .map(({ id, status, createdAt }) => ({ id, status, createdAt }));
    },
    async checkpoints() {
      return (await sdk.checkpoints(connection)).map((c) => ({ name: c.key, createdAt: c.createdAt }));
    },
    async deleteCheckpoint(name) {
      const found = await find(name);
      if (found) await sdk.deleteCheckpoint(found.id, connection);
    },
  };
}

function wrap(sandbox: Sandbox): ProviderSandbox {
  const gone = (error: unknown): never => {
    if (error instanceof SandboxNotFoundError) throw new SandboxGone(sandbox.id);
    throw error;
  };
  return {
    id: sandbox.id,
    async exec(command, { timeoutSec, env, signal, onStdout, onStderr }) {
      try {
        // `ephemeral`: no retained session, and the process group ends if this connection drops.
        const result = await sandbox.exec(command, {
          ephemeral: true,
          captureOutput: false,
          timeoutSec,
          ...(env ? { env } : {}),
          ...(signal ? { signal } : {}),
          ...(onStdout ? { onStdout } : {}),
          ...(onStderr ? { onStderr } : {}),
        });
        return { exitCode: result.exitCode, timedOut: result.timedOut };
      } catch (error) {
        return gone(error);
      }
    },
    async writeFile(path, data, mode) {
      await sandbox.files.write(path, data, mode === undefined ? {} : { mode }).catch(gone);
    },
    async readFile(path) {
      return sandbox.files.read(path, { format: "stream" }).catch(gone);
    },
    async fileSize(path) {
      return (await sandbox.files.stat(path).catch(gone)).size;
    },
    async checkpoint(name) {
      await sandbox.checkpoint(name).catch(gone);
    },
    async destroy() {
      await sandbox.destroy().catch((error: unknown) => {
        if (!(error instanceof SandboxNotFoundError)) throw error;
      });
    },
  };
}
