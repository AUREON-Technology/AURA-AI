import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { loadConfig } from "../src/config.js";
import { ALLOWED_HOSTS, GUEST_SCRIPTS, commandLine, relativePathProblem, shellQuote } from "../src/guest.js";
import { CappedOutput } from "../src/output.js";
import { words } from "./fake.js";

// Compiled tests live in dist/test, so the package root is two levels up.
const root = new URL("../../", import.meta.url);
const repoFile = (path: string) => fileURLToPath(new URL(`../${path}`, root));

const good = {
  AURA_BROKER_TOKEN: "b".repeat(40),
  AURA_SANDBOX_RAILWAY_TOKEN: "railway-project-token",
  AURA_SANDBOX_ENVIRONMENT_ID: "ecb2b832-09ee-4a60-b529-ddd73b4db9a0",
  RAILWAY_ENVIRONMENT_ID: "a9a791f3-3ffe-49a6-8760-e5594a9742ec",
};

test("config accepts a complete environment", () => {
  const config = loadConfig(good);
  assert.equal(config.environmentId, good.AURA_SANDBOX_ENVIRONMENT_ID);
  assert.equal(config.port, 8080);
});

test("config refuses to create sandboxes in the broker's own environment", () => {
  assert.throws(
    () => loadConfig({ ...good, AURA_SANDBOX_ENVIRONMENT_ID: good.RAILWAY_ENVIRONMENT_ID }),
    /own environment/,
  );
});

test("config refuses a short, missing or reused secret and a malformed id", () => {
  assert.throws(() => loadConfig({ ...good, AURA_BROKER_TOKEN: "short" }), /AURA_BROKER_TOKEN/);
  assert.throws(() => loadConfig({ ...good, AURA_SANDBOX_RAILWAY_TOKEN: "" }), /RAILWAY_TOKEN/);
  assert.throws(
    () => loadConfig({ ...good, AURA_SANDBOX_RAILWAY_TOKEN: good.AURA_BROKER_TOKEN }),
    /different/,
  );
  assert.throws(() => loadConfig({ ...good, AURA_SANDBOX_ENVIRONMENT_ID: "production" }), /ENVIRONMENT_ID/);
  assert.throws(() => loadConfig({ ...good, PORT: "70000" }), /PORT/);
});

test("config never falls back to the variables Railway injects or the SDK reads", () => {
  const { AURA_SANDBOX_ENVIRONMENT_ID: _id, AURA_SANDBOX_RAILWAY_TOKEN: _token, ...rest } = good;
  assert.throws(
    () => loadConfig({ ...rest, RAILWAY_TOKEN: "x", RAILWAY_API_TOKEN: "y" }),
    /AURA_SANDBOX_RAILWAY_TOKEN/,
  );
});

test("output under the cap is passed through unchanged", () => {
  const seen: string[] = [];
  const output = new CappedOutput(10, 5, (chunk) => seen.push(chunk));
  output.push("abc");
  output.push("def");
  assert.equal(output.finish(), false);
  assert.equal(seen.join(""), "abcdef");
});

test("output over the cap keeps the start and the end and says what was dropped", () => {
  const seen: string[] = [];
  const output = new CappedOutput(10, 5, (chunk) => seen.push(chunk));
  output.push("0123456789ABCDEF");
  output.push("GHIJKLMNOPQRSTUVWXYZ");
  assert.equal(output.finish(), true);
  const text = seen.join("");
  assert.ok(text.startsWith("0123456789"), text);
  assert.ok(text.endsWith("VWXYZ"), text);
  assert.match(text, /\[aura\] 21 characters omitted/);
});

test("output that fits head plus tail is not marked truncated", () => {
  const seen: string[] = [];
  const output = new CappedOutput(10, 5, (chunk) => seen.push(chunk));
  output.push("0123456789ABCDE");
  assert.equal(output.finish(), false);
  assert.equal(seen.join(""), "0123456789ABCDE");
});

test("the allowed hosts are exactly the Docker sandbox's allowlist", () => {
  const filter = readFileSync(repoFile("deploy/sandbox/filter"), "utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim() && !line.startsWith("#"))
    .map((line) => line.replace(/^\^|\$$/g, "").replaceAll("\\.", "."));
  assert.deepEqual([...ALLOWED_HOSTS], filter);
});

test("many small writes over the cap stay cheap", () => {
  const output = new CappedOutput(10, 1024 * 1024, () => {});
  const started = Date.now();
  for (let i = 0; i < 200_000; i += 1) output.push("xxxxxxxx");
  assert.equal(output.finish(), true);
  assert.ok(Date.now() - started < 2000, `${Date.now() - started} ms`);
});

const hostile = [
  "plain",
  "two words",
  "quote'inside",
  "''",
  "$(touch /tmp/owned)",
  "`id`",
  "a;b&&c|d>e<f",
  "line\nbreak",
  "tab\there",
  "back\\slash",
  "*?[x]",
  "~root",
  "--",
  "-rf",
  "",
  "ünïcode ✓",
];

const gitBash = "C:\\Program Files\\Git\\bin\\bash.exe";
const bash = process.platform === "win32" ? (existsSync(gitBash) ? gitBash : null) : "bash";

test("quoted arguments reach a real shell byte for byte", { skip: bash === null }, () => {
  for (const value of hostile) {
    const done = spawnSync(bash!, ["-s"], { input: `printf '%s' ${shellQuote(value)}`, encoding: "utf8" });
    assert.equal(done.status, 0, done.stderr);
    assert.equal(done.stdout, value);
  }
});

test("a command line carries every argument after the separator", () => {
  const line = commandLine("abc123", 30, ["sh", "-c", "echo 'hi' && ls"]);
  assert.deepEqual(words(line), [
    "bash", "/usr/local/lib/aura/run.sh", "abc123", "30", "--", "sh", "-c", "echo 'hi' && ls",
  ]);
});

test("workspace paths must be relative and stay inside", () => {
  for (const path of ["a.txt", "src/deep/file.py", ".env", "name with space"]) {
    assert.equal(relativePathProblem(path), null, path);
  }
  for (const path of ["", "/etc/passwd", "../x", "a/../../x", "a\u0000b", "a/", ".", "..", "x".repeat(5000)]) {
    assert.notEqual(relativePathProblem(path), null, JSON.stringify(path).slice(0, 40));
  }
});

test("guest scripts are present, parse, and have Unix line endings", () => {
  const shell = GUEST_SCRIPTS.filter((name) => name.endsWith(".sh"));
  assert.deepEqual(GUEST_SCRIPTS.filter((name) => !shell.includes(name)), ["confine.py", "proxy.py"]);
  for (const name of shell) {
    const path = fileURLToPath(new URL(`guest/${name}`, root));
    const text = readFileSync(path, "utf8").replaceAll("\r\n", "\n");
    assert.ok(text.startsWith("#!/usr/bin/env bash\n"), name);
    if (bash !== null) {
      const done = spawnSync(bash, ["-n", "-s"], { input: text, encoding: "utf8" });
      assert.equal(done.status, 0, `${name}: ${done.stderr}`);
    }
  }
});
