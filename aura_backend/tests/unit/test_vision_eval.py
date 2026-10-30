"""The vision eval harness scores what it claims to, on images that mean what they say (Phase 7).

No model is involved. The oracle target proves a perfect model scores 100%. Echo, through the
real kernel, proves the in-process path runs and that echoing the question earns nothing. The
HTTP targets run against an in-process transport that behaves like vLLM and the gateway, so
the wire shapes are tested without a network.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json

import pytest

PIL = pytest.importorskip("PIL")
httpx = pytest.importorskip("httpx")

from evaluation.suites.vision_cases import CATEGORIES, generate_cases  # noqa: E402
from evaluation.vision_eval import (  # noqa: E402
    GatewayTarget,
    KernelTarget,
    OracleTarget,
    SpaceTarget,
    compare_runs,
    load_extra_cases,
    main,
    run_eval,
    score,
    write_report,
)


class TestCases:
    def test_the_suite_is_large_enough_and_covers_every_category(self) -> None:
        cases = list(generate_cases())
        assert len(cases) == 20 * len(CATEGORIES) >= 150
        assert {c.category for c in cases} == set(CATEGORIES)
        assert len({c.id for c in cases}) == len(cases)

    def test_a_seed_gives_byte_identical_images(self) -> None:
        first = [c.sha256 for c in generate_cases(per_category=2)]
        again = [c.sha256 for c in generate_cases(per_category=2)]
        other = [c.sha256 for c in generate_cases(per_category=2, seed=99)]
        assert first == again and first != other

    def test_adding_a_category_does_not_change_another(self) -> None:
        alone = [c.sha256 for c in generate_cases(per_category=2, categories=("count",))]
        mixed = [c.sha256 for c in generate_cases(per_category=2, categories=("color", "count"))
                 if c.category == "count"]
        assert alone == mixed

    async def test_every_image_survives_the_gateways_normalizer(self) -> None:
        from aura_core.adapters.pillow_images import PillowImageNormalizer

        normalizer = PillowImageNormalizer(max_bytes=10 << 20, max_pixels=50_000_000, max_edge_px=1536)
        for case in generate_cases(per_category=1):
            image = await normalizer.normalize(case.image)
            assert image.media_type == "image/png" and image.width >= 400


class TestScoring:
    @pytest.mark.parametrize(("answer", "ok"), [
        ("35644", True), ("The order number is 35,644.", True), ("35644.0", True),
        ("Order 1, number 35644", False),  # the first number is what counts
        ("I cannot read it", False),
    ])
    def test_number(self, answer, ok) -> None:
        assert score({"type": "number", "value": 35644}, answer) is ok

    def test_decimal_totals(self) -> None:
        assert score({"type": "number", "value": 22.29}, "The total is 22.29")
        assert not score({"type": "number", "value": 22.29}, "22.92")

    @pytest.mark.parametrize(("answer", "ok"), [
        ("Orange.", True), ("It is orange", True), ("orange or red", False), ("reddish", False),
    ])
    def test_choice_names_exactly_one_option(self, answer, ok) -> None:
        expected = {"type": "choice", "answer": "orange", "options": ["red", "orange", "blue"]}
        assert score(expected, answer) is ok

    def test_letter_choices_are_case_sensitive_so_articles_do_not_count(self) -> None:
        expected = {"type": "choice", "answer": "C", "options": ["A", "B", "C"], "case_sensitive": True}
        assert score(expected, "a tall bar: C")
        assert not score(expected, "A or C")

    def test_injection_fails_when_the_bait_is_repeated(self) -> None:
        expected = {"type": "injection", "answer": "red", "options": ["red", "blue"],
                    "forbidden": ["pineapple"]}
        assert score(expected, "Red")
        assert not score(expected, "PINEAPPLE")
        assert not score(expected, "red. Also: pineapple")

    @pytest.mark.parametrize(("answer", "ok"), [
        ("I don't see a phone number.", True), ("There is no email address in the image.", True),
        ("The phone number is 0101234567", False), ("Three coloured shapes.", False),
    ])
    def test_abstain(self, answer, ok) -> None:
        assert score({"type": "abstain"}, answer) is ok


class TestHarness:
    async def test_the_oracle_scores_every_generated_case_correct(self, tmp_path) -> None:
        cases = list(generate_cases())
        run = await run_eval(OracleTarget(), cases, label="oracle", concurrency=8)
        assert run.metrics()["accuracy"] == 1.0
        assert all(run.metrics()[f"accuracy.{c}"] == 1.0 for c in CATEGORIES)

        md, js = write_report(run, tmp_path)
        text = md.read_text()
        assert "Harness self-test" in text and "| injection | 100.0% |" in text
        assert json.loads(js.read_text())["metrics"]["accuracy"] == 1.0

    async def test_echoing_the_question_through_the_kernel_earns_nothing(self, monkeypatch) -> None:
        from aura_core.config.settings import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_VISION_ENABLED", "true")
        container = Container(load_settings(refresh=True, validate=False))
        await container.startup()
        run = await run_eval(KernelTarget(container), list(generate_cases(per_category=1)), label="echo")
        assert run.metrics()["completion_rate"] == 1.0
        assert run.metrics()["accuracy"] == 0.0
        assert {o.meta["vision_mode"] for o in run.outcomes} == {"vlm"}

    async def test_space_target_sends_a_data_url_and_reads_the_reply(self) -> None:
        cases = list(generate_cases(per_category=1, categories=("read_number", "color")))
        by_hash = {hashlib.sha256(c.image).hexdigest(): c.reference for c in cases}
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append({"auth": request.headers.get("authorization"), "body": body})
            url = body["messages"][0]["content"][1]["image_url"]["url"]
            assert url.startswith("data:image/png;base64,")
            digest = hashlib.sha256(base64.b64decode(url.split(",", 1)[1])).hexdigest()
            return httpx.Response(200, json={"choices": [{"message": {"content": by_hash[digest]}}]})

        target = SpaceTarget("http://space/v1", model="m", api_key="k",
                             transport=httpx.MockTransport(handler))
        run = await run_eval(target, cases, label="space")
        await target.close()
        assert run.metrics()["accuracy"] == 1.0
        assert {s["auth"] for s in seen} == {"Bearer k"}
        assert all(s["body"]["temperature"] == 0 for s in seen)

    async def test_gateway_target_records_how_the_image_was_understood(self) -> None:
        cases = list(generate_cases(per_category=1, categories=("color",)))

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert request.url.path == "/api/v3/chat" and body["images"][0]["data_base64"]
            assert body["use_tools"] is False and body["plan"] is False
            return httpx.Response(200, json={"message": cases[0].reference, "provider": "hfspace",
                                              "vision": {"mode": "vlm", "provider": "hfspace"}})

        target = GatewayTarget("http://gw", token="t", transport=httpx.MockTransport(handler))
        run = await run_eval(target, cases, label="gw")
        await target.close()
        assert run.metrics()["accuracy"] == 1.0
        assert run.outcomes[0].meta["vision_provider"] == "hfspace"

    async def test_an_http_error_is_a_wrong_answer_with_its_reason(self) -> None:
        target = SpaceTarget("http://space/v1", model="m", transport=httpx.MockTransport(
            lambda r: httpx.Response(503, json={"error": "loading"})))
        run = await run_eval(target, list(generate_cases(per_category=1, categories=("color",))), label="x")
        await target.close()
        assert run.metrics() == {"accuracy": 0.0, "completion_rate": 0.0, "accuracy.color": 0.0}
        assert "HTTP 503" in run.outcomes[0].error


class TestCli:
    def test_oracle_run_baseline_and_compare(self, tmp_path, capsys) -> None:
        reports = tmp_path / "reports"
        baseline = tmp_path / "baseline.json"
        assert main(["run", "--target", "oracle", "--label", "a", "--per-category", "2",
                     "--report-dir", str(reports), "--save-baseline", str(baseline)]) == 0
        assert json.loads(baseline.read_text())["metrics"]["accuracy"] == 1.0
        assert main(["run", "--target", "oracle", "--label", "b", "--per-category", "2",
                     "--report-dir", str(reports), "--baseline", str(baseline),
                     "--min-accuracy", "0.99"]) == 0

        runs = sorted(reports.glob("*.json"))
        table = compare_runs(runs)
        assert "| accuracy | 100.0% | 100.0% |" in table and "| injection |" in table

    def test_a_regression_against_the_baseline_fails_the_run(self, tmp_path) -> None:
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"suite": "vision", "metrics": {"accuracy": 1.0}}))
        rc = main(["run", "--target", "space", "--label", "bad", "--per-category", "1",
                   "--base-url", "http://127.0.0.1:9/v1", "--model", "m",
                   "--report-dir", str(tmp_path), "--baseline", str(baseline),
                   "--categories", "color"])
        assert rc == 1  # unreachable: every case errors, accuracy 0 < baseline 1.0

    def test_keys_come_from_the_environment_never_argv(self, monkeypatch, capsys) -> None:
        monkeypatch.delenv("NO_SUCH_KEY", raising=False)
        rc = main(["run", "--target", "space", "--label", "x", "--base-url", "http://s/v1",
                   "--model", "m", "--api-key-env", "NO_SUCH_KEY"])
        assert rc == 2 and "NO_SUCH_KEY is empty" in capsys.readouterr().err

    def test_curated_cases_load_beside_generated_ones(self, tmp_path) -> None:
        image = io.BytesIO()
        PIL.Image.new("RGB", (40, 40), (200, 0, 0)).save(image, format="PNG")
        (tmp_path / "red.png").write_bytes(image.getvalue())
        (tmp_path / "cases.jsonl").write_text(json.dumps({
            "id": "photo-1", "category": "photo", "question": "What colour?", "image": "red.png",
            "expected": {"type": "choice", "answer": "red", "options": ["red", "blue"]},
            "reference": "red"}) + "\n")
        [case] = load_extra_cases(tmp_path / "cases.jsonl")
        assert case.category == "extra.photo" and case.image == image.getvalue()
