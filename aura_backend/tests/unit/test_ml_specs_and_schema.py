"""ML architecture specifications and tool-argument validation.

The specs are declared as **data**: parameter counts and memory footprints are computable with
no torch installed, so an operator can answer "does this fit on the box?" from a laptop. The
build step is the only place torch is imported at all.

Argument validation is a security boundary, not a convenience: tool arguments come from a model
whose input may include attacker-controlled text, so coercion must widen types without ever
reinterpreting structure.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain.errors import ConfigurationError, ValidationError
from aura_core.domain.tools import ParameterSpec, ToolCategory, ToolSpec
from aura_core.ml.nn import (
    AnomalyDetectorSpec,
    Architecture,
    AutoencoderSpec,
    CNNSpec,
    GraphNetworkSpec,
    RecommenderSpec,
    RecurrentSpec,
    TabularSpec,
    Task,
    TimeSeriesSpec,
    TransformerSpec,
    build,
    torch_available,
)
from aura_core.tools.schema import coerce_value, validate_arguments


def _spec(**overrides) -> ParameterSpec:
    base = dict(name="p", type="string", description="d", required=True)
    base.update(overrides)
    return ParameterSpec(**base)


class TestSpecArithmetic:
    def test_a_transformer_parameter_count_is_dominated_by_the_embedding(self) -> None:
        small = TransformerSpec(
            architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION,
            vocab_size=1000, d_model=64, n_layers=2, n_heads=4, d_ff=256,
        )
        large_vocab = TransformerSpec(
            architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION,
            vocab_size=32_000, d_model=64, n_layers=2, n_heads=4, d_ff=256,
        )
        assert large_vocab.parameter_count > small.parameter_count * 5

    def test_layers_scale_the_count_roughly_linearly(self) -> None:
        def count(layers: int) -> int:
            return TransformerSpec(
                architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION,
                vocab_size=1000, d_model=64, n_layers=layers,
            ).parameter_count

        assert count(4) - count(2) == pytest.approx(count(6) - count(4), rel=0.01)

    def test_memory_is_reported_in_megabytes(self) -> None:
        """The number an operator needs before deciding whether a model fits on the hardware."""
        spec = TransformerSpec(
            architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION,
            vocab_size=32_000, d_model=512, n_layers=6,
        )
        assert spec.estimated_fp32_mb == pytest.approx(spec.parameter_count * 4 / 1024**2, rel=0.01)
        assert spec.estimated_fp32_mb > 0

    @pytest.mark.parametrize(
        "spec",
        [
            TransformerSpec(architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION),
            CNNSpec(architecture=Architecture.CNN, task=Task.CLASSIFICATION),
            RecurrentSpec(architecture=Architecture.LSTM, task=Task.CLASSIFICATION),
            AutoencoderSpec(architecture=Architecture.AUTOENCODER, task=Task.ANOMALY_DETECTION),
            GraphNetworkSpec(architecture=Architecture.GNN, task=Task.CLASSIFICATION),
            TimeSeriesSpec(architecture=Architecture.TIME_SERIES, task=Task.FORECASTING),
            TabularSpec(architecture=Architecture.MLP, task=Task.REGRESSION),
            RecommenderSpec(architecture=Architecture.RECOMMENDER, task=Task.RANKING),
            AnomalyDetectorSpec(architecture=Architecture.AUTOENCODER,
                                task=Task.ANOMALY_DETECTION),
        ],
    )
    def test_every_family_sizes_itself_without_torch(self, spec) -> None:
        assert spec.parameter_count > 0
        assert spec.estimated_fp32_mb > 0

    @pytest.mark.parametrize(
        "spec",
        [
            TransformerSpec(architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION),
            CNNSpec(architecture=Architecture.CNN, task=Task.CLASSIFICATION),
            RecurrentSpec(architecture=Architecture.GRU, task=Task.CLASSIFICATION),
            AutoencoderSpec(architecture=Architecture.AUTOENCODER, task=Task.ANOMALY_DETECTION),
            GraphNetworkSpec(architecture=Architecture.GNN, task=Task.CLASSIFICATION),
            TimeSeriesSpec(architecture=Architecture.TIME_SERIES, task=Task.FORECASTING),
            TabularSpec(architecture=Architecture.MLP, task=Task.REGRESSION),
            RecommenderSpec(architecture=Architecture.RECOMMENDER, task=Task.RANKING),
        ],
    )
    def test_every_family_serialises_for_lineage(self, spec) -> None:
        """A spec is recorded alongside a trained model, so it must round-trip as JSON."""
        payload = json.loads(spec.to_json())
        assert payload["architecture"] == str(spec.architecture)
        assert payload["parameter_count"] == spec.parameter_count
        assert payload["estimated_fp32_mb"] == spec.estimated_fp32_mb

    def test_a_bidirectional_recurrent_model_is_larger(self) -> None:
        def count(bidirectional: bool) -> int:
            return RecurrentSpec(
                architecture=Architecture.LSTM, task=Task.CLASSIFICATION,
                bidirectional=bidirectional,
            ).parameter_count

        assert count(True) > count(False)

    def test_gru_gates_are_cheaper_than_lstm_gates(self) -> None:
        """Three gates versus four -- the count must reflect the architecture, not a constant."""
        lstm = RecurrentSpec(architecture=Architecture.LSTM, task=Task.CLASSIFICATION)
        gru = RecurrentSpec(architecture=Architecture.GRU, task=Task.CLASSIFICATION)
        assert gru.parameter_count < lstm.parameter_count

    def test_a_variational_autoencoder_carries_an_extra_head(self) -> None:
        def count(variational: bool) -> int:
            return AutoencoderSpec(
                architecture=Architecture.AUTOENCODER, task=Task.ANOMALY_DETECTION,
                variational=variational,
            ).parameter_count

        assert count(True) > count(False)

    def test_attention_heads_change_a_gat_but_not_a_gcn(self) -> None:
        def count(conv_type: str, heads: int) -> int:
            return GraphNetworkSpec(
                architecture=Architecture.GNN, task=Task.CLASSIFICATION,
                conv_type=conv_type, heads=heads,
            ).parameter_count

        assert count("gat", 4) > count("gat", 1)
        assert count("gcn", 4) == count("gcn", 1)

    @pytest.mark.parametrize("backbone", ["lstm", "tcn", "transformer"])
    def test_each_time_series_backbone_sizes_itself(self, backbone: str) -> None:
        spec = TimeSeriesSpec(
            architecture=Architecture.TIME_SERIES, task=Task.FORECASTING, backbone=backbone
        )
        assert spec.parameter_count > 0

    def test_a_content_aware_recommender_is_larger(self) -> None:
        def count(use_content: bool) -> int:
            return RecommenderSpec(
                architecture=Architecture.RECOMMENDER, task=Task.RANKING,
                use_content_features=use_content,
            ).parameter_count

        assert count(True) > count(False)

    def test_a_two_dimensional_cnn_has_more_parameters_than_one_dimensional(self) -> None:
        def count(dimensions: int) -> int:
            return CNNSpec(
                architecture=Architecture.CNN, task=Task.CLASSIFICATION, dimensions=dimensions
            ).parameter_count

        assert count(2) > count(1)


class TestSpecValidation:
    """Invalid shapes must fail at construction, not at the first training step."""

    def test_dropout_outside_its_range_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            TransformerSpec(
                architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION, dropout=1.0
            )
        with pytest.raises(ValidationError):
            TransformerSpec(
                architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION, dropout=-0.1
            )

    def test_a_model_dimension_not_divisible_by_the_head_count_is_refused(self) -> None:
        """Torch raises this several minutes into a run; the spec catches it immediately."""
        with pytest.raises(ValidationError):
            TransformerSpec(
                architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION,
                d_model=100, n_heads=8,
            )

    def test_an_unknown_graph_convolution_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            GraphNetworkSpec(
                architecture=Architecture.GNN, task=Task.CLASSIFICATION, conv_type="magic"
            )

    def test_an_unknown_time_series_backbone_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            TimeSeriesSpec(
                architecture=Architecture.TIME_SERIES, task=Task.FORECASTING, backbone="oracle"
            )

    def test_an_invalid_cnn_dimensionality_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            CNNSpec(architecture=Architecture.CNN, task=Task.CLASSIFICATION, dimensions=4)


class TestBuild:
    def test_torch_availability_is_reported_honestly(self) -> None:
        assert isinstance(torch_available(), bool)

    def test_building_without_torch_names_the_missing_dependency(self) -> None:
        """A deployment that never trains never installs torch, and the specs stay usable."""
        if torch_available():
            pytest.skip("torch is installed in this environment")
        with pytest.raises(ConfigurationError) as excinfo:
            build(TransformerSpec(architecture=Architecture.TRANSFORMER,
                                  task=Task.CLASSIFICATION))
        assert "PyTorch is required" in str(excinfo.value)
        assert excinfo.value.details["architecture"] == "transformer"


# --------------------------------------------------------------------------------------
# Tool argument validation
# --------------------------------------------------------------------------------------


class TestCoercion:
    def test_a_quoted_number_is_accepted(self) -> None:
        """A JSON-emitting model quoting a number is a formatting artefact, not a difference."""
        assert coerce_value(_spec(type="integer"), "12") == 12
        assert coerce_value(_spec(type="number"), "1.5") == pytest.approx(1.5)

    def test_a_number_is_stringified_for_a_string_parameter(self) -> None:
        assert coerce_value(_spec(type="string"), 42) == "42"

    def test_a_boolean_is_not_silently_a_number(self) -> None:
        """`bool` subclasses `int` in Python; accepting True as 1 is never what was meant."""
        with pytest.raises(ValidationError) as excinfo:
            coerce_value(_spec(type="integer"), True)
        assert "boolean" in str(excinfo.value)

    def test_a_float_that_is_not_integral_is_refused_for_an_integer(self) -> None:
        with pytest.raises(ValidationError):
            coerce_value(_spec(type="integer"), 1.5)

    def test_an_integral_float_is_narrowed(self) -> None:
        assert coerce_value(_spec(type="integer"), 3.0) == 3

    @pytest.mark.parametrize("value", ["true", "TRUE", "yes", "1"])
    def test_truthy_strings_become_true(self, value: str) -> None:
        assert coerce_value(_spec(type="boolean"), value) is True

    @pytest.mark.parametrize("value", ["false", "no", "0"])
    def test_falsy_strings_become_false(self, value: str) -> None:
        assert coerce_value(_spec(type="boolean"), value) is False

    def test_an_ambiguous_boolean_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            coerce_value(_spec(type="boolean"), "maybe")

    def test_a_string_is_never_parsed_into_a_structure(self) -> None:
        """This is the boundary that stops a crafted string becoming structured tool input."""
        with pytest.raises(ValidationError):
            coerce_value(_spec(type="object"), '{"admin": true}')
        with pytest.raises(ValidationError):
            coerce_value(_spec(type="array", item_type="integer"), "[1, 2, 3]")

    def test_array_items_are_coerced_elementwise(self) -> None:
        assert coerce_value(_spec(type="array", item_type="integer"), ["1", 2]) == [1, 2]

    def test_a_bad_array_element_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            coerce_value(_spec(type="array", item_type="integer"), ["not a number"])

    def test_an_object_is_copied_not_referenced(self) -> None:
        source = {"a": 1}
        result = coerce_value(_spec(type="object"), source)
        result["b"] = 2
        assert source == {"a": 1}

    def test_a_null_for_an_optional_parameter_yields_its_default(self) -> None:
        assert coerce_value(_spec(type="integer", required=False, default=5), None) == 5

    def test_a_null_for_a_required_parameter_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            coerce_value(_spec(type="string"), None)

    def test_an_unparseable_number_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            coerce_value(_spec(type="integer"), "twelve")

    def test_a_wrong_python_type_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            coerce_value(_spec(type="string"), {"nested": "object"})
        with pytest.raises(ValidationError):
            coerce_value(_spec(type="integer"), ["a", "list"])

    def test_an_unsupported_declared_type_is_refused_at_declaration(self) -> None:
        """Caught when the tool is defined, not when a model first calls it."""
        with pytest.raises(ValueError):
            _spec(type="binary")

    def test_an_array_must_declare_its_item_type(self) -> None:
        with pytest.raises(ValueError):
            _spec(type="array")


class TestArgumentValidation:
    def _tool(self, *parameters: ParameterSpec) -> ToolSpec:
        return ToolSpec(
            name="t", description="d", parameters=tuple(parameters),
            category=ToolCategory.UTILITY,
        )

    def test_declared_parameters_are_returned_coerced(self) -> None:
        tool = self._tool(_spec(name="count", type="integer"))
        assert validate_arguments(tool, {"count": "7"}) == {"count": 7}

    def test_unknown_parameters_are_rejected_not_ignored(self) -> None:
        """Ignoring them lets a model believe it passed an option that silently did nothing."""
        tool = self._tool(_spec(name="query"))
        with pytest.raises(ValidationError) as excinfo:
            validate_arguments(tool, {"query": "x", "admin": True})
        assert excinfo.value.details["unknown"] == ["admin"]

    def test_missing_required_parameters_are_named(self) -> None:
        tool = self._tool(_spec(name="a"), _spec(name="b"))
        with pytest.raises(ValidationError) as excinfo:
            validate_arguments(tool, {})
        assert excinfo.value.details["missing"] == ["a", "b"]

    def test_a_null_required_parameter_counts_as_missing(self) -> None:
        tool = self._tool(_spec(name="a"))
        with pytest.raises(ValidationError):
            validate_arguments(tool, {"a": None})

    def test_omitted_optional_parameters_take_their_default(self) -> None:
        tool = self._tool(_spec(name="limit", type="integer", required=False, default=5))
        assert validate_arguments(tool, {}) == {"limit": 5}

    def test_an_omitted_optional_with_no_default_is_absent(self) -> None:
        tool = self._tool(_spec(name="note", required=False))
        assert validate_arguments(tool, {}) == {}

    def test_enum_constraints_are_enforced(self) -> None:
        tool = self._tool(_spec(name="mode", enum=("fast", "thorough")))
        assert validate_arguments(tool, {"mode": "fast"}) == {"mode": "fast"}
        with pytest.raises(ValidationError):
            validate_arguments(tool, {"mode": "unsafe"})

    def test_numeric_bounds_are_enforced(self) -> None:
        tool = self._tool(_spec(name="n", type="integer", minimum=1, maximum=10))
        assert validate_arguments(tool, {"n": 5}) == {"n": 5}
        with pytest.raises(ValidationError):
            validate_arguments(tool, {"n": 0})
        with pytest.raises(ValidationError):
            validate_arguments(tool, {"n": 11})

    def test_string_length_is_enforced(self) -> None:
        """A tool whose argument is unbounded is a memory and token-budget problem."""
        tool = self._tool(_spec(name="q", max_length=10))
        with pytest.raises(ValidationError):
            validate_arguments(tool, {"q": "x" * 11})

    def test_non_mapping_arguments_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            validate_arguments(self._tool(_spec(name="a")), ["not", "an", "object"])

    def test_a_providers_unparsed_marker_produces_a_correctable_message(self) -> None:
        """A provider that could not parse the model's JSON marks it rather than raising
        mid-stream; the model must be told how to fix it."""
        with pytest.raises(ValidationError) as excinfo:
            validate_arguments(self._tool(_spec(name="a")), {"_unparsed": "{broken"})
        assert "re-emit them as a JSON object" in str(excinfo.value)

    def test_a_tool_with_no_parameters_accepts_an_empty_object(self) -> None:
        assert validate_arguments(self._tool(), {}) == {}
