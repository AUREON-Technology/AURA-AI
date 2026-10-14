"""Capture a hosted provider's real responses as wire fixtures (ADR-030 Phase 3/6).

The fixtures in ``tests/fixtures/provider_wire/`` start life as ``documented`` reconstructions.
This script replaces guesswork with evidence. Run it against the live API with a real key, and
it records the raw responses the contract tests replay, with the key redacted.

    python scripts/capture_provider_wire.py \\
        --base-url "$AURA_PROVIDER_HOSTED1_BASE_URL" \\
        --model "$AURA_PROVIDER_HOSTED1_MODEL" \\
        --api-key-env AURA_PROVIDER_HOSTED1_API_KEY \\
        --prefix groq --out tests/fixtures/provider_wire

It writes ``<prefix>_capture_complete.json``, ``<prefix>_capture_stream.json`` and
``<prefix>_capture_tool_call.json``. Diff them against the documented fixture of the same
provider; where they disagree, the capture wins, and the documented file is updated or retired.

Deliberately not part of any test run: it needs network, a key, and spends quota. The base URL
comes from the caller, never from this file, which keeps hosted endpoints out of Python source
(``tests/architecture/test_no_external_inference.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

#: Response headers worth keeping: the ones the adapter or the quota tracker reads.
_KEEP_HEADERS = ("content-type", "retry-after")
_KEEP_PREFIXES = ("x-ratelimit-",)

_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def _headers(response) -> dict[str, str]:
    return {
        k.lower(): v for k, v in response.headers.items()
        if k.lower() in _KEEP_HEADERS or k.lower().startswith(_KEEP_PREFIXES)
    }


def _write(out: Path, name: str, payload: dict) -> None:
    path = out / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", required=True, help="name of the env var holding the key")
    parser.add_argument("--prefix", required=True, help="fixture name prefix, e.g. groq")
    parser.add_argument("--out", default="tests/fixtures/provider_wire")
    parser.add_argument("--extra-body", default="", help="JSON object merged into each request")
    args = parser.parse_args(argv)

    import httpx

    key = os.environ.get(args.api_key_env, "")
    if not key:
        print(f"{args.api_key_env} is not set", file=sys.stderr)
        return 2
    extra = json.loads(args.extra_body) if args.extra_body else {}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    provenance = f"captured {date.today().isoformat()}"
    messages = [{"role": "user", "content": "What is the capital of France? One word."}]
    base = {**extra, "model": args.model, "messages": messages, "max_tokens": 64, "temperature": 0}

    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {key}"},
        timeout=60.0,
    ) as client:
        response = client.post("/chat/completions", json=base)
        _write(out, f"{args.prefix}_capture_complete.json", {
            "provenance": provenance, "status": response.status_code,
            "headers": _headers(response), "json": response.json(),
        })

        lines: list[str] = []
        stream_body = {**base, "stream": True, "stream_options": {"include_usage": True}}
        with client.stream("POST", "/chat/completions", json=stream_body) as streamed:
            status, headers = streamed.status_code, _headers(streamed)
            lines.extend(streamed.iter_lines())
        _write(out, f"{args.prefix}_capture_stream.json", {
            "provenance": provenance, "status": status, "headers": headers, "sse": lines,
        })

        tool_body = {
            **base,
            "messages": [{"role": "user", "content": "What is the weather in Cairo?"}],
            "tools": [_TOOL], "tool_choice": "auto",
        }
        response = client.post("/chat/completions", json=tool_body)
        _write(out, f"{args.prefix}_capture_tool_call.json", {
            "provenance": provenance, "status": response.status_code,
            "headers": _headers(response), "json": response.json(),
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
