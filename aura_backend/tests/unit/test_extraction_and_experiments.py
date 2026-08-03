"""Knowledge-graph extraction and experiment tracking.

Extraction's hard problem is precision, not recall: an extractor that turns every
sentence-initial capital into a node fills the graph with noise that makes the real entities
unfindable. Experiment tracking's hard problem is that a reviewer approving a promotion needs to
see variance, not only the winning run.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain.errors import ConflictError, NotFoundError
from aura_core.knowledge.extraction import (
    CompositeExtractor,
    LLMEntityExtractor,
    RuleEntityExtractor,
    _classify,
    _parse_json_object,
    iter_entity_names,
)
from aura_core.mlops.experiments import (
    Experiment,
    ExperimentTracker,
    InMemoryExperimentStore,
    MetricPoint,
    RunRecord,
    RunStatus,
)
from aura_core.prompts import build_default_registry


class TestRuleExtraction:
    @pytest.fixture
    def extractor(self) -> RuleEntityExtractor:
        return RuleEntityExtractor()

    async def test_typed_literals_are_extracted(self, extractor) -> None:
        text = (
            "Contact sarah.chen@example.com or visit https://example.com/policy. "
            "The deadline is 2026-03-15 and the budget is $1,250.00."
        )
        delta = await extractor.extract(text, source="doc-1")
        by_type = {e.type: e.name for e in delta.entities}

        assert by_type["email"] == "sarah.chen@example.com"
        assert by_type["url"].startswith("https://example.com")
        assert by_type["date"] == "2026-03-15"
        assert by_type["money"] == "$1,250.00"

    async def test_prose_dates_are_recognised(self, extractor) -> None:
        delta = await extractor.extract("Effective 15 March 2026 the policy changes.")
        assert any(e.type == "date" for e in delta.entities)

    async def test_sentence_initial_capitals_are_not_entities(self, extractor) -> None:
        """Without this filter the graph fills with 'The', 'This', and every leading verb."""
        delta = await extractor.extract(
            "The policy changed. However the details are unclear. This affects everyone."
        )
        names = set(iter_entity_names(delta))
        assert not ({"The", "However", "This"} & names)

    async def test_a_multi_word_phrase_survives_even_at_a_sentence_start(
        self, extractor
    ) -> None:
        """'Cairo Regional Office is...' is a real entity that happens to open a sentence."""
        delta = await extractor.extract("Cairo Regional Office handles the backlog.")
        assert any("Cairo Regional Office" in n for n in iter_entity_names(delta))

    async def test_repeated_mentions_are_counted_not_duplicated(self, extractor) -> None:
        delta = await extractor.extract(
            "Sarah Chen approved it. Later Sarah Chen revised it. Sarah Chen signed off."
        )
        matches = [e for e in delta.entities if e.name == "Sarah Chen"]
        assert len(matches) == 1
        assert matches[0].mention_count >= 2

    async def test_relations_are_extracted_with_sub_certain_confidence(self, extractor) -> None:
        """A surface-pattern match is evidence, not proof."""
        delta = await extractor.extract(
            "Sarah Chen is the director of Finance Operations.", source="handbook"
        )
        leads = [r for r in delta.relations if r.predicate == "leads"]
        assert leads
        assert leads[0].confidence == pytest.approx(0.7)
        assert leads[0].sources == ("handbook",)

    @pytest.mark.parametrize(
        "text,predicate",
        [
            ("Ahmed Hassan works at Acme Corp.", "works_at"),
            ("Acme Corp is located in Cairo District.", "located_in"),
            ("Acme Corp acquired Beta Holdings.", "acquired"),
            ("Acme Corp operates Gamma Partners.", "manages"),
            ("Ahmed Hassan reports to Sarah Chen.", "reports_to"),
            ("Project Atlas depends on Project Beacon.", "depends_on"),
        ],
    )
    async def test_each_relation_pattern(self, extractor, text: str, predicate: str) -> None:
        delta = await extractor.extract(text)
        assert predicate in {r.predicate for r in delta.relations}

    async def test_a_self_relation_is_dropped(self, extractor) -> None:
        delta = await extractor.extract("Acme Corp acquired Acme Corp.")
        assert not delta.relations

    async def test_the_entity_ceiling_is_enforced(self) -> None:
        """An unbounded extractor on a large document is a memory and storage problem."""
        text = " ".join(f"Entity Number{i} exists." for i in range(200))
        delta = await RuleEntityExtractor(max_entities=10).extract(text)
        assert len(delta.entities) <= 10

    async def test_names_below_the_length_floor_are_skipped(self) -> None:
        delta = await RuleEntityExtractor(min_name_length=20).extract("Sarah Chen approved it.")
        assert not delta.entities

    async def test_empty_text_yields_an_empty_delta(self, extractor) -> None:
        delta = await extractor.extract("")
        assert not delta.entities and not delta.relations

    async def test_the_source_is_recorded_on_every_entity(self, extractor) -> None:
        """Provenance is what makes 'forget everything from this source' possible."""
        delta = await extractor.extract("Sarah Chen approved it.", source="doc-9")
        assert all(e.sources == ("doc-9",) for e in delta.entities)


class TestClassification:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Acme Corp", "organisation"),
            ("Cairo University", "organisation"),
            ("Barclays Bank", "organisation"),
            ("Cairo District", "place"),
            ("Heathrow Airport", "place"),
            ("Sarah Chen", "person"),
            ("Quarterly Revenue Forecast Model", "concept"),
        ],
    )
    def test_heuristic_types(self, name: str, expected: str) -> None:
        assert _classify(name) == expected


class TestLLMExtraction:
    class _Router:
        def __init__(self, body: str | Exception) -> None:
            self._body = body

        async def complete(self, request):  # noqa: ANN001
            if isinstance(self._body, Exception):
                raise self._body

            class Response:
                text = ""

            Response.text = self._body
            return Response()

    def _extractor(self, body, **kwargs) -> LLMEntityExtractor:
        return LLMEntityExtractor(
            router=self._Router(body), prompts=build_default_registry(), **kwargs
        )

    async def test_entities_and_relations_are_parsed(self) -> None:
        body = json.dumps({
            "entities": [
                {"name": "Sarah Chen", "type": "person"},
                {"name": "Finance Operations", "type": "organisation"},
            ],
            "relations": [
                {"subject": "Sarah Chen", "predicate": "leads", "object": "Finance Operations",
                 "confidence": 0.9},
            ],
        })
        delta = await self._extractor(body).extract("irrelevant", source="doc-1")

        assert {e.name for e in delta.entities} == {"Sarah Chen", "Finance Operations"}
        assert delta.relations[0].predicate == "leads"
        assert delta.relations[0].confidence == pytest.approx(0.9)

    async def test_confidence_is_capped_below_certainty(self) -> None:
        body = json.dumps({
            "entities": [{"name": "A", "type": "x"}, {"name": "B", "type": "x"}],
            "relations": [{"subject": "A", "predicate": "p", "object": "B", "confidence": 1.0}],
        })
        delta = await self._extractor(body).extract("text")
        assert delta.relations[0].confidence == pytest.approx(0.95)

    async def test_a_relation_naming_an_unextracted_entity_is_dropped(self) -> None:
        """Commonly a hallucinated participant; inventing the node would corrupt the graph."""
        body = json.dumps({
            "entities": [{"name": "Sarah Chen", "type": "person"}],
            "relations": [{"subject": "Sarah Chen", "predicate": "leads",
                           "object": "A Company That Was Never Mentioned", "confidence": 0.9}],
        })
        delta = await self._extractor(body).extract("text")
        assert not delta.relations

    async def test_low_confidence_relations_are_filtered(self) -> None:
        body = json.dumps({
            "entities": [{"name": "A", "type": "x"}, {"name": "B", "type": "x"}],
            "relations": [{"subject": "A", "predicate": "p", "object": "B", "confidence": 0.2}],
        })
        delta = await self._extractor(body, min_confidence=0.6).extract("text")
        assert not delta.relations

    async def test_predicates_are_normalised(self) -> None:
        body = json.dumps({
            "entities": [{"name": "A", "type": "x"}, {"name": "B", "type": "x"}],
            "relations": [{"subject": "A", "predicate": "Reports To", "object": "B",
                           "confidence": 0.8}],
        })
        delta = await self._extractor(body).extract("text")
        assert delta.relations[0].predicate == "reports_to"

    async def test_a_nameless_entity_is_skipped(self) -> None:
        body = json.dumps({"entities": [{"name": "  ", "type": "x"},
                                        {"name": "Real", "type": "x"}]})
        delta = await self._extractor(body).extract("text")
        assert {e.name for e in delta.entities} == {"Real"}

    async def test_an_incomplete_relation_is_skipped(self) -> None:
        body = json.dumps({
            "entities": [{"name": "A", "type": "x"}],
            "relations": [{"subject": "A", "predicate": "", "object": "", "confidence": 0.9}],
        })
        assert not (await self._extractor(body).extract("text")).relations

    async def test_a_router_failure_yields_an_empty_delta_not_an_exception(self) -> None:
        """Extraction enhances ingestion; failing it must not fail the ingestion the user asked
        for."""
        delta = await self._extractor(RuntimeError("provider down")).extract("text", source="d")
        assert not delta.entities
        assert delta.source == "d"

    async def test_a_malformed_response_yields_an_empty_delta(self) -> None:
        delta = await self._extractor("I could not find any entities, sorry!").extract("text")
        assert not delta.entities

    async def test_the_excerpt_is_truncated_rather_than_chunked(self) -> None:
        """Per-chunk LLM extraction is the cost blow-up this class is scoped to avoid."""
        captured: dict = {}

        class CapturingRouter:
            async def complete(self, request):  # noqa: ANN001
                captured["length"] = len(request.conversation.last_user_message.content)

                class Response:
                    text = "{}"
                    tool_calls = ()

                return Response()

        extractor = LLMEntityExtractor(
            router=CapturingRouter(), prompts=build_default_registry(), max_chars=100
        )
        await extractor.extract("x" * 10_000)
        assert captured["length"] < 2000


class TestCompositeExtractor:
    async def test_deltas_from_several_extractors_are_merged(self) -> None:
        rules = RuleEntityExtractor()
        llm_body = json.dumps({"entities": [{"name": "Project Atlas", "type": "project"}]})
        llm = LLMEntityExtractor(
            router=TestLLMExtraction._Router(llm_body), prompts=build_default_registry()
        )
        delta = await CompositeExtractor([rules, llm]).extract(
            "Sarah Chen approved it.", source="doc"
        )
        names = set(iter_entity_names(delta))
        assert "Sarah Chen" in names
        assert "Project Atlas" in names

    async def test_one_failing_extractor_does_not_lose_the_others(self) -> None:
        class Broken:
            name = "broken"

            async def extract(self, text, *, source=""):  # noqa: ANN001
                raise RuntimeError("boom")

        delta = await CompositeExtractor([Broken(), RuleEntityExtractor()]).extract(
            "Sarah Chen approved it."
        )
        assert "Sarah Chen" in set(iter_entity_names(delta))

    async def test_no_extractors_yields_an_empty_delta(self) -> None:
        delta = await CompositeExtractor([]).extract("text", source="s")
        assert not delta.entities


class TestJsonExtraction:
    def test_a_fenced_object_is_unwrapped(self) -> None:
        assert _parse_json_object('```json\n{"entities": []}\n```') == {"entities": []}

    def test_an_object_in_prose_is_recovered(self) -> None:
        assert _parse_json_object('Sure: {"entities": []} done')["entities"] == []

    def test_a_top_level_array_is_rejected(self) -> None:
        assert _parse_json_object("[1, 2, 3]") == {}

    def test_no_object_returns_empty(self) -> None:
        assert _parse_json_object("nothing here") == {}

    def test_an_unbalanced_object_returns_empty(self) -> None:
        assert _parse_json_object('{"entities": [') == {}


# --------------------------------------------------------------------------------------
# Experiment tracking
# --------------------------------------------------------------------------------------


@pytest.fixture
def tracker() -> ExperimentTracker:
    return ExperimentTracker(store=InMemoryExperimentStore())


class TestRunRecord:
    def _run(self, *points: MetricPoint) -> RunRecord:
        return RunRecord(id="r1", experiment_id="e1", metrics=tuple(points))

    def test_latest_takes_the_highest_step(self) -> None:
        run = self._run(
            MetricPoint(name="loss", value=0.9, step=0),
            MetricPoint(name="loss", value=0.3, step=5),
            MetricPoint(name="loss", value=0.5, step=2),
        )
        assert run.latest("loss") == pytest.approx(0.3)

    def test_best_respects_the_optimisation_direction(self) -> None:
        run = self._run(
            MetricPoint(name="loss", value=0.9, step=0),
            MetricPoint(name="loss", value=0.3, step=1),
        )
        assert run.best("loss", maximise=False) == pytest.approx(0.3)
        assert run.best("loss", maximise=True) == pytest.approx(0.9)

    def test_an_unrecorded_metric_is_none_not_zero(self) -> None:
        """Zero would rank an unmeasured run alongside a genuinely terrible one."""
        assert self._run().latest("accuracy") is None
        assert self._run().best("accuracy") is None

    def test_a_series_is_ordered_by_step(self) -> None:
        run = self._run(
            MetricPoint(name="loss", value=0.3, step=2),
            MetricPoint(name="loss", value=0.9, step=0),
        )
        assert run.series("loss") == [(0, 0.9), (2, 0.3)]

    def test_duration_is_none_while_a_run_is_open(self) -> None:
        assert self._run().duration_s is None

    def test_as_dict_is_serialisable(self) -> None:
        payload = self._run(MetricPoint(name="loss", value=0.1)).as_dict()
        assert json.dumps(payload, default=str)
        assert payload["id"] == "r1"


class TestExperimentTracker:
    async def test_experiment_creation_is_idempotent(self, tracker) -> None:
        """A scheduled job calls this on every run; a second call must not fork the history."""
        first = await tracker.experiment("finetune-qwen", description="d", tags=("nightly",))
        second = await tracker.experiment("finetune-qwen")
        assert first.id == second.id

    async def test_a_run_records_its_reproducibility_inputs(self, tracker) -> None:
        """Without the dataset digest, 'which data produced this model?' has no answer."""
        experiment = await tracker.experiment("e")
        run = await tracker.start_run(
            experiment.id, parameters={"lr": 3e-4}, dataset_digest="sha256:abc",
            base_model="qwen2.5:7b", code_version="git:deadbeef",
        )
        assert run.parameters == {"lr": 3e-4}
        assert run.dataset_digest == "sha256:abc"
        assert run.status is RunStatus.RUNNING

    async def test_starting_a_run_on_an_unknown_experiment_raises(self, tracker) -> None:
        with pytest.raises(NotFoundError):
            await tracker.start_run("no-such-experiment")

    async def test_metrics_accumulate_across_steps(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        run = await tracker.start_run(experiment.id)
        for step, loss in enumerate([0.9, 0.6, 0.4]):
            await tracker.log_metric(run.id, "loss", loss, step=step)
        final = await tracker.log_metrics(run.id, {"accuracy": 0.87, "f1": 0.84}, step=2)

        assert final.latest("loss") == pytest.approx(0.4)
        assert final.latest("accuracy") == pytest.approx(0.87)
        assert len(final.series("loss")) == 3

    async def test_artefacts_are_recorded_by_name(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        run = await tracker.start_run(experiment.id)
        updated = await tracker.log_artefact(run.id, "adapter", "s3://bucket/adapter.safetensors")
        assert updated.artefacts["adapter"].endswith("adapter.safetensors")

    async def test_logging_against_an_unknown_run_raises(self, tracker) -> None:
        with pytest.raises(NotFoundError):
            await tracker.log_metric("no-such-run", "loss", 0.1)

    async def test_finishing_a_run_stamps_a_duration(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        run = await tracker.start_run(experiment.id)
        finished = await tracker.finish_run(run.id)

        assert finished.status is RunStatus.COMPLETED
        assert finished.duration_s is not None and finished.duration_s >= 0.0

    async def test_finishing_twice_is_a_conflict(self, tracker) -> None:
        """Silently overwriting a terminal status would let a failed run be relabelled."""
        experiment = await tracker.experiment("e")
        run = await tracker.start_run(experiment.id)
        await tracker.finish_run(run.id, status=RunStatus.FAILED, error="OOM")
        with pytest.raises(ConflictError):
            await tracker.finish_run(run.id)

    async def test_a_failed_run_keeps_its_error(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        run = await tracker.start_run(experiment.id)
        finished = await tracker.finish_run(run.id, status=RunStatus.FAILED, error="CUDA OOM")
        assert finished.error == "CUDA OOM"

    async def test_compare_ranks_runs_by_a_metric(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        for lr, score in [(1e-4, 0.80), (3e-4, 0.91), (1e-3, 0.62)]:
            run = await tracker.start_run(experiment.id, parameters={"lr": lr})
            await tracker.log_metric(run.id, "accuracy", score)

        ranked = await tracker.compare(experiment.id, metric="accuracy")
        assert [row["value"] for row in ranked] == [0.91, 0.80, 0.62]
        assert ranked[0]["parameters"]["lr"] == pytest.approx(3e-4)

    async def test_compare_can_minimise(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        for loss in (0.9, 0.2, 0.5):
            run = await tracker.start_run(experiment.id)
            await tracker.log_metric(run.id, "loss", loss)

        ranked = await tracker.compare(experiment.id, metric="loss", maximise=False)
        assert ranked[0]["value"] == pytest.approx(0.2)

    async def test_runs_without_the_metric_are_excluded_not_ranked_last(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        scored = await tracker.start_run(experiment.id)
        await tracker.log_metric(scored.id, "accuracy", 0.9)
        await tracker.start_run(experiment.id)  # never logged anything

        assert len(await tracker.compare(experiment.id, metric="accuracy")) == 1

    async def test_summary_reports_spread_not_only_the_winner(self, tracker) -> None:
        """A configuration that wins on average with a wide spread is not obviously better than
        a stable second place."""
        experiment = await tracker.experiment("e")
        for score in (0.70, 0.80, 0.90):
            run = await tracker.start_run(experiment.id)
            await tracker.log_metric(run.id, "accuracy", score)

        summary = await tracker.summary(experiment.id, metric="accuracy")
        assert summary["runs"] == 3
        assert summary["best"] == pytest.approx(0.90)
        assert summary["worst"] == pytest.approx(0.70)
        assert summary["mean"] == pytest.approx(0.80)
        assert summary["stdev"] > 0.0

    async def test_a_summary_with_no_data_is_explicit(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        assert await tracker.summary(experiment.id, metric="accuracy") == {
            "metric": "accuracy", "runs": 0
        }

    async def test_a_single_run_has_zero_stdev_rather_than_an_error(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        run = await tracker.start_run(experiment.id)
        await tracker.log_metric(run.id, "accuracy", 0.9)
        assert (await tracker.summary(experiment.id, metric="accuracy"))["stdev"] == 0.0

    async def test_export_produces_one_json_object_per_line(self, tracker) -> None:
        experiment = await tracker.experiment("e")
        run = await tracker.start_run(experiment.id)
        await tracker.log_metric(run.id, "accuracy", 0.9)
        runs = await tracker._store.list_runs(experiment.id)

        lines = ExperimentTracker.export(runs).splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == run.id

    async def test_the_store_returns_runs_newest_first_and_respects_the_limit(
        self, tracker
    ) -> None:
        experiment = await tracker.experiment("e")
        for _ in range(5):
            await tracker.start_run(experiment.id)
        assert len(await tracker._store.list_runs(experiment.id, limit=2)) == 2

    async def test_an_unknown_experiment_lists_no_runs(self, tracker) -> None:
        assert await tracker._store.list_runs("nope") == []

    async def test_metrics_are_published_when_a_registry_is_supplied(self, metrics) -> None:
        tracked = ExperimentTracker(store=InMemoryExperimentStore(), metrics=metrics)
        experiment = await tracked.experiment("e")
        run = await tracked.start_run(experiment.id)
        await tracked.log_metric(run.id, "accuracy", 0.9)
        await tracked.log_metrics(run.id, {"f1": 0.8})
        assert (await tracked._store.get_run(run.id)).latest("f1") == pytest.approx(0.8)
