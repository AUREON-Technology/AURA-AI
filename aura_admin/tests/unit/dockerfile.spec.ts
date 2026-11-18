import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

// F52: the console image's runner stage must not run the server as root. No Docker daemon is
// available to the test suites, so this pins the Dockerfile itself: the last stage switches to
// the unprivileged `node` user before CMD, and everything it copies in is owned by that user so
// Next can still create .next/cache at runtime.

function runnerStage(): string[] {
  const dockerfile = readFileSync(path.join(__dirname, "..", "..", "Dockerfile"), "utf8");
  const lines = dockerfile.split("\n").map((line) => line.trim());
  const from = lines.findLastIndex((line) => /^FROM\s/i.test(line));
  expect(lines[from]).toMatch(/\bAS runner$/i);
  return lines.slice(from).filter((line) => line && !line.startsWith("#"));
}

test("the runner stage runs as the node user, set before CMD", () => {
  const stage = runnerStage();
  const users = stage.filter((line) => /^USER\s/i.test(line));
  expect(users).toEqual(["USER node"]);
  const cmd = stage.findIndex((line) => /^CMD\s/i.test(line));
  expect(stage.indexOf("USER node")).toBeGreaterThan(-1);
  expect(stage.indexOf("USER node")).toBeLessThan(cmd);
});

test("every artifact the runner copies in is owned by node", () => {
  const copies = runnerStage().filter((line) => /^COPY\s/i.test(line));
  expect(copies.length).toBeGreaterThan(0);
  for (const copy of copies) {
    expect(copy).toContain("--chown=node:node");
  }
});
