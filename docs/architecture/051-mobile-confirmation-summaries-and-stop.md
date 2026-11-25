# ADR-051: Confirmation cards lead with a summary; the phone can stop the agent

Status: Proposed (built and tested; not run on a device). Closes two rows of the tool inventory's
"mobile and multimodal" gaps (G.1, G.5-stop).

## Decisions

1. **Every held call carries `headline` and `risk_flags`** (`aura_core/tools/confirmation_summary.py`, attached in
   `Kernel._hold_for_confirmation`, the plan-review hold and `planning/feedback.py`). Deterministic and pure (no
   model call, no I/O, never raises): "Run: npm test", "Edit src/app.py", "Deploy deploy-backend.yml from main", and
   flags for destructive or elevated commands, secret files, migrations, pipeline config, deployments, remote
   writes and large diffs. Credentials are masked with the shared catalogue. **Flags are advisory:** they never
   change whether a call needs confirmation (`ToolPolicy` decides that), and a call without flags is not thereby
   safe — the card says so. The keys are additive, so an older client ignores them.
2. **The client leads with them** (`engineer_confirmation_card.dart`): headline, chips (localized per kind, the
   backend's English sentence as tooltip and screen-reader label), the raw command/arguments/diff under
   "Details". A **high-risk call opens Details at once**, so it is never approved without its detail in view. A
   backend without `headline` shows the raw card as before.
3. **The kill switch has a button** (`run_history_page.dart` → `POST /api/v3/agents/stop-all`, ADR-041): confirm
   dialog, then how many turns and desktop sessions were stopped, or "nothing was running". It never revokes
   devices from here.

## Not built (and why)

* Pause and redirect of a running agent: the loop has no checkpoint to pause at (needs a control flag checked
  each iteration, a shared store for multi-worker, and the redirect through the DATA/instruction channels).
* `stop-all` reaches this gateway process only (`scope: this_process`); the screen does not claim more.
* Flag texts on the chips are localized per kind; the tooltip sentence stays English.

## Evidence

`tests/unit/test_confirmation_summary.py` (headlines, every flag, ordering, hostile input, the kernel's real hold);
client: `test/engineer_confirmation_card_test.dart`, `test/agent_stop_all_test.dart` (real `AuraApiClient` against a
mock gateway; the page with a replaced repository); full client suite 1468 passed, `flutter analyze` clean.
