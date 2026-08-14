from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.sweep_boundary import (  # noqa: E402
    RTL_BOUNDARY_ANALYSIS_SURFACE,
    RTL_BOUNDARY_GROUND_TRUTH_SURFACE,
    RTL_BOUNDARY_POLICY_ANALYSIS_SURFACE,
    RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
    RTL_BOUNDARY_SCHEMA_VERSION,
    RTL_BOUNDARY_SWEEP_ENUMERATION_SURFACE,
    RTL_BOUNDARY_SWEEP_SPACE_SURFACE,
    SweepBoundaryError,
    analyze_boundary_ground_truth,
    analyze_boundary_policy_trial,
    enumerate_sweep_space,
    reconstruct_boundary_prediction,
    select_boundary_points,
)
from verilator_model_sidecar.boundary_benchmark import (  # noqa: E402
    RTL_BOUNDARY_ADJUDICATION_SURFACE,
    RTL_BOUNDARY_EVIDENCE_BUNDLE_SURFACE,
    RTL_BOUNDARY_EXPERIMENT_CONTRACT_SURFACE,
    RTL_BOUNDARY_SEMANTIC_MANIFEST_SURFACE,
)
from verilator_model_sidecar.boundary_report import (  # noqa: E402
    RTL_BOUNDARY_PIPELINE_RESULT_SURFACE,
    RTL_BOUNDARY_PLOT_PAYLOAD_SURFACE,
    RTL_BOUNDARY_REPORT_BUNDLE_SURFACE,
    RTL_BOUNDARY_REPORT_VALIDATION_SURFACE,
)


def _space(axes: list[dict]) -> dict:
    return {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_SWEEP_SPACE_SURFACE,
        "axes": axes,
    }


def _ground_truth(
    enumeration: dict,
    *,
    bad_fails,
    fixed_fails=lambda _parameters: False,
) -> dict:
    return {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_GROUND_TRUTH_SURFACE,
        "sweep_space_sha256": enumeration["sweep_space_sha256"],
        "observations": [
            {
                "point_id": point["point_id"],
                "parameters": dict(reversed(list(point["parameters"].items()))),
                "bad_oracle": int(bad_fails(point["parameters"])),
                "fixed_oracle": int(fixed_fails(point["parameters"])),
            }
            for point in enumeration["points"]
        ],
    }


def _policy(kind: str, configuration: dict | None = None) -> dict:
    return {
        "kind": kind,
        "algorithm_version": 1,
        "seed_sha256": "1" * 64,
        "configuration": configuration or {},
    }


def _reconstructor() -> dict:
    return {"kind": "nearest_observed_graph", "algorithm_version": 1}


def _point_by_parameters(enumeration: dict) -> dict[tuple, str]:
    return {
        tuple(point["parameters"].items()): point["point_id"]
        for point in enumeration["points"]
    }


def _prediction(enumeration: dict, *, fail) -> dict:
    pass_point_ids = []
    fail_point_ids = []
    for point in enumeration["points"]:
        if fail(point["parameters"]):
            fail_point_ids.append(point["point_id"])
        else:
            pass_point_ids.append(point["point_id"])
    return {
        "pass_point_ids": pass_point_ids,
        "fail_point_ids": fail_point_ids,
    }


def _single_epoch_trial(
    sweep_space: dict,
    enumeration: dict,
    ground_truth: dict,
    prediction: dict | None = None,
) -> dict:
    policy = _policy("random")
    selected = list(
        select_boundary_points(
            sweep_space,
            policy,
            [],
            enumeration["point_count"],
        )
    )
    truth_by_point = {
        observation["point_id"]: observation["bad_oracle"]
        for observation in ground_truth["observations"]
    }
    trial = {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
        "sweep_space_sha256": enumeration["sweep_space_sha256"],
        "policy": policy,
        "reconstructor": _reconstructor(),
        "requested_count": enumeration["point_count"],
        "budget_logical_bad_queries": enumeration["point_count"],
        "epochs": [
            {
                "epoch_index": 0,
                "selected_point_ids": selected,
                "bad_observations": [
                    {
                        "point_id": point_id,
                        "bad_oracle": truth_by_point[point_id],
                    }
                    for point_id in selected
                ],
            }
        ],
    }
    if prediction is not None:
        trial["epochs"][0]["total_prediction_after_feedback"] = prediction
    return trial


class SweepBoundaryTest(unittest.TestCase):
    def test_public_schemas_define_all_boundary_surfaces(self) -> None:
        expected = {
            "rtl_boundary_sweep_space.schema.json": RTL_BOUNDARY_SWEEP_SPACE_SURFACE,
            "rtl_boundary_sweep_enumeration.schema.json": RTL_BOUNDARY_SWEEP_ENUMERATION_SURFACE,
            "rtl_boundary_ground_truth.schema.json": RTL_BOUNDARY_GROUND_TRUTH_SURFACE,
            "rtl_boundary_analysis.schema.json": RTL_BOUNDARY_ANALYSIS_SURFACE,
            "rtl_boundary_policy_trial.schema.json": RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
            "rtl_boundary_policy_analysis.schema.json": RTL_BOUNDARY_POLICY_ANALYSIS_SURFACE,
            "rtl_boundary_semantic_manifest.schema.json": RTL_BOUNDARY_SEMANTIC_MANIFEST_SURFACE,
            "rtl_boundary_experiment_contract.schema.json": RTL_BOUNDARY_EXPERIMENT_CONTRACT_SURFACE,
            "rtl_boundary_evidence_bundle.schema.json": RTL_BOUNDARY_EVIDENCE_BUNDLE_SURFACE,
            "rtl_boundary_adjudication.schema.json": RTL_BOUNDARY_ADJUDICATION_SURFACE,
            "rtl_boundary_plot_payload.schema.json": RTL_BOUNDARY_PLOT_PAYLOAD_SURFACE,
            "rtl_boundary_pipeline_result.schema.json": RTL_BOUNDARY_PIPELINE_RESULT_SURFACE,
            "rtl_boundary_report_bundle.schema.json": RTL_BOUNDARY_REPORT_BUNDLE_SURFACE,
            "rtl_boundary_report_validation.schema.json": RTL_BOUNDARY_REPORT_VALIDATION_SURFACE,
        }
        self.assertEqual(
            set(expected),
            {
                path.name
                for path in (ROOT / "contracts").glob("rtl_boundary_*schema.json")
            },
        )
        for filename, surface in expected.items():
            with self.subTest(filename=filename):
                schema = json.loads(
                    (ROOT / "contracts" / filename).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertEqual(schema["properties"]["surface"]["const"], surface)
                self.assertFalse(schema["additionalProperties"])
        trial_schema = json.loads(
            (ROOT / "contracts" / "rtl_boundary_policy_trial.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(trial_schema["$defs"]["policy"]["oneOf"]), 4)
        for definition in (
            "empty_configuration",
            "stratified_configuration",
            "refinement_configuration",
        ):
            self.assertFalse(
                trial_schema["$defs"][definition]["additionalProperties"]
            )

    def test_selector_policies_are_deterministic_and_public_feedback_only(self) -> None:
        sweep_space = _space(
            [
                {"name": "delay", "kind": "ordered", "values": [0, 1, 2]},
                {
                    "name": "kind",
                    "kind": "categorical",
                    "values": ["valid", "malformed"],
                    "adjacent_value_pairs": [["valid", "malformed"]],
                },
            ]
        )
        random_policy = _policy("random")
        first = select_boundary_points(sweep_space, random_policy, [], 3)
        second = select_boundary_points(sweep_space, random_policy, [], 3)
        self.assertEqual(first, second)
        self.assertEqual(
            first,
            (
                "point:v1:02912347ff4f21a527dcf7d1b2c5afd0f1749d77c35f5f8394c8c95027ec6452",
                "point:v1:fa3b6db8644cbe3a8ccd01edc64ae2b277e1599fee45331daae17663f9a6f331",
                "point:v1:075f79e91c02c99ae5dacdb7f74a2f92cddee0215c76dd0ed66ab84cf750ad04",
            ),
        )
        self.assertEqual(len(set(first)), 3)

        completed = [
            {
                "selected_point_ids": [first[0]],
                "bad_observations": [
                    {
                        "point_id": first[0],
                        "bad_oracle": 0,
                        "coverage_feature_ids": ["feature:a"],
                    }
                ],
            }
        ]
        self.assertNotIn(
            first[0],
            select_boundary_points(sweep_space, random_policy, completed, 6),
        )
        stratified = select_boundary_points(
            sweep_space,
            _policy("stratified", {"strata_axes": ["kind"]}),
            [],
            2,
        )
        enumeration = enumerate_sweep_space(sweep_space)
        by_id = {point["point_id"]: point["parameters"] for point in enumeration["points"]}
        self.assertEqual(
            {by_id[point_id]["kind"] for point_id in stratified},
            {"valid", "malformed"},
        )

    def test_ordered_refinement_replay_counts_gpu_batch_feedback_by_epoch(self) -> None:
        sweep_space = _space(
            [{"name": "delay", "kind": "ordered", "values": [0, 1, 2, 3, 4]}]
        )
        enumeration = enumerate_sweep_space(sweep_space)
        ground_truth = _ground_truth(
            enumeration,
            bad_fails=lambda parameters: parameters["delay"] >= 2,
        )
        policy = _policy("ordered_refinement", {"axis": "delay"})
        selected = list(select_boundary_points(sweep_space, policy, [], 2))
        points = _point_by_parameters(enumeration)
        self.assertEqual(
            selected,
            [
                points[(("delay", 0),)],
                points[(("delay", 4),)],
            ],
        )
        trial = {
            "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
            "surface": RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
            "sweep_space_sha256": enumeration["sweep_space_sha256"],
            "policy": policy,
            "reconstructor": _reconstructor(),
            "requested_count": 2,
            "budget_logical_bad_queries": 2,
            "epochs": [
                {
                    "epoch_index": 0,
                    "selected_point_ids": selected,
                    "bad_observations": [
                        {"point_id": selected[0], "bad_oracle": 0},
                        {"point_id": selected[1], "bad_oracle": 1},
                    ],
                }
            ],
        }
        analysis = analyze_boundary_policy_trial(sweep_space, ground_truth, trial)
        self.assertEqual(analysis["surface"], RTL_BOUNDARY_POLICY_ANALYSIS_SURFACE)
        self.assertEqual(
            analysis["first_violation"],
            {"status": "reached", "epoch_index": 0, "logical_bad_queries": 2},
        )
        self.assertEqual(analysis["first_bracket"]["logical_bad_queries"], 2)
        self.assertEqual(
            analysis["prediction_metrics"]["first_exact_boundary"],
            {"status": "not_reached"},
        )
        epoch_metrics = analysis["prediction_metrics"]["epochs"][0]
        self.assertEqual(epoch_metrics["boundary_precision"]["value"], 0.0)
        self.assertEqual(epoch_metrics["boundary_recall"]["value"], 0.0)
        self.assertEqual(epoch_metrics["boundary_hausdorff"]["ordinal_distance"], 1)
        self.assertEqual(epoch_metrics["failure_region_iou"]["value"], 2 / 3)
        self.assertEqual(
            epoch_metrics["minimal_failing_point_recovery"]["value"], 0.0
        )

    def test_policy_trial_rejects_non_replayable_or_inconsistent_observations(self) -> None:
        sweep_space = _space(
            [{"name": "delay", "kind": "ordered", "values": [0, 1, 2]}]
        )
        enumeration = enumerate_sweep_space(sweep_space)
        ground_truth = _ground_truth(
            enumeration,
            bad_fails=lambda parameters: parameters["delay"] >= 1,
        )
        policy = _policy("random")
        selected = list(select_boundary_points(sweep_space, policy, [], 2))
        valid = {
            "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
            "surface": RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
            "sweep_space_sha256": enumeration["sweep_space_sha256"],
            "policy": policy,
            "reconstructor": _reconstructor(),
            "requested_count": 2,
            "budget_logical_bad_queries": 2,
            "epochs": [
                {
                    "epoch_index": 0,
                    "selected_point_ids": selected,
                    "bad_observations": [
                        {
                            "point_id": point_id,
                            "bad_oracle": next(
                                observation["bad_oracle"]
                                for observation in ground_truth["observations"]
                                if observation["point_id"] == point_id
                            ),
                        }
                        for point_id in selected
                    ],
                }
            ],
        }
        altered_order = copy.deepcopy(valid)
        altered_order["epochs"][0]["selected_point_ids"] = list(reversed(selected))
        altered_order["epochs"][0]["bad_observations"] = list(
            reversed(altered_order["epochs"][0]["bad_observations"])
        )
        with self.assertRaises(SweepBoundaryError):
            analyze_boundary_policy_trial(sweep_space, ground_truth, altered_order)

        altered_observation = copy.deepcopy(valid)
        altered_observation["epochs"][0]["bad_observations"][0]["bad_oracle"] ^= 1
        with self.assertRaises(SweepBoundaryError):
            analyze_boundary_policy_trial(
                sweep_space, ground_truth, altered_observation
            )

        premature_stop = copy.deepcopy(valid)
        premature_stop["budget_logical_bad_queries"] = 3
        with self.assertRaises(SweepBoundaryError):
            analyze_boundary_policy_trial(sweep_space, ground_truth, premature_stop)

    def test_prediction_metrics_are_recomputed_without_producer_prediction(self) -> None:
        sweep_space = _space(
            [{"name": "delay", "kind": "ordered", "values": [0, 1, 2]}]
        )
        enumeration = enumerate_sweep_space(sweep_space)
        ground_truth = _ground_truth(
            enumeration,
            bad_fails=lambda parameters: parameters["delay"] == 2,
        )
        policy = _policy("random")
        selected = list(select_boundary_points(sweep_space, policy, [], 1))
        trial = {
            "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
            "surface": RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
            "sweep_space_sha256": enumeration["sweep_space_sha256"],
            "policy": policy,
            "reconstructor": _reconstructor(),
            "requested_count": 1,
            "budget_logical_bad_queries": 1,
            "epochs": [
                {
                    "epoch_index": 0,
                    "selected_point_ids": selected,
                    "bad_observations": [
                        {
                            "point_id": selected[0],
                            "bad_oracle": next(
                                observation["bad_oracle"]
                                for observation in ground_truth["observations"]
                                if observation["point_id"] == selected[0]
                            ),
                        }
                    ],
                }
            ],
        }
        analysis = analyze_boundary_policy_trial(sweep_space, ground_truth, trial)
        metrics = analysis["prediction_metrics"]["epochs"][0]
        self.assertEqual(metrics["status"], "computed")
        self.assertIn("boundary_precision", metrics)

    def test_producer_prediction_must_match_common_reconstructor(self) -> None:
        sweep_space = _space(
            [{"name": "delay", "kind": "ordered", "values": [0, 1, 2]}]
        )
        enumeration = enumerate_sweep_space(sweep_space)
        ground_truth = _ground_truth(
            enumeration,
            bad_fails=lambda parameters: parameters["delay"] == 2,
        )
        policy = _policy("random")
        selected = list(select_boundary_points(sweep_space, policy, [], 1))
        truth_by_point = {
            observation["point_id"]: observation["bad_oracle"]
            for observation in ground_truth["observations"]
        }
        trial = {
            "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
            "surface": RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
            "sweep_space_sha256": enumeration["sweep_space_sha256"],
            "policy": policy,
            "reconstructor": _reconstructor(),
            "requested_count": 1,
            "budget_logical_bad_queries": 1,
            "epochs": [
                {
                    "epoch_index": 0,
                    "selected_point_ids": selected,
                    "bad_observations": [
                        {
                            "point_id": selected[0],
                            "bad_oracle": truth_by_point[selected[0]],
                        }
                    ],
                    "total_prediction_after_feedback": _prediction(
                        enumeration,
                        fail=lambda _parameters: True,
                    ),
                }
            ],
        }
        with self.assertRaises(SweepBoundaryError):
            analyze_boundary_policy_trial(sweep_space, ground_truth, trial)

    def test_empty_true_boundary_is_not_an_exact_boundary_discovery(self) -> None:
        sweep_space = _space(
            [{"name": "x", "kind": "ordered", "values": [0, 1]}]
        )
        enumeration = enumerate_sweep_space(sweep_space)
        ground_truth = _ground_truth(
            enumeration,
            bad_fails=lambda _parameters: False,
        )
        trial = _single_epoch_trial(
            sweep_space,
            enumeration,
            ground_truth,
            _prediction(enumeration, fail=lambda _parameters: False),
        )
        analysis = analyze_boundary_policy_trial(sweep_space, ground_truth, trial)
        self.assertEqual(
            analysis["first_violation"],
            {"status": "not_applicable", "reason": "no_true_failure"},
        )
        self.assertEqual(
            analysis["first_bracket"],
            {"status": "not_applicable", "reason": "no_true_boundary"},
        )
        self.assertEqual(
            analysis["prediction_metrics"]["first_exact_boundary"],
            {"status": "not_applicable", "reason": "no_true_boundary"},
        )
        metrics = analysis["prediction_metrics"]["epochs"][0]
        self.assertEqual(metrics["boundary_hausdorff"], {
            "status": "not_applicable",
            "reason": "no_boundary",
        })
        self.assertEqual(metrics["failure_region_iou"], {
            "status": "not_applicable",
            "reason": "no_failure_region",
            "numerator": 0,
            "denominator": 0,
        })

    def test_enumeration_is_canonical_and_ordered_axis_direction_is_semantic(self) -> None:
        first = _space(
            [
                {
                    "name": "request_kind",
                    "kind": "categorical",
                    "values": ["valid", "malformed"],
                    "adjacent_value_pairs": [["valid", "malformed"]],
                },
                {
                    "name": "stall_cycles",
                    "kind": "ordered",
                    "values": [0, 1, 2],
                },
            ]
        )
        reordered = _space(
            [
                {
                    "name": "stall_cycles",
                    "kind": "ordered",
                    "values": [0, 1, 2],
                },
                {
                    "name": "request_kind",
                    "kind": "categorical",
                    "values": ["malformed", "valid"],
                    "adjacent_value_pairs": [["malformed", "valid"]],
                },
            ]
        )
        first_enumeration = enumerate_sweep_space(first)
        reordered_enumeration = enumerate_sweep_space(reordered)
        self.assertEqual(first_enumeration, reordered_enumeration)
        self.assertEqual(first_enumeration["point_count"], 6)
        self.assertEqual(
            len({point["point_id"] for point in first_enumeration["points"]}),
            6,
        )
        reversed_order = copy.deepcopy(first)
        reversed_order["axes"][1]["values"] = [2, 1, 0]
        self.assertNotEqual(
            enumerate_sweep_space(reversed_order)["sweep_space_sha256"],
            first_enumeration["sweep_space_sha256"],
        )

    def test_categorical_adjacency_exposes_the_tlul10818_boundary(self) -> None:
        sweep_space = _space(
            [
                {
                    "name": "request_integrity",
                    "kind": "categorical",
                    "values": ["valid", "malformed"],
                    "adjacent_value_pairs": [["valid", "malformed"]],
                }
            ]
        )
        enumeration = enumerate_sweep_space(sweep_space)
        ground_truth = _ground_truth(
            enumeration,
            bad_fails=lambda parameters: parameters["request_integrity"]
            == "malformed",
        )
        analysis = analyze_boundary_ground_truth(sweep_space, ground_truth)
        bad = analysis["revisions"]["bad"]
        fixed = analysis["revisions"]["fixed"]
        self.assertEqual(bad["pass_point_count"], 1)
        self.assertEqual(bad["fail_point_count"], 1)
        self.assertEqual(bad["boundary_edge_count"], 1)
        self.assertEqual(bad["failure_component_count"], 1)
        self.assertEqual(
            bad["minimal_failing_points"],
            {"status": "not_applicable", "point_ids": []},
        )
        self.assertEqual(fixed["fail_point_count"], 0)
        self.assertEqual(
            analysis["bad_to_fixed"]["disappeared_failure_point_ids"],
            bad["fail_point_ids"],
        )

    def test_tlul10818_four_action_fixture_maps_to_four_boundary_points(self) -> None:
        sweep_space = _space(
            [
                {
                    "name": "request_integrity",
                    "kind": "categorical",
                    "values": ["valid", "malformed"],
                    "adjacent_value_pairs": [["valid", "malformed"]],
                },
                {
                    "name": "d_acceptance",
                    "kind": "categorical",
                    "values": ["immediate", "backpressured"],
                    "adjacent_value_pairs": [["immediate", "backpressured"]],
                },
            ]
        )
        enumeration = enumerate_sweep_space(sweep_space)
        ground_truth = _ground_truth(
            enumeration,
            bad_fails=lambda parameters: parameters["request_integrity"]
            == "malformed",
        )
        analysis = analyze_boundary_ground_truth(sweep_space, ground_truth)
        bad = analysis["revisions"]["bad"]
        self.assertEqual(enumeration["point_count"], 4)
        self.assertEqual(bad["pass_point_count"], 2)
        self.assertEqual(bad["fail_point_count"], 2)
        self.assertEqual(bad["boundary_edge_count"], 2)
        self.assertEqual(bad["failure_component_count"], 1)
        self.assertEqual(
            {edge["axis"] for edge in bad["boundary_edges"]},
            {"request_integrity"},
        )
        self.assertEqual(analysis["revisions"]["fixed"]["fail_point_count"], 0)

    def test_nonmonotonic_regions_and_pareto_minima_are_recomputed(self) -> None:
        sweep_space = _space(
            [
                {
                    "name": "mode",
                    "kind": "categorical",
                    "values": ["a", "b"],
                    "adjacent_value_pairs": [],
                },
                {"name": "x", "kind": "ordered", "values": [0, 1, 2]},
                {"name": "y", "kind": "ordered", "values": [0, 1, 2]},
            ]
        )
        failing_parameters = {
            ("a", 0, 0),
            ("a", 2, 2),
            ("b", 1, 0),
            ("b", 1, 1),
            ("b", 2, 1),
        }
        enumeration = enumerate_sweep_space(sweep_space)
        ground_truth = _ground_truth(
            enumeration,
            bad_fails=lambda parameters: (
                parameters["mode"], parameters["x"], parameters["y"]
            )
            in failing_parameters,
        )
        analysis = analyze_boundary_ground_truth(sweep_space, ground_truth)
        bad = analysis["revisions"]["bad"]
        self.assertEqual(bad["fail_point_count"], 5)
        self.assertEqual(bad["boundary_edge_count"], 10)
        self.assertEqual(bad["failure_component_count"], 3)
        self.assertEqual(
            sorted(len(component["point_ids"]) for component in bad["failure_components"]),
            [1, 1, 3],
        )
        point_parameters = {
            point["point_id"]: point["parameters"]
            for point in enumeration["points"]
        }
        minima = {
            (
                point_parameters[point_id]["mode"],
                point_parameters[point_id]["x"],
                point_parameters[point_id]["y"],
            )
            for point_id in bad["minimal_failing_points"]["point_ids"]
        }
        self.assertEqual(
            bad["minimal_failing_points"]["status"], "computed"
        )
        self.assertEqual(minima, {("a", 0, 0), ("b", 1, 0)})

    def test_invalid_sweep_spaces_fail_closed(self) -> None:
        valid = _space(
            [
                {"name": "delay", "kind": "ordered", "values": [0, 1]},
                {
                    "name": "kind",
                    "kind": "categorical",
                    "values": ["valid", "malformed"],
                    "adjacent_value_pairs": [["valid", "malformed"]],
                },
            ]
        )
        cases = (
            lambda value: value.__setitem__("unexpected", True),
            lambda value: value["axes"].append(copy.deepcopy(value["axes"][0])),
            lambda value: value["axes"][0].__setitem__("values", [0, 0]),
            lambda value: value["axes"][0].__setitem__("kind", "continuous"),
            lambda value: value["axes"][0].__setitem__(
                "adjacent_value_pairs", [[0, 1]]
            ),
            lambda value: value["axes"][1].pop("adjacent_value_pairs"),
            lambda value: value["axes"][1].__setitem__(
                "adjacent_value_pairs", [["valid", "unknown"]]
            ),
            lambda value: value["axes"][1].__setitem__(
                "adjacent_value_pairs",
                [["valid", "malformed"], ["malformed", "valid"]],
            ),
        )
        for mutate in cases:
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(valid)
                mutate(candidate)
                with self.assertRaises(SweepBoundaryError):
                    enumerate_sweep_space(candidate)

    def test_invalid_policy_configurations_fail_closed(self) -> None:
        sweep_space = _space(
            [
                {"name": "delay", "kind": "ordered", "values": [0, 1]},
                {
                    "name": "kind",
                    "kind": "categorical",
                    "values": ["valid", "malformed"],
                    "adjacent_value_pairs": [["valid", "malformed"]],
                },
            ]
        )
        candidates = (
            _policy("random", {"axis": "delay"}),
            _policy("stratified", {"strata_axes": []}),
            _policy("stratified", {"strata_axes": ["unknown"]}),
            _policy("ordered_refinement", {"axis": "kind"}),
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaises(SweepBoundaryError):
                    select_boundary_points(sweep_space, candidate, [], 1)

    def test_incomplete_or_inconsistent_ground_truth_fails_closed(self) -> None:
        sweep_space = _space(
            [{"name": "delay", "kind": "ordered", "values": [0, 1, 2]}]
        )
        enumeration = enumerate_sweep_space(sweep_space)
        valid = _ground_truth(
            enumeration,
            bad_fails=lambda parameters: parameters["delay"] >= 1,
        )

        def mismatch_hash(value: dict) -> None:
            value["sweep_space_sha256"] = "0" * 64

        def missing(value: dict) -> None:
            value["observations"].pop()

        def duplicate(value: dict) -> None:
            value["observations"][1] = copy.deepcopy(value["observations"][0])

        def mismatched_id(value: dict) -> None:
            value["observations"][0]["point_id"] = "point:v1:" + "0" * 64

        def wrong_parameter_type(value: dict) -> None:
            value["observations"][0]["parameters"]["delay"] = False

        def boolean_oracle(value: dict) -> None:
            value["observations"][0]["bad_oracle"] = True

        cases = (
            mismatch_hash,
            missing,
            duplicate,
            mismatched_id,
            wrong_parameter_type,
            boolean_oracle,
        )
        for mutate in cases:
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(valid)
                mutate(candidate)
                with self.assertRaises(SweepBoundaryError):
                    analyze_boundary_ground_truth(sweep_space, candidate)


if __name__ == "__main__":
    unittest.main()
