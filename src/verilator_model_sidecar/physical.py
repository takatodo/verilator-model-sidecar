"""Measured C++ ABI layout for generated Verilator model state."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .coverage import CoverageMappingError, coverage_region_contracts


LAYOUT_OBSERVATION_SURFACE = "verilator_cpp_layout_observation"
SUPPORTED_VERILATOR_PREFIX = "Verilator 5.050 "
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")


class PhysicalProbeError(RuntimeError):
    """Raised when generated C++ cannot prove the requested physical layout."""


@dataclass(frozen=True)
class _ProbeSpec:
    name: str
    binding: str
    offset_expression: str
    size_expression: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _model_prefix(adapter: Mapping[str, Any]) -> str:
    source_model = adapter.get("source_model")
    if not isinstance(source_model, Mapping):
        raise PhysicalProbeError("adapter source_model must be an object")
    prefix = source_model.get("prefix")
    if not isinstance(prefix, str) or _IDENTIFIER.fullmatch(prefix) is None:
        raise PhysicalProbeError("adapter source_model.prefix must be a C++ identifier")
    return prefix


def _member_types(syms_header: Path) -> dict[str, str]:
    members: dict[str, str] = {}
    pattern = re.compile(
        r"^\s*(?P<type>[A-Za-z_]\w*)\s+(?P<name>[A-Za-z_]\w*)\s*;\s*$"
    )
    for line in syms_header.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            members[match.group("name")] = match.group("type")
    return members


def _binding_spec(
    name: str,
    binding: str,
    *,
    prefix: str,
    syms_members: Mapping[str, str],
) -> _ProbeSpec:
    root_type = f"{prefix}___024root"
    syms_type = f"{prefix}__Syms"
    root_prefix = root_type + "."
    syms_prefix = syms_type + "."
    if binding.startswith(root_prefix):
        field = binding[len(root_prefix) :]
        if _IDENTIFIER.fullmatch(field) is None:
            raise PhysicalProbeError(f"unsupported root binding {binding!r}")
        return _ProbeSpec(
            name=name,
            binding=binding,
            offset_expression=(
                f"offsetof({syms_type}, TOP) + offsetof({root_type}, {field})"
            ),
            size_expression=f"sizeof((({root_type}*)nullptr)->{field})",
        )
    if not binding.startswith(syms_prefix):
        raise PhysicalProbeError(
            f"binding {binding!r} does not name {root_type} or {syms_type}"
        )
    path = binding[len(syms_prefix) :].split(".")
    if len(path) != 2 or any(_IDENTIFIER.fullmatch(part) is None for part in path):
        raise PhysicalProbeError(f"unsupported symbol-table binding {binding!r}")
    instance, field = path
    member_type = root_type if instance == "TOP" else syms_members.get(instance)
    if member_type is None:
        raise PhysicalProbeError(
            f"symbol-table instance {instance!r} is absent for binding {binding!r}"
        )
    return _ProbeSpec(
        name=name,
        binding=binding,
        offset_expression=(
            f"offsetof({syms_type}, {instance}) + "
            f"offsetof({member_type}, {field})"
        ),
        size_expression=f"sizeof((({member_type}*)nullptr)->{field})",
    )


def _verilator_include_dir(
    explicit: Path | None,
    *,
    verilator: str,
) -> Path:
    if explicit is not None:
        include_dir = explicit.resolve()
    else:
        completed = subprocess.run(
            [verilator, "--getenv", "VERILATOR_ROOT"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise PhysicalProbeError(
                "cannot discover Verilator headers; pass --verilator-include"
            )
        include_dir = Path(completed.stdout.strip()).resolve() / "include"
    if not (include_dir / "verilated.h").is_file():
        raise PhysicalProbeError(
            f"Verilator include directory has no verilated.h: {include_dir}"
        )
    return include_dir


def _compiler_identity(cxx: str) -> str:
    completed = subprocess.run(
        [cxx, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PhysicalProbeError(f"cannot execute C++ compiler: {cxx}")
    lines = completed.stdout.splitlines()
    if not lines:
        raise PhysicalProbeError("C++ compiler emitted no version identity")
    return lines[0].strip()


def probe_physical_layout(
    *,
    obj_dir: Path,
    adapter: Mapping[str, Any],
    producer: str,
    coverage_contract: Mapping[str, Any] | None = None,
    cxx: str = "c++",
    verilator_include: Path | None = None,
    verilator: str = "verilator",
) -> dict[str, Any]:
    """Compile and execute one explicit ``sizeof``/``offsetof`` ABI probe."""

    if not producer.startswith(SUPPORTED_VERILATOR_PREFIX):
        raise PhysicalProbeError(
            f"unsupported Verilator producer {producer!r}; expected 5.050"
        )
    obj_dir = obj_dir.resolve()
    if not obj_dir.is_dir():
        raise PhysicalProbeError(f"Verilator object directory does not exist: {obj_dir}")
    prefix = _model_prefix(adapter)
    syms_header = obj_dir / f"{prefix}__Syms.h"
    root_header = obj_dir / f"{prefix}___024root.h"
    if not syms_header.is_file() or not root_header.is_file():
        raise PhysicalProbeError(
            f"generated layout headers for {prefix} are missing under {obj_dir}"
        )
    signals = adapter.get("signals")
    if not isinstance(signals, Mapping) or not signals:
        raise PhysicalProbeError("adapter signals must be a non-empty object")
    syms_members = _member_types(syms_header)
    specs: list[_ProbeSpec] = []
    for name, contract in sorted(signals.items()):
        if not isinstance(contract, Mapping) or not isinstance(
            contract.get("binding"), str
        ):
            raise PhysicalProbeError(f"adapter signal {name!r} has no string binding")
        specs.append(
            _binding_spec(
                str(name),
                str(contract["binding"]),
                prefix=prefix,
                syms_members=syms_members,
            )
        )
    coverage_regions: list[dict[str, Any]] = []
    coverage_specs: list[_ProbeSpec] = []
    if coverage_contract is not None:
        try:
            coverage_regions = coverage_region_contracts(
                coverage_contract,
                expected_model_prefix=prefix,
            )
        except CoverageMappingError as error:
            raise PhysicalProbeError(str(error)) from error
        for region in coverage_regions:
            coverage_specs.append(
                _binding_spec(
                    str(region["name"]),
                    str(region["binding"]),
                    prefix=prefix,
                    syms_members=syms_members,
                )
            )
    include_dir = _verilator_include_dir(
        verilator_include,
        verilator=verilator,
    )
    syms_type = f"{prefix}__Syms"
    source_lines = [
        "#include <cstddef>",
        "#include <cstdio>",
        f'#include "{prefix}__Syms.h"',
        "int main() {",
        f'  std::printf("__state__\\t%zu\\t%zu\\n", sizeof({syms_type}), '
        f"offsetof({syms_type}, TOP));",
    ]
    for index, spec in enumerate(specs):
        source_lines.append(
            f'  std::printf("binding:{index}\\t%zu\\t%zu\\n", '
            f"static_cast<std::size_t>({spec.offset_expression}), "
            f"static_cast<std::size_t>({spec.size_expression}));"
        )
    for index, spec in enumerate(coverage_specs):
        source_lines.append(
            f'  std::printf("coverage:{index}\\t%zu\\t%zu\\n", '
            f"static_cast<std::size_t>({spec.offset_expression}), "
            f"static_cast<std::size_t>({spec.size_expression}));"
        )
    source_lines.extend(("  return 0;", "}"))
    source = "\n".join(source_lines) + "\n"
    with tempfile.TemporaryDirectory(prefix="verilator-model-layout-") as temporary:
        temporary_dir = Path(temporary)
        source_path = temporary_dir / "layout_probe.cpp"
        executable = temporary_dir / "layout_probe"
        source_path.write_text(source, encoding="utf-8")
        command = [
            cxx,
            "-std=c++20",
            "-Wno-invalid-offsetof",
            f"-I{obj_dir}",
            f"-I{include_dir}",
            f"-I{include_dir / 'vltstd'}",
            source_path.as_posix(),
            "-o",
            executable.as_posix(),
        ]
        compiled = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if compiled.returncode != 0:
            raise PhysicalProbeError(
                "Verilator layout probe compilation failed:\n"
                + (compiled.stderr or compiled.stdout).strip()
            )
        executed = subprocess.run(
            [executable.as_posix()],
            check=False,
            capture_output=True,
            text=True,
        )
        if executed.returncode != 0:
            raise PhysicalProbeError(
                "Verilator layout probe execution failed: "
                + (executed.stderr or executed.stdout).strip()
            )
    rows: dict[str, tuple[int, int]] = {}
    for line in executed.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            raise PhysicalProbeError(f"malformed layout probe row: {line!r}")
        key, offset, size = fields
        if key in rows:
            raise PhysicalProbeError(f"duplicate layout probe key {key!r}")
        try:
            rows[key] = (int(offset), int(size))
        except ValueError as error:
            raise PhysicalProbeError(f"non-integer layout probe row: {line!r}") from error
    expected_keys = {
        "__state__",
        *(f"binding:{i}" for i in range(len(specs))),
        *(f"coverage:{i}" for i in range(len(coverage_specs))),
    }
    if set(rows) != expected_keys:
        raise PhysicalProbeError("layout probe did not return every requested binding")
    storage_size, root_offset = rows.pop("__state__")
    bindings = []
    for index, spec in enumerate(specs):
        state_offset, size_bytes = rows[f"binding:{index}"]
        bindings.append(
            {
                "name": spec.name,
                "binding": spec.binding,
                "state_offset": state_offset,
                "size_bytes": size_bytes,
            }
        )
    measured_coverage = []
    for index, (spec, region) in enumerate(
        zip(coverage_specs, coverage_regions, strict=True)
    ):
        region_offset, region_size = rows[f"coverage:{index}"]
        word_bytes = int(region["word_bits"]) // 8
        if region_size % word_bytes:
            raise PhysicalProbeError(
                f"coverage region {spec.name!r} size is not word aligned"
            )
        measured_coverage.append(
            {
                **region,
                "state_offset": region_offset,
                "size_bytes": region_size,
                "word_count": region_size // word_bytes,
            }
        )
    observation: dict[str, Any] = {
        "surface": LAYOUT_OBSERVATION_SURFACE,
        "schema_version": 1,
        "status": "measured",
        "producer": producer,
        "model_prefix": prefix,
        "measurement": "compiled_cpp_sizeof_offsetof",
        "compiler": _compiler_identity(cxx),
        "cxx_standard": "c++20",
        "probe_source_sha256": _sha256_bytes(source.encode("utf-8")),
        "headers": {
            "syms_header_sha256": _sha256_file(syms_header),
            "root_header_sha256": _sha256_file(root_header),
        },
        "state_image": {
            "bytes": storage_size,
            "root_offset_bytes": root_offset,
        },
        "binding_count": len(bindings),
        "bindings": bindings,
        "non_claims": [
            "not_a_semantic_identity_source",
            "not_a_pointer_free_state_layout",
            "not_a_stable_cross_version_abi",
        ],
    }
    if measured_coverage:
        observation["coverage_region_count"] = len(measured_coverage)
        observation["coverage_regions"] = measured_coverage
    observation["observation_fingerprint"] = _sha256_bytes(
        _canonical_bytes(observation)
    )
    validate_layout_observation(observation)
    return observation


def validate_layout_observation(observation: Mapping[str, Any]) -> None:
    if observation.get("surface") != LAYOUT_OBSERVATION_SURFACE:
        raise PhysicalProbeError("unexpected layout observation surface")
    if observation.get("schema_version") != 1:
        raise PhysicalProbeError("unsupported layout observation schema_version")
    if observation.get("status") != "measured":
        raise PhysicalProbeError("layout observation must have status measured")
    producer = observation.get("producer")
    model_prefix = observation.get("model_prefix")
    if not isinstance(producer, str) or not producer.startswith(
        SUPPORTED_VERILATOR_PREFIX
    ):
        raise PhysicalProbeError("layout observation has unsupported producer")
    if not isinstance(model_prefix, str) or _IDENTIFIER.fullmatch(model_prefix) is None:
        raise PhysicalProbeError("layout observation has invalid model prefix")
    if observation.get("measurement") != "compiled_cpp_sizeof_offsetof":
        raise PhysicalProbeError("layout observation has unexpected measurement kind")
    if not isinstance(observation.get("compiler"), str) or not observation.get(
        "compiler"
    ):
        raise PhysicalProbeError("layout observation has no compiler identity")
    if observation.get("cxx_standard") != "c++20":
        raise PhysicalProbeError("layout observation has unexpected C++ standard")
    digest_pattern = re.compile(r"^[0-9a-f]{64}$")
    if digest_pattern.fullmatch(str(observation.get("probe_source_sha256", ""))) is None:
        raise PhysicalProbeError("layout observation has invalid probe source hash")
    headers = observation.get("headers")
    if not isinstance(headers, Mapping):
        raise PhysicalProbeError("layout observation headers must be an object")
    for header_name in ("syms_header_sha256", "root_header_sha256"):
        if digest_pattern.fullmatch(str(headers.get(header_name, ""))) is None:
            raise PhysicalProbeError(
                f"layout observation has invalid {header_name}"
            )
    state_image = observation.get("state_image")
    if not isinstance(state_image, Mapping):
        raise PhysicalProbeError("layout state_image must be an object")
    storage_size = state_image.get("bytes")
    root_offset = state_image.get("root_offset_bytes")
    if (
        not isinstance(storage_size, int)
        or storage_size <= 0
        or not isinstance(root_offset, int)
        or root_offset < 0
        or root_offset >= storage_size
    ):
        raise PhysicalProbeError("layout state image size/root offset is invalid")
    bindings = observation.get("bindings")
    if not isinstance(bindings, list) or observation.get("binding_count") != len(bindings):
        raise PhysicalProbeError("layout binding_count does not match bindings")
    names: list[str] = []
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise PhysicalProbeError("layout binding must be an object")
        name = binding.get("name")
        cpp_binding = binding.get("binding")
        offset = binding.get("state_offset")
        size = binding.get("size_bytes")
        if not isinstance(name, str) or not isinstance(cpp_binding, str):
            raise PhysicalProbeError("layout binding name/path must be strings")
        if (
            not isinstance(offset, int)
            or offset < 0
            or not isinstance(size, int)
            or size <= 0
            or offset + size > storage_size
        ):
            raise PhysicalProbeError(f"layout binding {name!r} is outside state image")
        names.append(name)
    if len(set(names)) != len(names):
        raise PhysicalProbeError("layout binding names must be unique")
    coverage_regions = observation.get("coverage_regions")
    coverage_region_count = observation.get("coverage_region_count")
    if coverage_regions is None and coverage_region_count is None:
        coverage_regions = []
    elif (
        not isinstance(coverage_regions, list)
        or coverage_region_count != len(coverage_regions)
    ):
        raise PhysicalProbeError(
            "layout coverage_region_count does not match coverage_regions"
        )
    coverage_names: list[str] = []
    for region in coverage_regions:
        if not isinstance(region, Mapping):
            raise PhysicalProbeError("layout coverage region must be an object")
        name = region.get("name")
        binding = region.get("binding")
        kind = region.get("kind")
        word_bits = region.get("word_bits")
        word_count = region.get("word_count")
        offset = region.get("state_offset")
        size = region.get("size_bytes")
        if (
            not isinstance(name, str)
            or not isinstance(binding, str)
            or kind != "toggle_direction_counters"
            or isinstance(word_bits, bool)
            or not isinstance(word_bits, int)
            or word_bits <= 0
            or word_bits % 8
            or isinstance(word_count, bool)
            or not isinstance(word_count, int)
            or word_count <= 0
            or not isinstance(offset, int)
            or offset < 0
            or not isinstance(size, int)
            or size != word_count * (word_bits // 8)
            or offset + size > storage_size
        ):
            raise PhysicalProbeError(
                f"layout coverage region {name!r} is invalid or outside state image"
            )
        coverage_names.append(name)
    if len(set(coverage_names)) != len(coverage_names):
        raise PhysicalProbeError("layout coverage region names must be unique")
    fingerprint = observation.get("observation_fingerprint")
    if not isinstance(fingerprint, str) or digest_pattern.fullmatch(fingerprint) is None:
        raise PhysicalProbeError("layout observation has invalid fingerprint")
    fingerprint_input = dict(observation)
    del fingerprint_input["observation_fingerprint"]
    if fingerprint != _sha256_bytes(_canonical_bytes(fingerprint_input)):
        raise PhysicalProbeError("layout observation fingerprint mismatch")
    serialized = json.dumps(observation, ensure_ascii=False)
    if re.search(r'(?<![A-Za-z0-9_])/(?:home|tmp)/', serialized):
        raise PhysicalProbeError("layout observation contains a local absolute path")


def write_layout_observation(path: Path, observation: Mapping[str, Any]) -> None:
    validate_layout_observation(observation)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(observation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
