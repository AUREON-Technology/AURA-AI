import assert from "node:assert/strict";
import { request as httpRequest } from "node:http";
import type { AddressInfo } from "node:net";
import { test } from "node:test";
import { setTimeout as sleep } from "node:timers/promises";

import { createBroker, type Limits } from "../src/server.js";
import { FakeProvider, words } from "./fake.js";

const TOKEN = "t".repeat(40);
const CHECKPOINT = `ws-${"a".repeat(32)}`;
const auth = { authorization: `Bearer ${TOKEN}` };

async function start(limits: Partial<Limits> = {}) {
  const provider = new FakeProvider();
  const logs: Record<string, unknown>[] = [];
  const server = createBroker({ brokerToken: TOKEN, provider, log: (entry) => logs.push(entry), limits });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;
  const base = `http://127.0.0.1:${port}`;
  const call = (method: string, path: string, body?: unknown, headers: Record<string, string> = auth) =>
    fetch(base + path, {
      method,
      headers,
      ...(body === undefined
        ? {}
        : { body: body instanceof Uint8Array ? Buffer.from(body) : JSON.stringify(body) }),
    });
  const sandbox = async () => {
    const response = await call("POST", "/v1/sandboxes", {});
    assert.equal(response.status, 201);
    return ((await response.json()) as { id: string }).id;
  };
  const close = () =>
    new Promise<void>((resolve) => {
      server.closeAllConnections();
      server.close(() => resolve());
    });
  return { provider, logs, call, sandbox, close, port };
}

async function lines(response: Response): Promise<Record<string, unknown>[]> {
  return (await response.text())
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line) as Record<string, unknown>);
}

const errorCode = async (response: Response) => ((await response.json()) as { error: string }).error;

test("health needs no token and says nothing else", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const response = await broker.call("GET", "/health", undefined, {});
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: "ok" });
});

test("every other route refuses a missing or wrong token before doing anything", async (t) => {
  const broker = await start();
  t.after(broker.close);
  for (const headers of [{}, { authorization: "Bearer wrong" }, { authorization: TOKEN }] as Record<string, string>[]) {
    for (const [method, path] of [["POST", "/v1/sandboxes"], ["GET", "/v1/sandboxes"], ["GET", "/nope"]] as const) {
      const response = await broker.call(method, path, method === "POST" ? {} : undefined, headers);
      assert.equal(response.status, 401, `${method} ${path}`);
    }
  }
  assert.equal(broker.provider.created.length, 0);
});

test("create fences the sandbox before handing it out", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  const sandbox = broker.provider.sandboxes.get(id)!;
  assert.match(sandbox.commands[0]!, /fence\.sh/);
  // "inspect:" marks the hosts whose networks also route by the Host header inside TLS.
  assert.deepEqual(words(sandbox.commands[0]!).slice(2), [
    "github.com", "api.github.com", "codeload.github.com", "inspect:objects.githubusercontent.com",
    "inspect:pypi.org", "inspect:files.pythonhosted.org", "registry.npmjs.org",
  ]);
  for (const script of ["fence.sh", "run.sh", "file.sh", "confine.py", "proxy.py"]) {
    assert.ok(sandbox.disk.has(`/usr/local/lib/aura/${script}`), script);
  }
});

test("a sandbox whose fence fails is destroyed and never returned", async (t) => {
  const broker = await start();
  t.after(broker.close);
  broker.provider.fenceExit = 3;
  const response = await broker.call("POST", "/v1/sandboxes", {});
  assert.equal(response.status, 502);
  assert.equal(await errorCode(response), "fence_failed");
  assert.equal(broker.provider.created[0]!.destroyed, true);
});

test("create accepts a checkpoint name and nothing else", async (t) => {
  const broker = await start();
  t.after(broker.close);
  for (const body of [
    { networkIsolation: "PRIVATE" },
    { checkpoint: CHECKPOINT, env: { A: "1" } },
    { resources: { cpu: 24 } },
    { checkpoint: "../x" },
    { checkpoint: "f".repeat(64) },
    [],
    "text",
  ]) {
    const response = await broker.call("POST", "/v1/sandboxes", body);
    assert.equal(response.status, 400, JSON.stringify(body));
  }
  assert.equal(broker.provider.requests.length, 0);
});

test("restoring a missing checkpoint is a 404", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const response = await broker.call("POST", "/v1/sandboxes", { checkpoint: CHECKPOINT });
  assert.equal(response.status, 404);
  assert.equal(await errorCode(response), "checkpoint_not_found");
});

test("a restore is fenced again", async (t) => {
  const broker = await start();
  t.after(broker.close);
  broker.provider.saved.set(CHECKPOINT, "now");
  const response = await broker.call("POST", "/v1/sandboxes", { checkpoint: CHECKPOINT });
  assert.equal(response.status, 201);
  assert.deepEqual(broker.provider.requests, [{ checkpoint: CHECKPOINT }]);
  assert.match(broker.provider.created[0]!.commands[0]!, /fence\.sh/);
});

test("exec streams output and the exit code as lines of JSON", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.run = (_command, request) => {
    request.onStdout?.("hello\n");
    request.onStderr?.("warn\n");
    return { exitCode: 3, timedOut: false };
  };
  const response = await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["ls"], timeout_s: 30 });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/x-ndjson");
  const [started, ...rest] = await lines(response);
  assert.equal(started!.type, "started");
  assert.match(started!.command_id as string, /^[0-9a-f]{32}$/);
  assert.deepEqual(rest, [
    { type: "stdout", data: "hello\n" },
    { type: "stderr", data: "warn\n" },
    { type: "exit", exit_code: 3, timed_out: false, truncated: false },
  ]);
  const run = broker.provider.sandboxes.get(id)!.commands.at(-1)!;
  assert.deepEqual(words(run).slice(1), ["/usr/local/lib/aura/run.sh", started!.command_id, "30", "--", "ls"]);
});

test("the transport deadline is longer than the one enforced inside the sandbox", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  let seen = 0;
  broker.provider.run = (_command, request) => {
    seen = request.timeoutSec;
    return { exitCode: 0, timedOut: false };
  };
  await (await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["ls"], timeout_s: 30 })).text();
  assert.equal(seen, 45);
});

/** The marker `run.sh` would print: its secret arrives in root's environment, not in argv. */
const mark = (request: { env?: Record<string, string> }, kind: string) =>
  `aura:${kind}:${request.env?.AURA_MARK}\n`;

type Broker = Awaited<ReturnType<typeof start>>;

const ran = (broker: Broker, id: string, text: string) =>
  broker.provider.created.find((s) => s.id === id)!.commands.filter((c) => c.includes(text)).length;

async function execOnce(broker: Broker, id: string) {
  const response = await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["x"], timeout_s: 1 });
  return lines(response);
}

test("a command stopped by the in-sandbox deadline is reported as timed out", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  for (const exitCode of [124, 137]) {
    broker.provider.run = (_command, request) => {
      request.onStderr?.(mark(request, "timeout"));
      return { exitCode, timedOut: false };
    };
    const all = await execOnce(broker, id);
    assert.deepEqual([all.at(-1)!.exit_code, all.at(-1)!.timed_out], [124, true], String(exitCode));
    assert.equal(all.filter((line) => line.type === "stderr").length, 0);
  }
});

test("the marker secret is not the command id and is never in the command line", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  let secret = "";
  broker.provider.run = (command, request) => {
    secret = request.env?.AURA_MARK ?? "";
    assert.ok(!command.includes(secret || "no-secret"));
    // What a command can forge after reading its own cgroup name.
    request.onStderr?.(`aura:timeout:${words(command)[2]}\naura:fence-missing:${words(command)[2]}\n`);
    return { exitCode: 97, timedOut: false };
  };
  const all = await execOnce(broker, id);
  assert.match(secret, /^[0-9a-f]{32}$/);
  assert.notEqual(secret, all[0]!.command_id);
  assert.equal(all.at(-1)!.exit_code, 97);
  assert.equal(broker.provider.sandboxes.has(id), true);
});

test("an end the sandbox did not report is unknown, and the command is cancelled", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  for (const outcome of [
    { exitCode: null, timedOut: false },
    { exitCode: null, timedOut: true },
    { exitCode: -1, timedOut: true },
    { exitCode: -1, timedOut: false },
  ]) {
    const before = ran(broker, id, "--cancel");
    broker.provider.run = () => outcome;
    assert.deepEqual((await execOnce(broker, id)).at(-1), { type: "error", error: "exit_unknown" });
    assert.equal(ran(broker, id, "--cancel"), before + 1, JSON.stringify(outcome));
    assert.equal(broker.provider.sandboxes.has(id), true);
  }
});

test("a sandbox whose command cannot be stopped is destroyed", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.cancelExit = 1;
  broker.provider.run = () => ({ exitCode: null, timedOut: true });
  assert.deepEqual((await execOnce(broker, id)).at(-1), { type: "error", error: "exit_unknown" });
  assert.equal(broker.provider.sandboxes.has(id), false);
});

test("a transport failure cancels the command and forgets the cached connection", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.run = () => {
    throw new Error("socket closed");
  };
  const connects = broker.provider.connects;
  assert.deepEqual((await execOnce(broker, id)).at(-1), { type: "error", error: "provider_error" });
  assert.equal(ran(broker, id, "--cancel"), 1);
  broker.provider.run = () => ({ exitCode: 0, timedOut: false });
  await execOnce(broker, id);
  assert.equal(broker.provider.connects, connects + 1);
});

test("a command whose processes could not be ended destroys the sandbox", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.run = (_command, request) => {
    request.onStderr?.(mark(request, "cleanup-failed"));
    return { exitCode: 95, timedOut: false };
  };
  assert.deepEqual((await execOnce(broker, id)).at(-1), { type: "error", error: "sandbox_unsafe" });
  assert.equal(broker.provider.sandboxes.has(id), false);
});

test("large output is capped and flagged", async (t) => {
  const broker = await start({ outputHead: 100, outputTail: 20 });
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.run = (_command, request) => {
    for (let i = 0; i < 50; i += 1) request.onStdout?.("x".repeat(100));
    request.onStdout?.("THE END");
    return { exitCode: 0, timedOut: false };
  };
  const response = await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["x"], timeout_s: 5 });
  const all = await lines(response);
  const text = all.filter((line) => line.type === "stdout").map((line) => line.data).join("");
  assert.ok(text.length < 300, String(text.length));
  assert.ok(text.endsWith("THE END"));
  assert.equal(all.at(-1)!.truncated, true);
});

test("a sandbox that lost its fence is destroyed", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.run = (_command, request) => {
    request.onStderr?.(mark(request, "fence-missing"));
    return { exitCode: 97, timedOut: false };
  };
  const response = await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["x"], timeout_s: 5 });
  assert.deepEqual((await lines(response)).at(-1), { type: "error", error: "fence_missing" });
  assert.equal(broker.provider.sandboxes.has(id), false);
});

test("a command exiting 97 on its own does not destroy anything", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.run = () => ({ exitCode: 97, timedOut: false });
  const response = await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["x"], timeout_s: 5 });
  assert.equal((await lines(response)).at(-1)!.exit_code, 97);
  assert.equal(broker.provider.sandboxes.has(id), true);
});

test("exec refuses a bad request without running anything", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  for (const body of [
    {},
    { command: [], timeout_s: 5 },
    { command: "ls", timeout_s: 5 },
    { command: ["ls", 1], timeout_s: 5 },
    { command: ["a\u0000b"], timeout_s: 5 },
    { command: ["ls"] },
    { command: ["ls"], timeout_s: 0 },
    { command: ["ls"], timeout_s: 901 },
    { command: ["ls"], timeout_s: 1.5 },
    { command: ["ls"], timeout_s: 5, user: "root" },
    { command: ["x".repeat(70_000)], timeout_s: 5 },
  ]) {
    const response = await broker.call("POST", `/v1/sandboxes/${id}/exec`, body);
    assert.equal(response.status, 400, JSON.stringify(body).slice(0, 80));
  }
  assert.equal(broker.provider.sandboxes.get(id)!.commands.length, 1);
});

test("malformed sandbox ids and unknown sandboxes are refused", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const body = { command: ["ls"], timeout_s: 5 };
  assert.equal((await broker.call("POST", "/v1/sandboxes/..%2Fx/exec", body)).status, 404);
  const unknown = await broker.call("POST", "/v1/sandboxes/00000000-0000-4000-8000-999999999999/exec", body);
  assert.equal(unknown.status, 404);
  assert.equal(await errorCode(unknown), "sandbox_not_found");
});

test("hanging up cancels the command inside the sandbox", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  let aborted = false;
  broker.provider.run = (_command, request) =>
    new Promise((resolve) => {
      request.onStdout?.("started\n");
      request.signal?.addEventListener("abort", () => {
        aborted = true;
        resolve({ exitCode: null, timedOut: false });
      });
    });
  await new Promise<void>((resolve, reject) => {
    const body = JSON.stringify({ command: ["sleep", "100"], timeout_s: 100 });
    const outgoing = httpRequest(
      {
        host: "127.0.0.1",
        port: broker.port,
        method: "POST",
        path: `/v1/sandboxes/${id}/exec`,
        headers: { ...auth, "content-length": String(Buffer.byteLength(body)) },
      },
      (incoming) => {
        incoming.once("data", () => {
          outgoing.destroy();
          resolve();
        });
      },
    );
    outgoing.on("error", reject);
    outgoing.end(body);
  });
  for (let i = 0; i < 50 && !broker.provider.sandboxes.get(id)!.commands.some((c) => c.includes("--cancel")); i += 1) {
    await sleep(20);
  }
  assert.equal(aborted, true);
  assert.ok(broker.provider.sandboxes.get(id)!.commands.some((c) => c.includes("--cancel")));
});

test("a command can be cancelled by id", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  const commandId = "c".repeat(32);
  const response = await broker.call("POST", `/v1/sandboxes/${id}/exec/${commandId}/cancel`, {});
  assert.equal(response.status, 204);
  assert.deepEqual(words(broker.provider.sandboxes.get(id)!.commands.at(-1)!).slice(2), ["--cancel", commandId]);
  assert.equal((await broker.call("POST", `/v1/sandboxes/${id}/exec/x;rm/cancel`, {})).status, 404);
});

test("a file goes in through root-only staging and comes back identical", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  const data = Uint8Array.from({ length: 5000 }, (_, i) => i % 256);
  const put = await broker.call("PUT", `/v1/sandboxes/${id}/files?path=bin%2Fblob`, data);
  assert.equal(put.status, 204);
  const sandbox = broker.provider.sandboxes.get(id)!;
  assert.deepEqual(new Uint8Array(sandbox.workspace.get("bin/blob")!), data);
  const write = words(sandbox.commands.find((c) => words(c)[2] === "write")!);
  assert.match(write[4]!, /^\/var\/lib\/aura\/staging\/[0-9a-f]{32}$/);
  const got = await broker.call("GET", `/v1/sandboxes/${id}/files?path=bin%2Fblob`);
  assert.equal(got.status, 200);
  assert.equal(got.headers.get("content-length"), "5000");
  assert.deepEqual(new Uint8Array(await got.arrayBuffer()), data);
  assert.equal([...sandbox.disk.keys()].filter((k) => k.includes("/staging/")).length, 0);
});

test("file paths outside the workspace are refused", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  for (const path of ["%2Fetc%2Fpasswd", "..%2Fx", "a%00b", ""]) {
    assert.equal((await broker.call("GET", `/v1/sandboxes/${id}/files?path=${path}`)).status, 400, path);
    const put = await broker.call("PUT", `/v1/sandboxes/${id}/files?path=${path}`, new Uint8Array([1]));
    assert.equal(put.status, 400, path);
  }
  const escaped = await broker.call("PUT", `/v1/sandboxes/${id}/files?path=link-out`, new Uint8Array([1]));
  assert.equal(escaped.status, 400);
  assert.equal(await errorCode(escaped), "path_refused");
  const sandbox = broker.provider.sandboxes.get(id)!;
  assert.equal([...sandbox.disk.keys()].filter((k) => k.includes("/staging/")).length, 0);
});

test("a missing file is a 404 and an oversized one a 413", async (t) => {
  const broker = await start({ fileBytes: 10 });
  t.after(broker.close);
  const id = await broker.sandbox();
  const missing = await broker.call("GET", `/v1/sandboxes/${id}/files?path=nope`);
  assert.equal(missing.status, 404);
  assert.equal(await errorCode(missing), "file_not_found");
  broker.provider.sandboxes.get(id)!.workspace.set("big", Buffer.alloc(11));
  assert.equal((await broker.call("GET", `/v1/sandboxes/${id}/files?path=big`)).status, 413);
  const put = await broker.call("PUT", `/v1/sandboxes/${id}/files?path=big2`, new Uint8Array(11));
  assert.equal(put.status, 413);
  assert.equal(broker.provider.sandboxes.get(id)!.workspace.has("big2"), false);
});

test("an upload with no declared length is cut off at the limit", async (t) => {
  const broker = await start({ fileBytes: 10 });
  t.after(broker.close);
  const id = await broker.sandbox();
  const status = await new Promise<number>((resolve, reject) => {
    const outgoing = httpRequest(
      { host: "127.0.0.1", port: broker.port, method: "PUT", path: `/v1/sandboxes/${id}/files?path=chunked`, headers: auth },
      (incoming) => {
        incoming.resume();
        resolve(incoming.statusCode ?? 0);
      },
    );
    outgoing.on("error", reject);
    outgoing.write(Buffer.alloc(8));
    outgoing.write(Buffer.alloc(8));
    outgoing.end();
  });
  assert.equal(status, 413);
  assert.equal(broker.provider.sandboxes.get(id)!.workspace.has("chunked"), false);
});

test("usage reports the workspace size", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.sandboxes.get(id)!.workspace.set("a", Buffer.alloc(1234));
  const response = await broker.call("GET", `/v1/sandboxes/${id}/usage`);
  assert.deepEqual(await response.json(), { bytes: 1234 });
});

test("a checkpoint stops the agent's processes first", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  const response = await broker.call("POST", `/v1/sandboxes/${id}/checkpoint`, { name: CHECKPOINT });
  assert.equal(response.status, 204);
  const commands = broker.provider.sandboxes.get(id)!.commands;
  assert.deepEqual(words(commands.at(-2)!).slice(1), ["/usr/local/lib/aura/file.sh", "quiesce"]);
  assert.equal(commands.at(-1), `checkpoint ${CHECKPOINT}`);
  const bad = await broker.call("POST", `/v1/sandboxes/${id}/checkpoint`, { name: "mine" });
  assert.equal(bad.status, 400);
});

test("sandboxes and checkpoints can be listed and removed, idempotently", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  const listed = (await (await broker.call("GET", "/v1/sandboxes")).json()) as { sandboxes: { id: string }[] };
  assert.deepEqual(listed.sandboxes.map((s) => s.id), [id]);
  for (let i = 0; i < 2; i += 1) {
    assert.equal((await broker.call("DELETE", `/v1/sandboxes/${id}`)).status, 204);
  }
  broker.provider.saved.set(CHECKPOINT, "now");
  const saved = await (await broker.call("GET", "/v1/checkpoints")).json();
  assert.deepEqual(saved, { checkpoints: [{ name: CHECKPOINT, createdAt: "now" }] });
  for (let i = 0; i < 2; i += 1) {
    assert.equal((await broker.call("DELETE", `/v1/checkpoints/${CHECKPOINT}`)).status, 204);
  }
  assert.equal(broker.provider.saved.size, 0);
});

test("a vendor failure is reported without its details", async (t) => {
  const broker = await start();
  t.after(broker.close);
  broker.provider.list = async () => {
    throw new Error("secret-token-in-vendor-message");
  };
  const response = await broker.call("GET", "/v1/sandboxes");
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "provider_error" });
});

test("a caller that leaves while the sandbox is looked up runs nothing", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  // A failed command drops the cached connection, so the next one looks the sandbox up again.
  broker.provider.run = () => {
    throw new Error("drop the cache");
  };
  await execOnce(broker, id);
  let runs = 0;
  broker.provider.run = () => {
    runs += 1;
    return { exitCode: 0, timedOut: false };
  };
  broker.provider.connectDelayMs = 150;
  const controller = new AbortController();
  const request = fetch(`http://127.0.0.1:${broker.port}/v1/sandboxes/${id}/exec`, {
    method: "POST",
    headers: auth,
    body: JSON.stringify({ command: ["x"], timeout_s: 5 }),
    signal: controller.signal,
  }).catch(() => undefined);
  await sleep(50);
  controller.abort();
  await request;
  await sleep(250);
  assert.equal(runs, 0);
});

test("cancelling by id also ends the transport of a running command", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  let aborted = false;
  broker.provider.run = (_command, request) =>
    new Promise((resolve) => {
      request.signal?.addEventListener("abort", () => {
        aborted = true;
        resolve({ exitCode: null, timedOut: false });
      });
    });
  const response = await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["x"], timeout_s: 30 });
  const reader = response.body!.getReader();
  const first = new TextDecoder().decode((await reader.read()).value);
  const commandId = (JSON.parse(first.split("\n")[0]!) as { command_id: string }).command_id;
  const cancelled = await broker.call("POST", `/v1/sandboxes/${id}/exec/${commandId}/cancel`, {});
  assert.equal(cancelled.status, 204);
  await sleep(50);
  assert.equal(aborted, true);
  await reader.cancel();
});

test("a cancel that did not work says so", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.cancelExit = 1;
  const response = await broker.call("POST", `/v1/sandboxes/${id}/exec/${"c".repeat(32)}/cancel`, {});
  assert.equal(response.status, 502);
  assert.equal(await errorCode(response), "cancel_failed");
});

test("delete destroys a sandbox that is not running", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = "00000000-0000-4000-8000-00000000abcd";
  const destroyed: string[] = [];
  broker.provider.destroy = async (value: string) => {
    destroyed.push(value);
  };
  assert.equal((await broker.call("DELETE", `/v1/sandboxes/${id}`)).status, 204);
  assert.deepEqual(destroyed, [id]);
});

test("destroying an unsafe sandbox is retried", async (t) => {
  const broker = await start({ destroyRetryMs: 1 });
  t.after(broker.close);
  broker.provider.fenceExit = 11;
  broker.provider.destroyFailures = 2;
  assert.equal((await broker.call("POST", "/v1/sandboxes", {})).status, 502);
  assert.equal(broker.provider.created[0]!.destroyed, true);
});

test("a usage answer that is not a number is an error, not zero", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  for (const output of ["", "\n", "du: cannot read\n", "-1\n", "1e9\n"]) {
    broker.provider.usageOutput = output;
    assert.equal((await broker.call("GET", `/v1/sandboxes/${id}/usage`)).status, 502, JSON.stringify(output));
  }
});

test("a download that does not match its declared length breaks the connection", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.sandboxes.get(id)!.workspace.set("f", Buffer.from("0123456789"));
  for (const size of [4, 20]) {
    broker.provider.reportedSize = size;
    await assert.rejects(async () => {
      const response = await broker.call("GET", `/v1/sandboxes/${id}/files?path=f`);
      await response.arrayBuffer();
    }, String(size));
  }
});

test("nothing runs while a checkpoint is being taken", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  let release: (() => void) | undefined;
  broker.provider.sandboxes.get(id)!.checkpoint = () => new Promise<void>((resolve) => (release = resolve));
  const saving = broker.call("POST", `/v1/sandboxes/${id}/checkpoint`, { name: CHECKPOINT });
  while (!release) await sleep(5);
  const busy = await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["x"], timeout_s: 5 });
  assert.equal(busy.status, 409);
  assert.equal(await errorCode(busy), "sandbox_busy");
  const put = await broker.call("PUT", `/v1/sandboxes/${id}/files?path=f`, new Uint8Array([1]));
  assert.equal(put.status, 409);
  release();
  assert.equal((await saving).status, 204);
  const after = await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["x"], timeout_s: 5 });
  assert.equal(after.status, 200);
  await after.text();
});

test("logs carry ids and outcomes, never commands, paths, content or tokens", async (t) => {
  const broker = await start();
  t.after(broker.close);
  const id = await broker.sandbox();
  broker.provider.run = (_command, request) => {
    request.onStdout?.("OUTPUT-SECRET");
    return { exitCode: 0, timedOut: false };
  };
  await (await broker.call("POST", `/v1/sandboxes/${id}/exec`, { command: ["echo", "ARG-SECRET"], timeout_s: 5 })).text();
  await broker.call("PUT", `/v1/sandboxes/${id}/files?path=PATH-SECRET`, new TextEncoder().encode("CONTENT-SECRET"));
  await broker.call("GET", "/v1/sandboxes", undefined, { authorization: "Bearer GUESS-SECRET" });
  const text = JSON.stringify(broker.logs);
  assert.ok(text.includes(id));
  assert.doesNotMatch(text, /SECRET/);
  assert.ok(!text.includes(TOKEN));
});
