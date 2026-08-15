#!/usr/bin/env python3
"""Build an Ibex #2188 boundary evidence bundle from real runner observations.

Consumes the repository-defined runner's run_result (point_results + trials +
runner identity) together with the experiment contract and semantic manifests,
and produces the sidecar evidence bundle and profile metadata.  The GPU and CPU
projections in every semantic observation are the runner's own values, not a
CPU-copied fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.boundary_benchmark import RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION
from verilator_model_sidecar.sweep_boundary import (
    RTL_BOUNDARY_SCHEMA_VERSION,
    enumerate_sweep_space,
)


def _canonical_json(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _strict_object(path: Path, label: str) -> dict:
    def reject_constant(token: str) -> None:
        raise ValueError(f"{label} contains non-finite JSON token {token}")

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"cannot read JSON object {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return raw


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _validate_runner(raw_runner: Any) -> dict:
    if not isinstance(raw_runner, Mapping) or set(raw_runner) != {
        "status",
        "identity",
        "completed_at",
    }:
        raise ValueError("runner must contain status, identity, and completed_at")
    if raw_runner.get("status") != "pass":
        raise ValueError("runner status must be pass")
    return {
        "status": "pass",
        "identity": _nonempty_string(raw_runner.get("identity"), "runner identity"),
        "completed_at": _nonempty_string(
            raw_runner.get("completed_at"), "runner completed_at"
        ),
    }


def _ground_truth_and_semantics(
    contract: Mapping[str, Any],
    point_results: Any,
) -> tuple[list[dict], list[dict]]:
    enumeration = enumerate_sweep_space(contract["sweep_space"])
    parameters_by_point = {
        point["point_id"]: point["parameters"] for point in enumeration["points"]
    }
    oracle_field = contract["target"]["oracle_field"]
    if not isinstance(point_results, list) or not point_results:
        raise ValueError("point_results must be a nonempty list")
    ground_truth_rows: list[dict] = []
    semantic_rows: list[dict] = []
    seen: set[str] = set()
    for row in point_results:
        if not isinstance(row, Mapping):
            raise ValueError("point result must be an object")
        point_id = row.get("point_id")
        revisions = row.get("revisions")
        if point_id not in parameters_by_point or point_id in seen:
            raise ValueError(f"point result {point_id!r} is unknown or duplicated")
        seen.add(point_id)
        if not isinstance(revisions, Mapping) or set(revisions) != {"bad", "fixed"}:
            raise ValueError(f"point result {point_id} must contain bad and fixed")
        oracles: dict[str, int] = {}
        normalized_revisions: dict[str, dict] = {}
        for label in ("bad", "fixed"):
            revision = revisions[label]
            if not isinstance(revision, Mapping) or set(revision) != {"cpu", "gpu"}:
                raise ValueError(f"point result {point_id} {label} must contain cpu and gpu")
            cpu = revision.get("cpu")
            gpu = revision.get("gpu")
            if not isinstance(cpu, Mapping) or not isinstance(gpu, Mapping):
                raise ValueError(f"point result {point_id} {label} projections are invalid")
            if set(cpu) != set(gpu):
                raise ValueError(f"point result {point_id} {label} cpu/gpu keys differ")
            oracle = cpu.get(oracle_field)
            if not isinstance(oracle, int) or isinstance(oracle, bool) or oracle not in (0, 1):
                raise ValueError(f"point result {point_id} {label} oracle is invalid")
            normalized_revisions[label] = {"cpu": dict(cpu), "gpu": dict(gpu)}
            oracles[label] = oracle
        ground_truth_rows.append(
            {
                "point_id": point_id,
                "parameters": dict(parameters_by_point[point_id]),
                "bad_oracle": oracles["bad"],
                "fixed_oracle": oracles["fixed"],
            }
        )
        semantic_rows.append({"point_id": point_id, "revisions": normalized_revisions})
    if set(seen) != set(parameters_by_point):
        raise ValueError("point results must cover every sweep point")
    return ground_truth_rows, semantic_rows


def build_evidence(
    experiment_contract_path: Path,
    run_result_path: Path,
    manifest_bad_path: Path,
    manifest_fixed_path: Path,
    contract_sha256: str | None,
) -> tuple[dict, dict]:
    contract = _strict_object(experiment_contract_path, "experiment contract")
    run_result = _strict_object(run_result_path, "run result")
    if run_result.get("surface") not in (None, "ibex2188_boundary_run_result"):
        raise ValueError("run result surface is invalid")
    if set(run_result) != {"runner", "point_results", "trials"}:
        raise ValueError("run result must contain runner, point_results, and trials")
    if contract_sha256 is None:
        contract_sha256 = _sha256(contract)
    else:
        expected = _sha256(contract)
        if expected != contract_sha256:
            raise ValueError("experiment_contract_hash_mismatch")

    ground_truth_rows, semantic_rows = _ground_truth_and_semantics(
        contract, run_result["point_results"]
    )
    ground_truth = {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": "rtl_boundary_ground_truth",
        "sweep_space_sha256": contract["sweep_space_sha256"],
        "observations": ground_truth_rows,
    }
    evidence = {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": "rtl_boundary_evidence_bundle",
        "experiment_contract_sha256": contract_sha256,
        "runner": _validate_runner(run_result["runner"]),
        "semantic_manifests": {
            "bad": _load(manifest_bad_path),
            "fixed": _load(manifest_fixed_path),
        },
        "ground_truth": ground_truth,
        "semantic_observations": semantic_rows,
        "trials": list(run_result["trials"]),
    }
    return evidence, {"points": len(ground_truth_rows), "trials": len(run_result["trials"])}


def build_profile_from_artifacts(profile_path: Path, evidence_path: Path, status: str) -> dict:
    profile = _load(profile_path)
    profile["status"] = status
    profile["evidence_bundle"] = {
        "path": str(evidence_path.resolve().relative_to(ROOT.resolve())),
        "sha256": _sha256(_load(evidence_path)),
    }
    profile["evidence_bundle_sha256"] = profile["evidence_bundle"]["sha256"]
    return profile


def main() -> int:
    root = ROOT
    default_base = root / "evidence" / "ibex2188_boundary_profile_inputs_v1"
    parser = argparse.ArgumentParser(
        description="Build the ibex2188 boundary evidence bundle from real runner observations."
    )
    parser.add_argument(
        "--experiment-contract",
        type=Path,
        default=default_base / "experiment_contract.json",
    )
    parser.add_argument(
        "--run-result",
        type=Path,
        required=True,
        help="run_result.json produced by build_ibex2188_boundary_run_result.py",
    )
    parser.add_argument(
        "--manifest-bad",
        type=Path,
        default=default_base / "semantic_manifest_bad.json",
    )
    parser.add_argument(
        "--manifest-fixed",
        type=Path,
        default=default_base / "semantic_manifest_fixed.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_base / "evidence_bundle.json",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=default_base / "profile.json",
    )
    parser.add_argument(
        "--update-profile",
        action="store_true",
        help="Update profile.json status and evidence_bundle metadata",
    )
    parser.add_argument(
        "--profile-status",
        type=str,
        default="experiment_contract_and_evidence_ready",
    )
    parser.add_argument(
        "--contract-hash",
        type=str,
        default=None,
    )
    args = parser.parse_args()

    evidence, summary = build_evidence(
        experiment_contract_path=args.experiment_contract,
        run_result_path=args.run_result,
        manifest_bad_path=args.manifest_bad,
        manifest_fixed_path=args.manifest_fixed,
        contract_sha256=args.contract_hash,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(args.output, evidence)

    if args.update_profile:
        profile = build_profile_from_artifacts(
            profile_path=args.profile,
            evidence_path=args.output,
            status=args.profile_status,
        )
        _write_json(args.profile, profile)

    print(f"experiment_contract_sha256={evidence['experiment_contract_sha256']}")
    print(f"evidence_bundle={args.output}")
    print(f"evidence_bundle_sha256={_sha256(evidence)}")
    print(f"points={summary['points']} trials={summary['trials']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
