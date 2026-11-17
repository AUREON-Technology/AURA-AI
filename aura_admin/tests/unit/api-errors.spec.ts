import { expect, test } from "@playwright/test";

import { errorFromBody } from "../../src/lib/api";

// Audit INT-029: the gateway answers errors in four shapes, and the console must turn each into
// a message an operator can act on rather than "Request failed (422)".

test("an AuraError envelope keeps its slug, message and retryable flag", () => {
  const body = { error: "store_unavailable", message: "the registry is down", retryable: true };
  const err = errorFromBody(503, { ...body, detail: body });
  expect(err.status).toBe(503);
  expect(err.error).toBe("store_unavailable");
  expect(err.message).toBe("the registry is down");
  expect(err.retryable).toBe(true);
});

test("a plain string detail is the message", () => {
  const err = errorFromBody(403, { detail: "cannot demote the last SuperAdmin" });
  expect(err.message).toBe("cannot demote the last SuperAdmin");
  expect(err.error).toBe("request_failed");
});

test("a FastAPI 422 list detail names each field and its problem", () => {
  const err = errorFromBody(422, {
    detail: [
      { type: "missing", loc: ["body", "version"], msg: "Field required", input: {} },
      {
        type: "string_pattern_mismatch",
        loc: ["body", "id"],
        msg: "String should match pattern '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
      },
    ],
  });
  expect(err.error).toBe("validation_error");
  expect(err.message).toBe(
    "version: Field required; id: String should match pattern '^[A-Za-z0-9][A-Za-z0-9._:-]*$'",
  );
});

test("a dict detail without a message is shown rather than dropped", () => {
  const err = errorFromBody(422, { detail: { allowed: ["User", "Developer", "Admin", "SuperAdmin"] } });
  expect(err.message).toContain("Request failed (422)");
  expect(err.message).toContain("allowed");
  expect(err.message).toContain("SuperAdmin");
});

test("an empty or unparseable body falls back to the status", () => {
  expect(errorFromBody(502, {}).message).toBe("Request failed (502)");
  expect(errorFromBody(502, null).message).toBe("Request failed (502)");
  expect(errorFromBody(500, "oops").message).toBe("oops");
});
