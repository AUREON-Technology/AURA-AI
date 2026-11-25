import assert from "node:assert/strict";
import { test } from "node:test";

import { SandboxNotFoundError } from "railway";

import type { Config } from "../src/config.js";
import { CheckpointNotFound } from "../src/provider.js";
import { createRailwayProvider, type Sdk } from "../src/railway.js";

const config: Config = {
  brokerToken: "b".repeat(40),
  railwayToken: "railway-project-token",
  environmentId: "ecb2b832-09ee-4a60-b529-ddd73b4db9a0",
  port: 8080,
};
// Every endpoint is pinned: the SDK would otherwise read them from the environment.
const connection = {
  token: "railway-project-token",
  authType: "project-token",
  environmentId: config.environmentId,
  endpoint: "https://backboard.railway.com/graphql/v2",
  tcpProxyWsEndpoint: "wss://ssh.railway.com:2226/ws/exec",
  verbose: false,
};

type Call = { name: string; args: unknown[] };

function fakeSdk() {
  const calls: Call[] = [];
  const steps: unknown[][] = [];
  const template = {
    withPackages(...packages: string[]) {
      steps.push(["packages", ...packages]);
      return template;
    },
    run(command: string) {
      steps.push(["run", command]);
      return template;
    },
    async build(options: unknown) {
      calls.push({ name: "build", args: [options] });
      return template;
    },
  };
  const execs: { command: string; options: Record<string, unknown> }[] = [];
  const sandbox = {
    id: "sandbox-1",
    status: "RUNNING",
    exec(command: string, options: Record<string, unknown>) {
      execs.push({ command, options });
      return Promise.resolve({ exitCode: 0, timedOut: false, stdout: "", stderr: "", truncated: false });
    },
    async destroy() {},
  };
  const sdk = {
    template: () => template,
    async create(...args: unknown[]) {
      calls.push({ name: "create", args });
      return sandbox;
    },
    async connect(id: string, options: unknown) {
      calls.push({ name: "connect", args: [id, options] });
      if (id === "gone") throw new SandboxNotFoundError({ id, environmentId: config.environmentId });
      return sandbox;
    },
    async list(options: unknown) {
      calls.push({ name: "list", args: [options] });
      return [
        { id: "a", status: "RUNNING", createdAt: "t1" },
        { id: "b", status: "DESTROYED", createdAt: "t2" },
      ];
    },
    async checkpoints(options: unknown) {
      calls.push({ name: "checkpoints", args: [options] });
      return [{ id: "ck-1", key: "ws-known", createdAt: "t3", environmentId: config.environmentId }];
    },
    async deleteCheckpoint(id: string, options: unknown) {
      calls.push({ name: "deleteCheckpoint", args: [id, options] });
    },
  };
  return { sdk: sdk as unknown as Sdk, calls, steps, execs, template };
}

const fixed = {
  ...connection,
  networkIsolation: "ISOLATED",
  idleTimeoutMinutes: 120,
  resources: { cpu: 2, memoryGB: 2 },
};

test("a fresh sandbox boots from the template with fixed, isolated settings", async () => {
  const { sdk, calls, template } = fakeSdk();
  await createRailwayProvider(config, sdk).create({});
  const create = calls.find((c) => c.name === "create")!;
  assert.equal(create.args[0], template);
  // The SDK rebuilds the template from these options: without the region it boots elsewhere.
  assert.deepEqual(create.args[1], { ...fixed, region: "us-west2" });
});

test("a restore boots from the checkpoint with the same fixed settings and no region", async () => {
  const { sdk, calls } = fakeSdk();
  await createRailwayProvider(config, sdk).create({ checkpoint: "ws-known" });
  const create = calls.find((c) => c.name === "create")!;
  assert.deepEqual(create.args, ["ws-known", fixed]);
});

test("restoring a checkpoint that does not exist is its own error", async () => {
  const { sdk, calls } = fakeSdk();
  await assert.rejects(
    createRailwayProvider(config, sdk).create({ checkpoint: "ws-absent" }),
    CheckpointNotFound,
  );
  assert.equal(calls.filter((c) => c.name === "create").length, 0);
});

test("the template is built once, in the fixed region, with the tools the agent needs", async () => {
  const { sdk, calls, steps } = fakeSdk();
  const provider = createRailwayProvider(config, sdk);
  await Promise.all([provider.prepare(), provider.prepare(), provider.create({})]);
  const builds = calls.filter((c) => c.name === "build");
  assert.equal(builds.length, 1);
  assert.deepEqual(builds[0]!.args[0], { ...connection, region: "us-west2" });
  const packages = steps.find((s) => s[0] === "packages")!;
  for (const name of ["git", "python3", "nodejs", "npm", "build-essential", "curl", "openssl"]) {
    assert.ok(packages.includes(name), name);
  }
  // The proxy is the broker's own: a package that forwards by host name alone must not return.
  assert.ok(!packages.includes("tinyproxy"));
});

test("a failed template build is retried on the next use", async () => {
  const { sdk, calls, template } = fakeSdk();
  let failures = 1;
  const build = template.build.bind(template);
  template.build = async (options: unknown) => {
    if (failures-- > 0) throw new Error("build failed");
    return build(options);
  };
  const provider = createRailwayProvider(config, sdk);
  await assert.rejects(provider.prepare());
  await provider.prepare();
  assert.equal(calls.filter((c) => c.name === "build").length, 1);
});

test("commands run without a durable session or captured copy, under the given deadline", async () => {
  const { sdk, execs } = fakeSdk();
  const sandbox = (await createRailwayProvider(config, sdk).connect("sandbox-1"))!;
  const controller = new AbortController();
  const outcome = await sandbox.exec("true", { timeoutSec: 7, signal: controller.signal });
  assert.deepEqual(outcome, { exitCode: 0, timedOut: false });
  const options = execs[0]!.options;
  assert.equal(options.ephemeral, true);
  assert.equal(options.captureOutput, false);
  assert.equal(options.timeoutSec, 7);
  assert.equal(options.signal, controller.signal);
  assert.equal("env" in options, false);
});

test("the marker secret travels in the command's environment", async () => {
  const { sdk, execs } = fakeSdk();
  const sandbox = (await createRailwayProvider(config, sdk).connect("sandbox-1"))!;
  await sandbox.exec("true", { timeoutSec: 7, env: { AURA_MARK: "m" } });
  assert.deepEqual(execs[0]!.options.env, { AURA_MARK: "m" });
});

test("destroying by id works whatever the status, and a missing sandbox is fine", async () => {
  const { sdk, calls } = fakeSdk();
  const provider = createRailwayProvider(config, sdk);
  await provider.destroy("sandbox-1");
  await provider.destroy("gone");
  assert.deepEqual(calls.filter((c) => c.name === "connect").map((c) => c.args[0]), ["sandbox-1", "gone"]);
});

test("an unknown sandbox connects as null", async () => {
  const { sdk, calls } = fakeSdk();
  assert.equal(await createRailwayProvider(config, sdk).connect("gone"), null);
  assert.deepEqual(calls[0]!.args, ["gone", connection]);
});

test("listing leaves out destroyed sandboxes and names the environment", async () => {
  const { sdk, calls } = fakeSdk();
  const rows = await createRailwayProvider(config, sdk).list();
  assert.deepEqual(rows, [{ id: "a", status: "RUNNING", createdAt: "t1" }]);
  assert.deepEqual(calls[0]!.args, [connection]);
});

test("checkpoints are deleted by name, and deleting an unknown one succeeds", async () => {
  const { sdk, calls } = fakeSdk();
  const provider = createRailwayProvider(config, sdk);
  await provider.deleteCheckpoint("ws-known");
  await provider.deleteCheckpoint("ws-absent");
  const deletes = calls.filter((c) => c.name === "deleteCheckpoint");
  assert.deepEqual(deletes.map((c) => c.args), [["ck-1", connection]]);
});
