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
from .native import (
    NativeManifestError,
    verify_native_adapter,
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
        required=True,
        metavar="NAME=PATH",
        help="named LLVM IR input; repeat for every Contract input",
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
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SidecarError(f"{description} JSON root must be an object")
    return value


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


def _named_paths(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise SidecarError(f"LLVM IR input must use NAME=PATH: {value!r}")
        if name in result:
            raise SidecarError(f"duplicate LLVM IR input name: {name}")
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
        ir_inputs=_named_paths(arguments.ir),
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
        if arguments.command == "validate":
            return _validate(arguments)
    except (
        OSError,
        json.JSONDecodeError,
        SidecarError,
        PhysicalProbeError,
        NativeManifestError,
        EvalEffectError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {arguments.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
