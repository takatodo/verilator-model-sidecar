"""Command-line interface for the bounded Verilator model sidecar."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from . import __version__
from .physical import (
    PhysicalProbeError,
    probe_physical_layout,
    write_layout_observation,
)
from .opentitan_evidence import (
    OpenTitanEvidenceError,
    adjudication_input_error,
    adjudicate_external_evidence,
    file_sha256,
    format_adjudication_report,
    format_adjudication_summary_report,
    format_output_validation_report,
    format_target_contract_report,
    read_strict_json_object,
    summarize_adjudications,
    validate_adjudication_document,
    validate_adjudication_run_spec,
    validate_adjudication_summary_document,
    validate_target_contract_document,
    write_json_atomic,
    write_text_atomic,
)
from .native import (
    NativeManifestError,
    project_native_checkpoint,
    verify_native_adapter,
    write_native_checkpoint,
    write_native_verification,
)
from .effects import (
    EvalEffectError,
    classify_eval_effects,
    write_eval_effects,
)
from .semantic import (
    SidecarError,
    analyze_manifest,
    capture_manifest,
    validate_manifest,
    write_manifest,
)
from .boundary_benchmark import (
    RTL_BOUNDARY_ADJUDICATION_SURFACE,
    RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
    BoundaryBenchmarkError,
    adjudicate_boundary_benchmark,
)
from .boundary_report import (
    RTL_BOUNDARY_PIPELINE_RESULT_SURFACE,
    build_boundary_report_bundle,
)
from .sweep_boundary import (
    RTL_BOUNDARY_SELECTOR_RESPONSE_SURFACE,
    SweepBoundaryError,
    select_boundary_points,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verilator-model-sidecar")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser(
        "capture", help="capture one semantic manifest from Verilator 5.050"
    )
    capture.add_argument("--source-root", type=Path, default=Path("."))
    capture.add_argument("--top", required=True)
    capture.add_argument("--source", type=Path, action="append", required=True)
    capture.add_argument("--work-dir", type=Path)
    capture.add_argument("--verilator", default="verilator")
    capture.add_argument("--output", type=Path, required=True)

    analyze = subparsers.add_parser(
        "analyze", help="analyze existing Verilator JSON and generated C++"
    )
    analyze.add_argument("--source-root", type=Path, default=Path("."))
    analyze.add_argument("--top", required=True)
    analyze.add_argument("--tree", type=Path, required=True)
    analyze.add_argument("--meta", type=Path, required=True)
    analyze.add_argument("--obj-dir", type=Path, required=True)
    analyze.add_argument(
        "--producer",
        required=True,
        help="exact output of the Verilator producer's --version",
    )
    analyze.add_argument(
        "--effects",
        type=Path,
        help="optional precomputed eval-effect observation",
    )
    analyze.add_argument(
        "--adapter",
        type=Path,
        help="optional adapter JSON whose semantic signals must be resolved",
    )
    analyze.add_argument(
        "--layout",
        type=Path,
        help="optional measured C++ layout observation",
    )
    analyze.add_argument(
        "--physical-oracle",
        type=Path,
        help="optional expected state image and physical offsets",
    )
    analyze.add_argument(
        "--coverage-contract",
        type=Path,
        help="optional toggle coverage array and semantic mapping contract",
    )
    analyze.add_argument(
        "--native-manifest",
        type=Path,
        help="Verilator-native lowering authority required by coverage mapping",
    )
    analyze.add_argument(
        "--coverage-oracle",
        type=Path,
        help="optional expected coverage layout, counts, and fingerprints",
    )
    analyze.add_argument("--output", type=Path, required=True)

    probe = subparsers.add_parser(
        "probe-layout", help="measure generated C++ state with sizeof/offsetof"
    )
    probe.add_argument("--obj-dir", type=Path, required=True)
    probe.add_argument("--adapter", type=Path, required=True)
    probe.add_argument(
        "--native-manifest",
        type=Path,
        help="optional Verilator-native authority for generated storage bindings",
    )
    probe.add_argument(
        "--coverage-contract",
        type=Path,
        help="optional coverage arrays to include in the ABI measurement",
    )
    probe.add_argument(
        "--producer",
        required=True,
        help="exact output of the Verilator producer's --version",
    )
    probe.add_argument("--cxx", default="c++")
    probe.add_argument("--verilator", default="verilator")
    probe.add_argument("--verilator-include", type=Path)
    probe.add_argument("--output", type=Path, required=True)

    effects = subparsers.add_parser(
        "classify-effects",
        help="classify explicit LLVM eval closures without invoking tools",
    )
    effects.add_argument("--contract", type=Path, required=True)
    effects.add_argument(
        "--ir",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="named LLVM IR input; repeat for each LLVM Contract input",
    )
    effects.add_argument(
        "--native-manifest",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="named native manifest; repeat for each native eval Contract input",
    )
    effects.add_argument(
        "--producer",
        required=True,
        help="exact output of the Verilator producer's --version",
    )
    effects.add_argument("--oracle", type=Path)
    effects.add_argument("--output", type=Path, required=True)

    native = subparsers.add_parser(
        "verify-native",
        help="verify adapter signals from a Verilator-native model manifest",
    )
    native.add_argument("--manifest", type=Path, required=True)
    native.add_argument("--adapter", type=Path, required=True)
    native.add_argument("--output", type=Path, required=True)

    checkpoint = subparsers.add_parser(
        "project-native-checkpoint",
        help="project native savable field membership onto stored instances",
    )
    checkpoint.add_argument("--manifest", type=Path, required=True)
    checkpoint.add_argument("--output", type=Path, required=True)

    opentitan = subparsers.add_parser(
        "adjudicate-opentitan-evidence",
        help="statically validate externally generated OpenTitan RTL evidence",
    )
    opentitan.add_argument("--target-contract", type=Path, required=True)
    opentitan.add_argument("--evidence", type=Path, required=True)
    opentitan.add_argument(
        "--evidence-root",
        type=Path,
        help="root for relative artifact paths; defaults to evidence file directory",
    )
    opentitan.add_argument("--output", type=Path, required=True)
    opentitan.add_argument("--report", type=Path, required=True)

    opentitan_set = subparsers.add_parser(
        "adjudicate-opentitan-evidence-set",
        help="summarize multiple externally generated OpenTitan evidence bundles",
    )
    opentitan_set.add_argument("--target-contract", type=Path, required=True)
    opentitan_set.add_argument("--evidence", type=Path, action="append", required=True)
    opentitan_set.add_argument(
        "--evidence-root",
        type=Path,
        help="root for relative artifact paths; defaults to each evidence file directory",
    )
    opentitan_set.add_argument("--output", type=Path, required=True)
    opentitan_set.add_argument("--report", type=Path, required=True)

    opentitan_contract = subparsers.add_parser(
        "validate-opentitan-target-contract",
        help="statically validate an OpenTitan regression target contract",
    )
    opentitan_contract.add_argument("--target-contract", type=Path, required=True)
    opentitan_contract.add_argument("--output", type=Path, required=True)
    opentitan_contract.add_argument("--report", type=Path, required=True)

    opentitan_run = subparsers.add_parser(
        "adjudicate-opentitan-run-spec",
        help="run static OpenTitan evidence adjudication from a checked run spec",
    )
    opentitan_run.add_argument("--run-spec", type=Path, required=True)
    opentitan_run.add_argument("--output", type=Path, required=True)
    opentitan_run.add_argument("--report", type=Path, required=True)

    opentitan_adjudication_output = subparsers.add_parser(
        "validate-opentitan-adjudication",
        help="validate a stored OpenTitan adjudication JSON output",
    )
    opentitan_adjudication_output.add_argument("--adjudication", type=Path, required=True)
    opentitan_adjudication_output.add_argument("--output", type=Path, required=True)
    opentitan_adjudication_output.add_argument("--report", type=Path, required=True)

    opentitan_summary_output = subparsers.add_parser(
        "validate-opentitan-adjudication-summary",
        help="validate a stored OpenTitan adjudication summary JSON output",
    )
    opentitan_summary_output.add_argument("--summary", type=Path, required=True)
    opentitan_summary_output.add_argument("--output", type=Path, required=True)
    opentitan_summary_output.add_argument("--report", type=Path, required=True)

    boundary_benchmark = subparsers.add_parser(
        "adjudicate-boundary-benchmark",
        help="adjudicate existing finite-grid RTL boundary evidence without running a DUT",
    )
    boundary_benchmark.add_argument(
        "--experiment-contract", type=Path, required=True
    )
    boundary_benchmark.add_argument("--evidence", type=Path, required=True)
    boundary_benchmark.add_argument("--output", type=Path, required=True)

    boundary_select = subparsers.add_parser(
        "select-boundary-points",
        help="select the next finite-grid boundary points from public feedback only",
    )
    boundary_select.add_argument("--sweep-space", type=Path, required=True)
    boundary_select.add_argument("--policy", type=Path, required=True)
    boundary_select.add_argument("--completed-public-batches", type=Path)
    boundary_select.add_argument("--requested-count", type=int, required=True)
    boundary_select.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser(
        "validate", help="validate a generated model manifest"
    )
    validate.add_argument("manifest", type=Path)
    return parser


def _capture(arguments: argparse.Namespace) -> int:
    if arguments.work_dir is not None:
        manifest = capture_manifest(
            source_root=arguments.source_root,
            top=arguments.top,
            sources=arguments.source,
            work_dir=arguments.work_dir,
            verilator=arguments.verilator,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="verilator-model-sidecar-") as work:
            manifest = capture_manifest(
                source_root=arguments.source_root,
                top=arguments.top,
                sources=arguments.source,
                work_dir=Path(work),
                verilator=arguments.verilator,
            )
    write_manifest(arguments.output, manifest)
    print(
        f"wrote {arguments.output}: "
        f"{manifest['semantic_projection']['entity_count']} semantic entities"
    )
    return 0


def _validate(arguments: argparse.Namespace) -> int:
    manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    print(f"valid: {arguments.manifest}")
    return 0


def _read_object(path: Path, description: str) -> dict:
    value = _read_strict_json(path, description)
    if not isinstance(value, dict):
        raise SidecarError(f"{description} JSON root must be an object")
    return value


def _read_list(path: Path, description: str) -> list:
    value = _read_strict_json(path, description)
    if not isinstance(value, list):
        raise SidecarError(f"{description} JSON root must be a list")
    return value


def _read_strict_json(path: Path, description: str):
    def reject_constant(token: str) -> None:
        raise SidecarError(f"{description} contains non-finite JSON token {token}")

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SidecarError(
                    f"{description} contains duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )


def _probe_layout(arguments: argparse.Namespace) -> int:
    adapter = _read_object(arguments.adapter, "adapter")
    native_manifest = (
        _read_object(arguments.native_manifest, "native manifest")
        if arguments.native_manifest is not None
        else None
    )
    coverage_contract = (
        _read_object(arguments.coverage_contract, "coverage contract")
        if arguments.coverage_contract is not None
        else None
    )
    observation = probe_physical_layout(
        obj_dir=arguments.obj_dir,
        adapter=adapter,
        producer=arguments.producer,
        native_manifest=native_manifest,
        coverage_contract=coverage_contract,
        cxx=arguments.cxx,
        verilator_include=arguments.verilator_include,
        verilator=arguments.verilator,
    )
    write_layout_observation(arguments.output, observation)
    state_image = observation["state_image"]
    print(
        f"wrote {arguments.output}: {state_image['bytes']} bytes, "
        f"root offset {state_image['root_offset_bytes']}, "
        f"{observation['binding_count']} bindings"
        + (
            f", {observation['coverage_region_count']} coverage region"
            if observation.get("coverage_region_count")
            else ""
        )
    )
    return 0


def _named_paths(values: Sequence[str], description: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise SidecarError(f"{description} input must use NAME=PATH: {value!r}")
        if name in result:
            raise SidecarError(f"duplicate {description} input name: {name}")
        result[name] = Path(raw_path)
    return result


def _classify_effects(arguments: argparse.Namespace) -> int:
    contract = _read_object(arguments.contract, "eval-effect contract")
    oracle = (
        _read_object(arguments.oracle, "eval-effect oracle")
        if arguments.oracle is not None
        else None
    )
    observation = classify_eval_effects(
        ir_inputs=_named_paths(arguments.ir, "LLVM IR"),
        native_inputs=_named_paths(arguments.native_manifest, "native manifest"),
        contract=contract,
        producer=arguments.producer,
        oracle=oracle,
    )
    write_eval_effects(arguments.output, observation)
    counts = observation["classification_counts"]
    print(
        f"wrote {arguments.output}: {observation['region_count']} regions, "
        f"clean={counts['proven_device_clean']}, "
        f"host={counts['host_dependent']}, unknown={counts['unknown']}, "
        f"status={observation['status']}"
    )
    return 1 if observation["status"] == "mismatch" else 0


def _verify_native(arguments: argparse.Namespace) -> int:
    manifest = _read_object(arguments.manifest, "native manifest")
    adapter = _read_object(arguments.adapter, "adapter")
    report = verify_native_adapter(manifest, adapter)
    write_native_verification(arguments.output, report)
    print(
        f"wrote {arguments.output}: {report['matched_count']}/"
        f"{report['signal_count']} adapter signals matched"
    )
    return 0 if report["status"] == "matched" else 1


def _project_native_checkpoint(arguments: argparse.Namespace) -> int:
    manifest = _read_object(arguments.manifest, "native manifest")
    report = project_native_checkpoint(manifest)
    write_native_checkpoint(arguments.output, report)
    print(
        f"wrote {arguments.output}: {report['stored_field_occurrence_count']} "
        f"stored field occurrences, status={report['status']}"
    )
    return 0 if report["status"] == "projected" else 1


def _adjudicate_opentitan_evidence(arguments: argparse.Namespace) -> int:
    evidence_root = (
        arguments.evidence_root
        if arguments.evidence_root is not None
        else arguments.evidence.parent
    )
    input_sha256: dict[str, str] = {}
    for name, path in (
        ("target_contract", arguments.target_contract),
        ("evidence", arguments.evidence),
    ):
        try:
            input_sha256[name] = file_sha256(path)
        except OSError:
            pass
    try:
        contract = read_strict_json_object(arguments.target_contract)
        evidence = read_strict_json_object(arguments.evidence)
        adjudication = adjudicate_external_evidence(
            target_contract=contract,
            evidence=evidence,
            evidence_root=evidence_root,
        )
    except (OSError, OpenTitanEvidenceError) as error:
        adjudication = adjudication_input_error(str(error))
    adjudication["input_sha256"] = input_sha256
    write_json_atomic(arguments.output, adjudication)
    write_text_atomic(arguments.report, format_adjudication_report(adjudication))
    print(
        f"wrote {arguments.output}: status={adjudication['status']}, "
        f"issues={adjudication['issue_count']}"
    )
    return 0 if adjudication["status"] == "pass" else 1


def _adjudicate_opentitan_evidence_set(arguments: argparse.Namespace) -> int:
    rows: list[dict[str, object]] = []
    target_contract_report = None
    try:
        contract_sha256 = file_sha256(arguments.target_contract)
        contract = read_strict_json_object(arguments.target_contract)
        target_contract_report = validate_target_contract_document(contract)
    except (OSError, OpenTitanEvidenceError) as error:
        contract_sha256 = None
        contract = None
        for evidence_path in arguments.evidence:
            adjudication = adjudication_input_error(str(error))
            input_sha256: dict[str, str] = {}
            try:
                input_sha256["evidence"] = file_sha256(evidence_path)
            except OSError:
                pass
            adjudication["input_sha256"] = input_sha256
            rows.append(
                {
                    "evidence_path": evidence_path.as_posix(),
                    "adjudication": adjudication,
                }
            )
    if contract is not None:
        for evidence_path in arguments.evidence:
            evidence_root = (
                arguments.evidence_root
                if arguments.evidence_root is not None
                else evidence_path.parent
            )
            input_sha256 = {
                "target_contract": contract_sha256,
            }
            try:
                input_sha256["evidence"] = file_sha256(evidence_path)
                evidence = read_strict_json_object(evidence_path)
                adjudication = adjudicate_external_evidence(
                    target_contract=contract,
                    evidence=evidence,
                    evidence_root=evidence_root,
                )
            except (OSError, OpenTitanEvidenceError) as error:
                adjudication = adjudication_input_error(str(error))
            adjudication["input_sha256"] = input_sha256
            rows.append(
                {
                    "evidence_path": evidence_path.as_posix(),
                    "adjudication": adjudication,
                }
            )
    summary = summarize_adjudications(
        rows,
        target_contract_sha256=contract_sha256,
    )
    if target_contract_report is not None:
        summary["target_contract"] = target_contract_report
    write_json_atomic(arguments.output, summary)
    write_text_atomic(arguments.report, format_adjudication_summary_report(summary))
    print(
        f"wrote {arguments.output}: status={summary['status']}, "
        f"pass={summary['pass_count']}, fail={summary['fail_count']}"
    )
    return 0 if summary["status"] == "pass" else 1


def _run_spec_path(base: Path, relative: str) -> Path:
    root = base.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise OpenTitanEvidenceError(
            f"run spec path escapes its declared root: {relative}"
        ) from error
    return resolved


def _adjudicate_opentitan_run_spec(arguments: argparse.Namespace) -> int:
    input_sha256: dict[str, str] = {}
    rows: list[dict[str, object]] = []
    run_report: dict[str, object] | None = None
    contract_sha256: str | None = None
    target_contract_report = None
    try:
        input_sha256["run_spec"] = file_sha256(arguments.run_spec)
        run_spec = read_strict_json_object(arguments.run_spec)
        run_report = validate_adjudication_run_spec(run_spec)
    except (OSError, OpenTitanEvidenceError) as error:
        rows.append(
            {
                "evidence_path": arguments.run_spec.as_posix(),
                "adjudication": adjudication_input_error(str(error)),
            }
        )
        summary = summarize_adjudications(rows, target_contract_sha256=None)
        summary["input_sha256"].update(input_sha256)
        write_json_atomic(arguments.output, summary)
        write_text_atomic(arguments.report, format_adjudication_summary_report(summary))
        print(
            f"wrote {arguments.output}: status={summary['status']}, "
            f"pass={summary['pass_count']}, fail={summary['fail_count']}"
        )
        return 1

    if run_report["status"] == "pass":
        base = arguments.run_spec.parent.resolve()
        try:
            target_contract_path = _run_spec_path(base, run_spec["target_contract"])
            evidence_root = _run_spec_path(base, run_spec.get("evidence_root", "."))
            contract_sha256 = file_sha256(target_contract_path)
            contract = read_strict_json_object(target_contract_path)
            target_contract_report = validate_target_contract_document(contract)
        except (OSError, OpenTitanEvidenceError) as error:
            contract = None
            for evidence_path in run_spec["evidence"]:
                rows.append(
                    {
                        "evidence_path": evidence_path,
                        "adjudication": adjudication_input_error(str(error)),
                    }
                )
        if contract is not None:
            for evidence_path in run_spec["evidence"]:
                input_hashes = {
                    "run_spec": input_sha256["run_spec"],
                    "target_contract": contract_sha256,
                }
                try:
                    resolved_evidence = _run_spec_path(evidence_root, evidence_path)
                    input_hashes["evidence"] = file_sha256(resolved_evidence)
                    evidence = read_strict_json_object(resolved_evidence)
                    adjudication = adjudicate_external_evidence(
                        target_contract=contract,
                        evidence=evidence,
                        evidence_root=evidence_root,
                    )
                except (OSError, OpenTitanEvidenceError) as error:
                    adjudication = adjudication_input_error(str(error))
                adjudication["input_sha256"] = input_hashes
                rows.append(
                    {
                        "evidence_path": evidence_path,
                        "adjudication": adjudication,
                    }
                )
    else:
        rows.append(
            {
                "evidence_path": arguments.run_spec.as_posix(),
                "adjudication": adjudication_input_error("run spec validation failed"),
            }
        )
    summary = summarize_adjudications(rows, target_contract_sha256=contract_sha256)
    summary["input_sha256"].update(input_sha256)
    summary["run_spec"] = run_report
    if target_contract_report is not None:
        summary["target_contract"] = target_contract_report
    write_json_atomic(arguments.output, summary)
    write_text_atomic(arguments.report, format_adjudication_summary_report(summary))
    print(
        f"wrote {arguments.output}: status={summary['status']}, "
        f"pass={summary['pass_count']}, fail={summary['fail_count']}"
    )
    return 0 if summary["status"] == "pass" else 1


def _validate_opentitan_target_contract(arguments: argparse.Namespace) -> int:
    input_sha256: dict[str, str] = {}
    try:
        input_sha256["target_contract"] = file_sha256(arguments.target_contract)
        contract = read_strict_json_object(arguments.target_contract)
        report = validate_target_contract_document(contract)
    except (OSError, OpenTitanEvidenceError) as error:
        report = {
            "schema_version": 1,
            "surface": "opentitan_regression_target_contract_report",
            "status": "fail",
            "checks": [
                {
                    "name": "input_format",
                    "status": "fail",
                    "issue_codes": ["adjudication_input_error"],
                }
            ],
            "target_count": 0,
            "issue_count": 1,
            "issues": [
                {
                    "code": "adjudication_input_error",
                    "detail": str(error),
                }
            ],
            "targets": [],
        }
    report["input_sha256"] = input_sha256
    write_json_atomic(arguments.output, report)
    write_text_atomic(arguments.report, format_target_contract_report(report))
    print(
        f"wrote {arguments.output}: status={report['status']}, "
        f"targets={report['target_count']}, issues={report['issue_count']}"
    )
    return 0 if report["status"] == "pass" else 1


def _validate_opentitan_adjudication_output(arguments: argparse.Namespace) -> int:
    input_sha256: dict[str, str] = {}
    try:
        input_sha256["adjudication"] = file_sha256(arguments.adjudication)
        adjudication = read_strict_json_object(arguments.adjudication)
        report = validate_adjudication_document(adjudication)
    except (OSError, OpenTitanEvidenceError) as error:
        report = {
            "schema_version": 1,
            "surface": "opentitan_regression_adjudication_validation_report",
            "status": "fail",
            "issue_count": 1,
            "issues": [
                {
                    "code": "adjudication_input_error",
                    "detail": str(error),
                }
            ],
        }
    report["input_sha256"] = input_sha256
    write_json_atomic(arguments.output, report)
    write_text_atomic(arguments.report, format_output_validation_report(report))
    print(
        f"wrote {arguments.output}: status={report['status']}, "
        f"issues={report['issue_count']}"
    )
    return 0 if report["status"] == "pass" else 1


def _validate_opentitan_adjudication_summary_output(arguments: argparse.Namespace) -> int:
    input_sha256: dict[str, str] = {}
    try:
        input_sha256["summary"] = file_sha256(arguments.summary)
        summary = read_strict_json_object(arguments.summary)
        report = validate_adjudication_summary_document(summary)
    except (OSError, OpenTitanEvidenceError) as error:
        report = {
            "schema_version": 1,
            "surface": "opentitan_regression_adjudication_summary_validation_report",
            "status": "fail",
            "issue_count": 1,
            "issues": [
                {
                    "code": "adjudication_input_error",
                    "detail": str(error),
                }
            ],
        }
    report["input_sha256"] = input_sha256
    write_json_atomic(arguments.output, report)
    write_text_atomic(arguments.report, format_output_validation_report(report))
    print(
        f"wrote {arguments.output}: status={report['status']}, "
        f"issues={report['issue_count']}"
    )
    return 0 if report["status"] == "pass" else 1


def _boundary_input_failure(message: str) -> dict[str, object]:
    return {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_ADJUDICATION_SURFACE,
        "status": "fail",
        "issues": [{"code": "input_read_error", "message": message}],
        "input_canonical_sha256": {
            "experiment_contract": None,
            "evidence_bundle": None,
        },
    }


def _adjudicate_boundary_benchmark(arguments: argparse.Namespace) -> int:
    input_file_sha256: dict[str, str | None] = {
        "experiment_contract": None,
        "evidence_bundle": None,
    }
    for name, path in (
        ("experiment_contract", arguments.experiment_contract),
        ("evidence_bundle", arguments.evidence),
    ):
        try:
            input_file_sha256[name] = file_sha256(path)
        except OSError:
            pass
    try:
        contract = read_strict_json_object(arguments.experiment_contract)
        evidence = read_strict_json_object(arguments.evidence)
        adjudication = adjudicate_boundary_benchmark(contract, evidence)
    except (OSError, OpenTitanEvidenceError) as error:
        adjudication = _boundary_input_failure(str(error))
    adjudication["input_file_sha256"] = input_file_sha256

    report_bundle = None
    graph_artifact = None
    markdown_artifact = None
    if adjudication["status"] == "pass":
        try:
            report_bundle = build_boundary_report_bundle(adjudication)
            graph_name = (
                f"{arguments.output.stem}-{report_bundle['graph_sha256']}.svg"
            )
            markdown_name = (
                f"{arguments.output.stem}-{report_bundle['markdown_sha256']}.md"
            )
            graph_path = arguments.output.parent / graph_name
            markdown_path = arguments.output.parent / markdown_name
            write_text_atomic(graph_path, report_bundle["graph_svg"])
            write_text_atomic(markdown_path, report_bundle["markdown_report"])
            graph_artifact = {
                "path": graph_name,
                "sha256": report_bundle["graph_sha256"],
            }
            markdown_artifact = {
                "path": markdown_name,
                "sha256": report_bundle["markdown_sha256"],
            }
        except (OSError, BoundaryBenchmarkError) as error:
            adjudication = _boundary_input_failure(
                f"report generation failed: {error}"
            )
            adjudication["input_file_sha256"] = input_file_sha256
            report_bundle = None
            graph_artifact = None
            markdown_artifact = None
    pipeline = {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_PIPELINE_RESULT_SURFACE,
        "status": adjudication["status"],
        "adjudication": adjudication,
        "report_bundle": report_bundle,
        "graph_artifact": graph_artifact,
        "markdown_artifact": markdown_artifact,
    }
    write_json_atomic(arguments.output, pipeline)
    print(
        f"wrote {arguments.output}: status={pipeline['status']}, "
        f"issues={len(adjudication['issues'])}"
    )
    return 0 if pipeline["status"] == "pass" else 1


def _select_boundary_points(arguments: argparse.Namespace) -> int:
    sweep_space = _read_object(arguments.sweep_space, "sweep space")
    policy = _read_object(arguments.policy, "policy")
    completed = (
        _read_list(arguments.completed_public_batches, "completed public batches")
        if arguments.completed_public_batches is not None
        else []
    )
    selected = select_boundary_points(
        sweep_space,
        policy,
        completed,
        arguments.requested_count,
    )
    output = {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_SELECTOR_RESPONSE_SURFACE,
        "selected_point_ids": list(selected),
    }
    write_json_atomic(arguments.output, output)
    print(
        f"wrote {arguments.output}: selected_point_count={len(selected)}"
    )
    return 0


def _analyze(arguments: argparse.Namespace) -> int:
    adapter = (
        _read_object(arguments.adapter, "adapter")
        if arguments.adapter is not None
        else None
    )
    layout = (
        _read_object(arguments.layout, "layout observation")
        if arguments.layout is not None
        else None
    )
    physical_oracle = (
        _read_object(arguments.physical_oracle, "physical oracle")
        if arguments.physical_oracle is not None
        else None
    )
    coverage_contract = (
        _read_object(arguments.coverage_contract, "coverage contract")
        if arguments.coverage_contract is not None
        else None
    )
    coverage_oracle = (
        _read_object(arguments.coverage_oracle, "coverage oracle")
        if arguments.coverage_oracle is not None
        else None
    )
    native_manifest = (
        _read_object(arguments.native_manifest, "native manifest")
        if arguments.native_manifest is not None
        else None
    )
    eval_effect_observation = (
        _read_object(arguments.effects, "eval-effect observation")
        if arguments.effects is not None
        else None
    )
    manifest = analyze_manifest(
        source_root=arguments.source_root,
        top=arguments.top,
        tree_path=arguments.tree,
        meta_path=arguments.meta,
        obj_dir=arguments.obj_dir,
        producer=arguments.producer,
        adapter=adapter,
        layout_observation=layout,
        physical_oracle=physical_oracle,
        coverage_contract=coverage_contract,
        native_manifest=native_manifest,
        coverage_oracle=coverage_oracle,
        eval_effect_observation=eval_effect_observation,
    )
    write_manifest(arguments.output, manifest)
    hierarchy = manifest["semantic_projection"]["hierarchy"]
    summary = (
        f"wrote {arguments.output}: {hierarchy['instance_count']} instances"
    )
    verification = manifest.get("adapter_verification")
    if verification is not None:
        summary += (
            f", {verification['matched_count']}/{verification['signal_count']} "
            "adapter signals matched"
        )
    physical = manifest["physical_bindings"]
    if physical["status"] != "not_analyzed":
        summary += (
            f", physical bindings {physical['status']} "
            f"({physical['binding_count']} bindings)"
        )
    coverage = manifest["coverage_mapping"]
    if coverage["status"] != "not_analyzed":
        summary += (
            f", coverage mapping {coverage['status']} "
            f"({coverage['metrics']['semantic_observation_count']} semantic to "
            f"{coverage['metrics']['raw_word_count']} physical words)"
        )
    effects = manifest["eval_effects"]
    if effects["status"] != "not_analyzed":
        counts = effects["classification_counts"]
        summary += (
            f", eval effects {effects['status']} "
            f"(clean={counts['proven_device_clean']}, "
            f"host={counts['host_dependent']}, unknown={counts['unknown']})"
        )
    print(summary)
    mismatch = verification is not None and verification["status"] != "matched"
    mismatch = mismatch or physical["status"] == "mismatch"
    mismatch = mismatch or coverage["status"] == "mismatch"
    mismatch = mismatch or effects["status"] == "mismatch"
    return 1 if mismatch else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "capture":
            return _capture(arguments)
        if arguments.command == "analyze":
            return _analyze(arguments)
        if arguments.command == "probe-layout":
            return _probe_layout(arguments)
        if arguments.command == "classify-effects":
            return _classify_effects(arguments)
        if arguments.command == "verify-native":
            return _verify_native(arguments)
        if arguments.command == "project-native-checkpoint":
            return _project_native_checkpoint(arguments)
        if arguments.command == "adjudicate-opentitan-evidence":
            return _adjudicate_opentitan_evidence(arguments)
        if arguments.command == "adjudicate-opentitan-evidence-set":
            return _adjudicate_opentitan_evidence_set(arguments)
        if arguments.command == "validate-opentitan-target-contract":
            return _validate_opentitan_target_contract(arguments)
        if arguments.command == "adjudicate-opentitan-run-spec":
            return _adjudicate_opentitan_run_spec(arguments)
        if arguments.command == "validate-opentitan-adjudication":
            return _validate_opentitan_adjudication_output(arguments)
        if arguments.command == "validate-opentitan-adjudication-summary":
            return _validate_opentitan_adjudication_summary_output(arguments)
        if arguments.command == "adjudicate-boundary-benchmark":
            return _adjudicate_boundary_benchmark(arguments)
        if arguments.command == "select-boundary-points":
            return _select_boundary_points(arguments)
        if arguments.command == "validate":
            return _validate(arguments)
    except (
        OSError,
        json.JSONDecodeError,
        SidecarError,
        PhysicalProbeError,
        NativeManifestError,
        EvalEffectError,
        OpenTitanEvidenceError,
        SweepBoundaryError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {arguments.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
