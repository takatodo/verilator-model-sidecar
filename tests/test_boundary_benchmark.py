from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.boundary_benchmark import (  # noqa: E402
    RTL_BOUNDARY_ADJUDICATION_SURFACE,
    RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
    RTL_BOUNDARY_EVIDENCE_BUNDLE_SURFACE,
    RTL_BOUNDARY_EXPERIMENT_CONTRACT_SURFACE,
    RTL_BOUNDARY_SEMANTIC_MANIFEST_SURFACE,
    adjudicate_boundary_benchmark,
)
from verilator_model_sidecar.sweep_boundary import (  # noqa: E402
    RTL_BOUNDARY_GROUND_TRUTH_SURFACE,
    RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
    RTL_BOUNDARY_SCHEMA_VERSION,
    RTL_BOUNDARY_SWEEP_SPACE_SURFACE,
    enumerate_sweep_space,
    select_boundary_points,
)
from verilator_model_sidecar.boundary_report import (  # noqa: E402
    RTL_BOUNDARY_PIPELINE_RESULT_SURFACE,
    RTL_BOUNDARY_REPORT_BUNDLE_SURFACE,
    build_boundary_report_bundle,
    validate_boundary_report_bundle,
)
from verilator_model_sidecar.cli import main as cli_main  # noqa: E402


def _sha256(value) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _policy(kind: str, configuration: dict | None = None) -> dict:
    return {
        "kind": kind,
        "algorithm_version": 1,
        "seed_sha256": "1" * 64,
        "configuration": configuration or {},
    }


def _manifest(label: str, revision: str) -> dict:
    return {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_SEMANTIC_MANIFEST_SURFACE,
        "target_id": "tlul10818",
        "revision_label": label,
        "revision_sha": revision,
        "checkpoint_identity": "checkpoint:tlul10818:v1",
        "oracle_identity": "oracle:tlul10818:error-response:v1",
        "observables": [
            {
                "name": "done",
                "semantic_id": "tlul10818.done",
                "width_bits": 1,
            },
            {
                "name": "oracle_violation",
                "semantic_id": "tlul10818.oracle_violation",
                "width_bits": 1,
            },
        ],
    }


def _action_domain(enumeration: dict) -> list[dict]:
    rows = []
    for point in enumeration["points"]:
        parameters = point["parameters"]
        action = (
            f"{parameters['request_integrity']}"
            f"_stall{parameters['stall_cycles']}"
        )
        rows.append(
            {
                "point_id": point["point_id"],
                "action": action,
                "parameters": copy.deepcopy(parameters),
            }
        )
    return rows


def _bundle() -> tuple[dict, dict]:
    sweep_space = {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_SWEEP_SPACE_SURFACE,
        "axes": [
            {
                "name": "request_integrity",
                "kind": "categorical",
                "values": ["valid", "malformed"],
                "adjacent_value_pairs": [["valid", "malformed"]],
            },
            {
                "name": "stall_cycles",
                "kind": "ordered",
                "values": [0, 1, 2],
            },
        ],
    }
    enumeration = enumerate_sweep_space(sweep_space)
    action_domain = _action_domain(enumeration)
    revisions = {
        "bad": "1" * 40,
        "fixed": "2" * 40,
    }
    manifests = {
        label: _manifest(label, revision) for label, revision in revisions.items()
    }
    policies = {
        "random": _policy("random"),
        "stratified": _policy(
            "stratified", {"strata_axes": ["request_integrity"]}
        ),
        "refinement": _policy("ordered_refinement", {"axis": "stall_cycles"}),
        "novelty": _policy("novelty_boundary_guided"),
    }
    trial_contracts = [
        {
            "trial_id": f"{name}_gpu",
            "backend_id": "gpu",
            "policy": policy,
            "requested_count": 2,
            "budget_logical_bad_queries": 2,
        }
        for name, policy in policies.items()
    ]
    trial_contracts.append(
        {
            "trial_id": "random_cpu",
            "backend_id": "cpu",
            "policy": policies["random"],
            "requested_count": 2,
            "budget_logical_bad_queries": 2,
        }
    )
    contract = {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_EXPERIMENT_CONTRACT_SURFACE,
        "experiment_id": "opentitan-tlul10818-boundary-v1",
        "target": {
            "target_id": "tlul10818",
            "issue": "10818",
            "ip": "tlul",
            "checkpoint_identity": "checkpoint:tlul10818:v1",
            "oracle_identity": "oracle:tlul10818:error-response:v1",
            "revisions": revisions,
            "semantic_observables": ["done"],
            "oracle_field": "oracle_violation",
            "semantic_manifest_sha256": {
                label: _sha256(manifest) for label, manifest in manifests.items()
            },
        },
        "sweep_space": sweep_space,
        "sweep_space_sha256": enumeration["sweep_space_sha256"],
        "action_domain": action_domain,
        "action_domain_sha256": _sha256(action_domain),
        "reconstructor": {
            "kind": "nearest_observed_graph",
            "algorithm_version": 1,
        },
        "backends": [
            {
                "backend_id": "cpu",
                "kind": "cpu",
                "executor_identity": "cpu-reference:v1",
                "resident_width": 1,
            },
            {
                "backend_id": "gpu",
                "kind": "gpu",
                "executor_identity": "gpu-resident:v1",
                "resident_width": 4,
            },
        ],
        "trials": trial_contracts,
        "comparisons": [
            {
                "comparison_id": "selector-on-gpu",
                "kind": "selector",
                "trial_ids": [
                    "random_gpu",
                    "stratified_gpu",
                    "refinement_gpu",
                    "novelty_gpu",
                ],
            },
            {
                "comparison_id": "random-backend",
                "kind": "backend",
                "trial_ids": ["random_cpu", "random_gpu"],
            },
        ],
    }
    ground_truth = {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_GROUND_TRUTH_SURFACE,
        "sweep_space_sha256": enumeration["sweep_space_sha256"],
        "observations": [
            {
                "point_id": point["point_id"],
                "parameters": point["parameters"],
                "bad_oracle": int(
                    point["parameters"]["request_integrity"] == "malformed"
                    and point["parameters"]["stall_cycles"] >= 1
                ),
                "fixed_oracle": 0,
            }
            for point in enumeration["points"]
        ],
    }
    truth_by_point = {
        observation["point_id"]: observation
        for observation in ground_truth["observations"]
    }
    semantic_observations = []
    for point_index, point in enumerate(enumeration["points"]):
        revisions_projection = {}
        for label in ("bad", "fixed"):
            projection = {
                "done": point_index + 1,
                "oracle_violation": truth_by_point[point["point_id"]][
                    f"{label}_oracle"
                ],
            }
            revisions_projection[label] = {
                "cpu": projection,
                "gpu": copy.deepcopy(projection),
            }
        semantic_observations.append(
            {"point_id": point["point_id"], "revisions": revisions_projection}
        )

    backend_by_id = {backend["backend_id"]: backend for backend in contract["backends"]}
    trials = []
    for trial_contract in contract["trials"]:
        selected = list(
            select_boundary_points(
                sweep_space,
                trial_contract["policy"],
                [],
                trial_contract["requested_count"],
            )
        )
        observations = [
            {
                "point_id": point_id,
                "bad_oracle": truth_by_point[point_id]["bad_oracle"],
                "coverage_feature_ids": [f"coverage:{point_id}"],
            }
            for point_id in selected
        ]
        policy_trial = {
            "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
            "surface": RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
            "sweep_space_sha256": enumeration["sweep_space_sha256"],
            "policy": copy.deepcopy(trial_contract["policy"]),
            "reconstructor": copy.deepcopy(contract["reconstructor"]),
            "requested_count": trial_contract["requested_count"],
            "budget_logical_bad_queries": trial_contract[
                "budget_logical_bad_queries"
            ],
            "epochs": [
                {
                    "epoch_index": 0,
                    "selected_point_ids": selected,
                    "bad_observations": observations,
                }
            ],
        }
        confirmations = [
            {"point_id": point_id, "epoch_index": 0, "fixed_oracle": 0}
            for point_id in selected
            if truth_by_point[point_id]["bad_oracle"] == 1
        ]
        execution_specs = [
            (point_id, "bad", "bad_search") for point_id in selected
        ] + [
            (confirmation["point_id"], "fixed", "fixed_confirmation")
            for confirmation in confirmations
        ]
        backend = backend_by_id[trial_contract["backend_id"]]
        executions = []
        launches = []
        groups = []
        for role in (("bad", "bad_search"), ("fixed", "fixed_confirmation")):
            role_specs = [spec for spec in execution_specs if spec[1:] == role]
            width = backend["resident_width"]
            groups.extend(
                role_specs[offset : offset + width]
                for offset in range(0, len(role_specs), width)
            )
        offset_ns = 0
        for launch_index, group in enumerate(groups):
            launch_id = f"{trial_contract['trial_id']}:launch:{launch_index}"
            execution_ids = []
            for point_id, revision, purpose in group:
                execution_id = f"{trial_contract['trial_id']}:execution:{len(executions)}"
                execution_ids.append(execution_id)
                executions.append(
                    {
                        "execution_id": execution_id,
                        "epoch_index": 0,
                        "point_id": point_id,
                        "revision": revision,
                        "purpose": purpose,
                        "launch_id": launch_id,
                        "cycle_evals": 5,
                    }
                )
            launches.append(
                {
                    "launch_id": launch_id,
                    "backend_id": backend["backend_id"],
                    "executor_identity": backend["executor_identity"],
                    "resident_width": backend["resident_width"],
                    "execution_ids": execution_ids,
                    "start_offset_ns": offset_ns,
                    "end_offset_ns": offset_ns + 10,
                }
            )
            offset_ns += 10
        trials.append(
            {
                "trial_id": trial_contract["trial_id"],
                "policy_trial": policy_trial,
                "fixed_confirmations": confirmations,
                "executions": executions,
                "launches": launches,
                "trial_wall_time_ns": offset_ns,
            }
        )
    evidence = {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_EVIDENCE_BUNDLE_SURFACE,
        "experiment_contract_sha256": _sha256(contract),
        "runner": {
            "status": "pass",
            "identity": "external-ci:tlul10818:v1",
            "completed_at": "2026-08-14T00:00:00Z",
        },
        "semantic_manifests": manifests,
        "ground_truth": ground_truth,
        "semantic_observations": semantic_observations,
        "trials": trials,
    }
    return contract, evidence


class BoundaryBenchmarkTest(unittest.TestCase):
    def test_external_bundle_recomputes_boundary_policy_and_backend_results(self) -> None:
        contract, evidence = _bundle()
        result = adjudicate_boundary_benchmark(contract, evidence)
        self.assertEqual(result["surface"], RTL_BOUNDARY_ADJUDICATION_SURFACE)
        self.assertEqual(result["status"], "pass", result["issues"])
        self.assertEqual(result["verified_identity"]["point_count"], 6)
        self.assertEqual(
            result["verified_identity"]["action_domain_sha256"],
            _sha256(contract["action_domain"]),
        )
        self.assertEqual(
            result["ground_truth_analysis"]["revisions"]["bad"]["fail_point_count"],
            2,
        )
        self.assertEqual(
            result["ground_truth_analysis"]["revisions"]["fixed"]["fail_point_count"],
            0,
        )
        self.assertEqual(len(result["trial_results"]), 5)
        selector = result["selector_comparisons"][0]
        self.assertEqual(selector["backend_id"], "gpu")
        self.assertEqual(len(selector["rows"]), 4)
        backend = result["backend_comparisons"][0]
        self.assertEqual(len(backend["selection_trace_sha256"]), 64)
        rows = {row["trial_id"]: row for row in backend["rows"]}
        self.assertEqual(
            rows["random_cpu"]["accounting"]["scheduled_state_evals"],
            rows["random_gpu"]["accounting"]["scheduled_state_evals"],
        )
        self.assertGreater(
            rows["random_cpu"]["accounting"]["batch_launches"],
            rows["random_gpu"]["accounting"]["batch_launches"],
        )

    def test_contract_and_evidence_drift_fail_closed(self) -> None:
        cases = []

        def boolean_schema_version(contract: dict, evidence: dict) -> None:
            evidence["schema_version"] = True

        cases.append((boolean_schema_version, "evidence_schema_version_invalid"))

        def semantic_mismatch(contract: dict, evidence: dict) -> None:
            projection = evidence["semantic_observations"][0]["revisions"]["bad"]
            projection["gpu"]["done"] = True

        cases.append((semantic_mismatch, "semantic_cpu_gpu_mismatch"))

        def action_domain_hash_drift(contract: dict, evidence: dict) -> None:
            contract["action_domain_sha256"] = "0" * 64
            evidence["experiment_contract_sha256"] = _sha256(contract)

        cases.append((action_domain_hash_drift, "action_domain_hash_mismatch"))

        def action_domain_parameter_drift(contract: dict, evidence: dict) -> None:
            contract["action_domain"][0]["parameters"]["stall_cycles"] = 1
            contract["action_domain_sha256"] = _sha256(contract["action_domain"])
            evidence["experiment_contract_sha256"] = _sha256(contract)

        cases.append((action_domain_parameter_drift, "action_domain_parameters_mismatch"))

        def action_domain_action_duplicate(contract: dict, evidence: dict) -> None:
            contract["action_domain"][1]["action"] = contract["action_domain"][0]["action"]
            contract["action_domain_sha256"] = _sha256(contract["action_domain"])
            evidence["experiment_contract_sha256"] = _sha256(contract)

        cases.append((action_domain_action_duplicate, "action_domain_action_duplicate"))

        def fixed_failure(contract: dict, evidence: dict) -> None:
            evidence["ground_truth"]["observations"][0]["fixed_oracle"] = 1

        cases.append((fixed_failure, "ground_truth_fixed_failure"))

        def non_replayable_selection(contract: dict, evidence: dict) -> None:
            epoch = evidence["trials"][0]["policy_trial"]["epochs"][0]
            epoch["selected_point_ids"].reverse()
            epoch["bad_observations"].reverse()

        cases.append((non_replayable_selection, "policy_trial_replay_failed"))

        def missing_execution(contract: dict, evidence: dict) -> None:
            evidence["trials"][0]["executions"].pop()

        cases.append((missing_execution, "execution_selection_link_mismatch"))

        def resident_width_exceeded(contract: dict, evidence: dict) -> None:
            cpu_trial = next(
                trial for trial in evidence["trials"] if trial["trial_id"] == "random_cpu"
            )
            first_launch = cpu_trial["launches"][0]
            second_launch = cpu_trial["launches"][1]
            moved = second_launch["execution_ids"][0]
            first_launch["execution_ids"].append(moved)
            next(
                execution
                for execution in cpu_trial["executions"]
                if execution["execution_id"] == moved
            )["launch_id"] = first_launch["launch_id"]
            cpu_trial["launches"].pop(1)

        cases.append((resident_width_exceeded, "launch_resident_width_exceeded"))

        def backend_trace_drift(contract: dict, evidence: dict) -> None:
            cpu_trial = next(
                trial for trial in evidence["trials"] if trial["trial_id"] == "random_cpu"
            )
            cpu_trial["policy_trial"]["epochs"][0]["bad_observations"][0][
                "coverage_feature_ids"
            ] = ["backend-specific-feature"]

        cases.append((backend_trace_drift, "backend_comparison_trace_mismatch"))

        def manifest_revision_drift(contract: dict, evidence: dict) -> None:
            fixed = evidence["semantic_manifests"]["fixed"]
            fixed["observables"][0]["semantic_id"] = "tlul10818.done.fixed"
            contract["target"]["semantic_manifest_sha256"]["fixed"] = _sha256(fixed)
            evidence["experiment_contract_sha256"] = _sha256(contract)

        cases.append((manifest_revision_drift, "semantic_manifest_revision_drift"))

        for mutate, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                contract, evidence = _bundle()
                mutate(contract, evidence)
                result = adjudicate_boundary_benchmark(contract, evidence)
                self.assertEqual(result["status"], "fail")
                self.assertEqual(result["issues"][0]["code"], expected_code)

    def test_evidence_must_bind_exact_contract_hash(self) -> None:
        contract, evidence = _bundle()
        evidence["experiment_contract_sha256"] = "0" * 64
        result = adjudicate_boundary_benchmark(contract, evidence)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["issues"][0]["code"], "evidence_contract_hash_mismatch")

    def test_report_bundle_is_deterministic_and_byte_verifiable(self) -> None:
        contract, evidence = _bundle()
        adjudication = adjudicate_boundary_benchmark(contract, evidence)
        first = build_boundary_report_bundle(adjudication)
        second = build_boundary_report_bundle(adjudication)
        self.assertEqual(first, second)
        self.assertEqual(first["surface"], RTL_BOUNDARY_REPORT_BUNDLE_SURFACE)
        self.assertEqual(first["plot_payload_sha256"], _sha256(first["plot_payload"]))
        self.assertEqual(
            first["plot_payload"]["action_domain_sha256"],
            adjudication["verified_identity"]["action_domain_sha256"],
        )
        self.assertEqual(
            first["graph_sha256"],
            hashlib.sha256(first["graph_svg"].encode("utf-8")).hexdigest(),
        )
        self.assertIn(first["plot_payload_sha256"], first["graph_svg"])
        self.assertIn(
            adjudication["verified_identity"]["action_domain_sha256"],
            first["markdown_report"],
        )
        self.assertIn("Selector comparison on one GPU executor", first["markdown_report"])
        self.assertIn("Same selector across CPU and GPU backends", first["markdown_report"])
        self.assertIn("does not claim unknown-bug discovery", first["markdown_report"])
        validation = validate_boundary_report_bundle(adjudication, first)
        self.assertEqual(validation["status"], "pass", validation["issues"])

        altered = copy.deepcopy(first)
        altered["graph_svg"] += "<!-- altered -->\n"
        validation = validate_boundary_report_bundle(adjudication, altered)
        self.assertEqual(validation["status"], "fail")
        self.assertEqual(validation["issues"][0]["code"], "report_bundle_mismatch")

        missing_identity = copy.deepcopy(adjudication)
        del missing_identity["verified_identity"]["action_domain_sha256"]
        validation = validate_boundary_report_bundle(missing_identity, first)
        self.assertEqual(validation["status"], "fail")
        self.assertEqual(
            validation["issues"][0]["code"],
            "report_action_domain_identity_invalid",
        )

    def test_public_report_schemas_require_action_domain_identity(self) -> None:
        adjudication_schema = json.loads(
            (ROOT / "contracts" / "rtl_boundary_adjudication.schema.json").read_text(
                encoding="utf-8"
            )
        )
        plot_schema = json.loads(
            (ROOT / "contracts" / "rtl_boundary_plot_payload.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(
            "action_domain_sha256",
            adjudication_schema["$defs"]["verified_identity"]["required"],
        )
        self.assertIn("action_domain_sha256", plot_schema["required"])
        self.assertEqual(
            plot_schema["properties"]["action_domain_sha256"],
            {"$ref": "#/$defs/sha256"},
        )

    def test_cli_writes_authoritative_boundary_pipeline_index(self) -> None:
        contract, evidence = _bundle()
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            contract_path = directory / "contract.json"
            evidence_path = directory / "evidence.json"
            output_path = directory / "pipeline.json"
            contract_path.write_text(
                json.dumps(contract, sort_keys=True), encoding="utf-8"
            )
            evidence_path.write_text(
                json.dumps(evidence, sort_keys=True), encoding="utf-8"
            )

            exit_code = cli_main(
                [
                    "adjudicate-boundary-benchmark",
                    "--experiment-contract",
                    contract_path.as_posix(),
                    "--evidence",
                    evidence_path.as_posix(),
                    "--output",
                    output_path.as_posix(),
                ]
            )
            self.assertEqual(exit_code, 0)
            pipeline = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(pipeline["surface"], RTL_BOUNDARY_PIPELINE_RESULT_SURFACE)
            self.assertEqual(pipeline["status"], "pass")
            self.assertEqual(pipeline["adjudication"]["status"], "pass")
            self.assertIsNotNone(pipeline["report_bundle"])
            graph = directory / pipeline["graph_artifact"]["path"]
            markdown = directory / pipeline["markdown_artifact"]["path"]
            self.assertTrue(graph.is_file())
            self.assertTrue(markdown.is_file())
            self.assertEqual(
                hashlib.sha256(graph.read_bytes()).hexdigest(),
                pipeline["graph_artifact"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256(markdown.read_bytes()).hexdigest(),
                pipeline["markdown_artifact"]["sha256"],
            )

            stale_graph = pipeline["graph_artifact"]["path"]
            evidence["experiment_contract_sha256"] = "0" * 64
            evidence_path.write_text(
                json.dumps(evidence, sort_keys=True), encoding="utf-8"
            )
            exit_code = cli_main(
                [
                    "adjudicate-boundary-benchmark",
                    "--experiment-contract",
                    contract_path.as_posix(),
                    "--evidence",
                    evidence_path.as_posix(),
                    "--output",
                    output_path.as_posix(),
                ]
            )
            self.assertEqual(exit_code, 1)
            failed = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(failed["status"], "fail")
            self.assertIsNone(failed["report_bundle"])
            self.assertIsNone(failed["graph_artifact"])
            self.assertIsNone(failed["markdown_artifact"])
            self.assertTrue((directory / stale_graph).is_file())


if __name__ == "__main__":
    unittest.main()
