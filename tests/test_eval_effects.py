from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.effects import (  # noqa: E402
    EvalEffectError,
    classify_eval_effects,
    validate_eval_effects,
)
from verilator_model_sidecar.cli import main as cli_main  # noqa: E402
from tests.test_native_manifest import _native_eval_manifest  # noqa: E402
from verilator_model_sidecar.semantic import validate_manifest  # noqa: E402


PRODUCER = "Verilator 5.050 2026-07-01 rev v5.050"

IR = r'''
declare void @llvm.memset.p0.i64(ptr, i8, i64, i1)
declare void @_ZN16VerilatedContext9coveragepEv()
declare void @mystery_runtime()

define void @leaf(ptr %state) {
  %value = load i8, ptr %state
  store i8 %value, ptr %state
  call void @llvm.memset.p0.i64(ptr %state, i8 0, i64 1, i1 false)
  ret void
}

define void @clean_root(ptr %state) {
  call void @leaf(ptr %state)
  ret void
}

define void @host_leaf() {
  call void @_ZN16VerilatedContext9coveragepEv()
  ret void
}

define void @host_root() {
  call void @host_leaf()
  ret void
}

define void @unknown_root() {
  call void @mystery_runtime()
  ret void
}

define void @indirect_root(ptr %callee) {
  call void %callee()
  ret void
}
'''


def _contract() -> dict:
    return {
        "schema_version": 1,
        "surface": "verilator_eval_effect_contract",
        "target": "synthetic",
        "policy": {
            "name": "test_policy",
            "classification_precedence": [
                "host_dependent",
                "unknown",
                "proven_device_clean",
            ],
            "permitted_external_symbols": ["llvm.memset.p0.i64"],
            "permitted_external_prefixes": [],
        },
        "regions": {
            "clean": {
                "input": "model",
                "artifact_role": "test",
                "entry": "clean_root",
                "expected_classification": "proven_device_clean",
            },
            "host": {
                "input": "model",
                "artifact_role": "test",
                "entry": "host_root",
                "expected_classification": "host_dependent",
            },
            "indirect": {
                "input": "model",
                "artifact_role": "test",
                "entry": "indirect_root",
                "expected_classification": "unknown",
            },
            "unknown": {
                "input": "model",
                "artifact_role": "test",
                "entry": "unknown_root",
                "expected_classification": "unknown",
            },
        },
    }


def _oracle(observation: dict) -> dict:
    return {
        "schema_version": 1,
        "surface": "verilator_eval_effect_oracle",
        "target": observation["target"],
        "contract_sha256": observation["contract_sha256"],
        "observation_fingerprint": observation["observation_fingerprint"],
        "regions": {
            row["name"]: {
                "artifact_sha256": row["artifact_sha256"],
                "entry": row["entry"],
                "classification": row["classification"],
                "closure_fingerprint": row["closure_fingerprint"],
                "metrics": dict(row["metrics"]),
            }
            for row in observation["regions"]
        },
    }


class EvalEffectsTest(unittest.TestCase):
    def _build(self, root: Path, oracle: dict | None = None) -> dict:
        ir = root / "model.ll"
        ir.write_text(IR, encoding="utf-8")
        return classify_eval_effects(
            ir_inputs={"model": ir},
            contract=_contract(),
            producer=PRODUCER,
            oracle=oracle,
        )

    def test_classifies_clean_host_and_unknown_transitively(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = self._build(Path(first))
            two = self._build(Path(second))

        self.assertEqual(one, two)
        self.assertEqual(one["status"], "resolved")
        regions = {row["name"]: row for row in one["regions"]}
        self.assertEqual(regions["clean"]["classification"], "proven_device_clean")
        self.assertEqual(regions["host"]["classification"], "host_dependent")
        self.assertEqual(regions["unknown"]["classification"], "unknown")
        self.assertEqual(regions["indirect"]["classification"], "unknown")
        self.assertEqual(regions["clean"]["metrics"]["reachable_function_count"], 2)
        self.assertEqual(regions["clean"]["metrics"]["load_instruction_count"], 1)
        self.assertEqual(regions["clean"]["metrics"]["store_instruction_count"], 1)
        self.assertEqual(regions["host"]["host_dependencies"][0]["category"], "runtime_context")
        validate_eval_effects(one)

    def test_oracle_drift_is_a_valid_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as first:
            resolved = self._build(Path(first))
        oracle = _oracle(resolved)
        with tempfile.TemporaryDirectory() as second:
            verified = self._build(Path(second), oracle=oracle)
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["issues"], [])

        drifted = copy.deepcopy(oracle)
        drifted["regions"]["clean"]["metrics"]["reachable_function_count"] += 1
        with tempfile.TemporaryDirectory() as third:
            mismatch = self._build(Path(third), oracle=drifted)
        self.assertEqual(mismatch["status"], "mismatch")
        self.assertEqual(
            mismatch["issues"],
            ["oracle_region_clean_metric_reachable_function_count_mismatch"],
        )

    def test_validator_rejects_tampered_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            observation = self._build(Path(temporary))
        tampered = copy.deepcopy(observation)
        tampered["regions"][0]["classification"] = "host_dependent"
        with self.assertRaisesRegex(EvalEffectError, "inconsistent"):
            validate_eval_effects(tampered)

    def test_cli_verifies_oracle_and_returns_one_for_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir = root / "model.ll"
            contract_path = root / "contract.json"
            resolved_path = root / "resolved.json"
            oracle_path = root / "oracle.json"
            verified_path = root / "verified.json"
            drift_path = root / "drift.json"
            mismatch_path = root / "mismatch.json"
            ir.write_text(IR, encoding="utf-8")
            contract_path.write_text(json.dumps(_contract()), encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "classify-effects",
                        "--contract",
                        str(contract_path),
                        "--ir",
                        f"model={ir}",
                        "--producer",
                        PRODUCER,
                        "--output",
                        str(resolved_path),
                    ]
                ),
                0,
            )
            resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
            oracle = _oracle(resolved)
            oracle_path.write_text(json.dumps(oracle), encoding="utf-8")
            self.assertEqual(
                cli_main(
                    [
                        "classify-effects",
                        "--contract",
                        str(contract_path),
                        "--ir",
                        f"model={ir}",
                        "--producer",
                        PRODUCER,
                        "--oracle",
                        str(oracle_path),
                        "--output",
                        str(verified_path),
                    ]
                ),
                0,
            )
            self.assertEqual(
                json.loads(verified_path.read_text(encoding="utf-8"))["status"],
                "verified",
            )

            oracle["regions"]["clean"]["classification"] = "unknown"
            drift_path.write_text(json.dumps(oracle), encoding="utf-8")
            self.assertEqual(
                cli_main(
                    [
                        "classify-effects",
                        "--contract",
                        str(contract_path),
                        "--ir",
                        f"model={ir}",
                        "--producer",
                        PRODUCER,
                        "--oracle",
                        str(drift_path),
                        "--output",
                        str(mismatch_path),
                    ]
                ),
                1,
            )
            mismatch = json.loads(mismatch_path.read_text(encoding="utf-8"))
            self.assertEqual(mismatch["status"], "mismatch")
            self.assertEqual(
                mismatch["issues"],
                ["oracle_region_clean_classification_mismatch"],
            )

    def test_model_manifest_accepts_analyzed_effect_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            observation = self._build(Path(temporary))
        manifest = {
            "schema_version": 1,
            "surface": "verilator_model_sidecar_manifest",
            "semantic_projection": {
                "entities": [],
                "hierarchy": {
                    "instance_count": 0,
                    "unresolved_count": 0,
                    "instances": [],
                    "unresolved": [],
                },
            },
            "physical_bindings": {"status": "not_analyzed", "bindings": []},
            "checkpoint_projection": {"status": "not_analyzed", "fields": []},
            "coverage_mapping": {"status": "not_analyzed", "mappings": []},
            "eval_effects": observation,
        }
        validate_manifest(manifest)

    def test_replaces_host_llvm_with_native_eval_metadata(self) -> None:
        contract = {
            "schema_version": 2,
            "surface": "verilator_eval_effect_contract",
            "target": "synthetic-hybrid",
            "policy": {
                "name": "hybrid_test_policy",
                "classification_precedence": [
                    "host_dependent",
                    "unknown",
                    "proven_device_clean",
                ],
                "permitted_external_symbols": ["llvm.memset.p0.i64"],
                "permitted_external_prefixes": [],
            },
            "regions": {
                "gpu": {
                    "input": "gpu_ir",
                    "input_kind": "llvm_ir",
                    "artifact_role": "device_lowered_entry_slice",
                    "entry": "clean_root",
                    "expected_classification": "proven_device_clean",
                },
                "host": {
                    "input": "host_native",
                    "input_kind": "verilator_native_eval",
                    "artifact_role": "verilator_final_ast_eval_manifest",
                    "entry": "main_eval",
                    "expected_classification": "host_dependent",
                },
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir = root / "gpu.ll"
            native = root / "native.json"
            contract_path = root / "contract.json"
            output = root / "effects.json"
            ir.write_text(IR, encoding="utf-8")
            native.write_text(
                json.dumps(_native_eval_manifest()),
                encoding="utf-8",
            )
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            observation = classify_eval_effects(
                ir_inputs={"gpu_ir": ir},
                native_inputs={"host_native": native},
                contract=contract,
                producer="Verilator test",
            )
            self.assertEqual(
                cli_main(
                    [
                        "classify-effects",
                        "--contract",
                        str(contract_path),
                        "--ir",
                        f"gpu_ir={ir}",
                        "--native-manifest",
                        f"host_native={native}",
                        "--producer",
                        "Verilator test",
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
            cli_observation = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(cli_observation, observation)
        self.assertEqual(observation["schema_version"], 2)
        self.assertEqual(observation["status"], "resolved")
        regions = {region["name"]: region for region in observation["regions"]}
        self.assertEqual(regions["gpu"]["analysis_authority"], "llvm_ir_instruction_scan")
        self.assertEqual(regions["gpu"]["classification"], "proven_device_clean")
        self.assertEqual(regions["host"]["analysis_authority"], "verilator_final_ast")
        self.assertEqual(regions["host"]["entry_selector"], "main_eval")
        self.assertEqual(regions["host"]["classification"], "host_dependent")
        self.assertEqual(regions["host"]["schedule_semantics"], "not_provided")
        self.assertEqual(
            {artifact["kind"] for artifact in observation["input_artifacts"]},
            {"llvm_ir", "verilator_native_eval"},
        )
        validate_eval_effects(observation)

    def test_native_eval_input_fails_closed(self) -> None:
        contract = {
            "schema_version": 2,
            "surface": "verilator_eval_effect_contract",
            "target": "synthetic-native",
            "policy": {
                "name": "native_test_policy",
                "classification_precedence": [
                    "host_dependent",
                    "unknown",
                    "proven_device_clean",
                ],
                "permitted_external_symbols": [],
                "permitted_external_prefixes": [],
            },
            "regions": {
                "host": {
                    "input": "host_native",
                    "input_kind": "verilator_native_eval",
                    "artifact_role": "verilator_final_ast_eval_manifest",
                    "entry": "main_eval",
                    "expected_classification": "host_dependent",
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "native.json"
            tampered = _native_eval_manifest()
            tampered["eval_regions"]["functions"][1]["classification"] = "unknown"
            native.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(EvalEffectError, "fixed-point"):
                classify_eval_effects(
                    ir_inputs={},
                    native_inputs={"host_native": native},
                    contract=contract,
                    producer="Verilator test",
                )


if __name__ == "__main__":
    unittest.main()
