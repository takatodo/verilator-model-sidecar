from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.boundary_benchmark import adjudicate_boundary_benchmark
from verilator_model_sidecar.boundary_report import (
    build_boundary_report_bundle,
    validate_boundary_report_bundle,
)

def _sha256(value) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load(path: Path) -> dict:
    with path.open() as handle:
        return json.load(handle)


def _load_ibex2188_inputs() -> tuple[dict, dict, dict]:
    contract_path = (
        ROOT / "evidence" / "ibex2188_boundary_profile_inputs_v1" / "experiment_contract.json"
    )
    profile_path = ROOT / "evidence" / "ibex2188_boundary_profile_inputs_v1" / "profile.json"
    contract = _load(contract_path)
    profile = _load(profile_path)
    evidence_path = ROOT / profile["evidence_bundle"]["path"]

    if not evidence_path.is_file():
        raise FileNotFoundError(f"missing evidence bundle: {evidence_path}")

    evidence = _load(evidence_path)

    assert evidence["surface"] == "rtl_boundary_evidence_bundle"
    assert evidence["experiment_contract_sha256"] == _sha256(contract)
    assert profile["experiment_contract_sha256"] == _sha256(contract)
    assert _sha256(evidence) == profile["evidence_bundle"]["sha256"]

    return (
        contract,
        profile,
        evidence,
    )


class Ibex2188BoundaryBenchmarkTest(unittest.TestCase):
    def test_ibex2188_boundary_adjudication_passes(self) -> None:
        contract, profile, evidence = _load_ibex2188_inputs()
        self.assertIn("evidence_bundle", profile)
        self.assertEqual(
            evidence["experiment_contract_sha256"],
            profile["experiment_contract_sha256"],
        )
        self.assertEqual(
            profile["evidence_bundle"]["sha256"],
            _sha256(evidence),
        )
        adjudication = adjudicate_boundary_benchmark(contract, evidence)
        self.assertEqual(adjudication["status"], "pass")
        self.assertEqual(adjudication["issues"], [])
        self.assertEqual(adjudication["verified_identity"]["point_count"], 4)
        self.assertEqual(
            adjudication["ground_truth_analysis"]["revisions"]["bad"]["fail_point_count"],
            1,
        )
        self.assertEqual(
            adjudication["ground_truth_analysis"]["revisions"]["fixed"]["fail_point_count"],
            0,
        )

    def test_ibex2188_boundary_report_is_reproducible(self) -> None:
        contract, _, evidence = _load_ibex2188_inputs()
        adjudication = adjudicate_boundary_benchmark(contract, evidence)
        report_bundle = build_boundary_report_bundle(adjudication)
        validation = validate_boundary_report_bundle(adjudication, report_bundle)
        self.assertEqual(validation["status"], "pass")
        self.assertEqual(validation["issues"], [])
        self.assertEqual(report_bundle["surface"], "rtl_boundary_report_bundle")
