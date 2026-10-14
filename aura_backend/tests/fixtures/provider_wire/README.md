# Provider wire fixtures (ADR-030 Phase 3)

Response shapes of the hosted open-weight APIs ADR-030 admits. `tests/unit/test_provider_wire_contracts.py`
replays them against `OpenAICompatibleProvider` through an `httpx.MockTransport`, so the default
suite stays offline.

**Provenance matters.** Each file's `provenance` field says where it came from:

- `documented`: reconstructed from the provider's public API documentation (2026-10). It pins the
  quirks the adapter must handle, but it is **not** evidence that the live API still behaves this
  way.
- `captured <date>`: written by `aura_backend/scripts/capture_provider_wire.py` against the live
  API, with the key redacted. Replacing a `documented` file with a capture is part of Phase 6.

File shape: `{"provenance", "status", "headers", "json"}` for a plain response, or
`{"provenance", "status", "headers", "sse": [lines...]}` for a stream.
