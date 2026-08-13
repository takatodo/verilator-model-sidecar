"""Deterministic semantic projection from Verilator 5.050 JSON output."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .coverage import (
    CoverageMappingError,
    build_toggle_coverage_mapping,
    validate_coverage_mapping,
)
from .effects import EvalEffectError, validate_eval_effects


MANIFEST_SCHEMA_VERSION = 1
MANIFEST_SURFACE = "verilator_model_sidecar_manifest"
SUPPORTED_VERILATOR_PREFIX = "Verilator 5.050 "


class SidecarError(RuntimeError):
    """Raised when an input cannot satisfy the bounded sidecar contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _walk(value: Any) -> Iterator[Mapping[str, Any]]:
    """Walk arbitrary Verilator JSON without depending on Python recursion depth."""

    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            yield current
            pending.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            pending.extend(reversed(current))


def _width_from_dtype(dtype: Mapping[str, Any] | None) -> tuple[int | None, str]:
    if dtype is None:
        return None, "unknown_dtype"
    raw_range = dtype.get("range")
    if isinstance(raw_range, str):
        match = re.fullmatch(r"\s*(-?\d+)\s*:\s*(-?\d+)\s*", raw_range)
        if match:
            high, low = (int(group) for group in match.groups())
            return abs(high - low) + 1, "resolved_range"
        return None, "non_constant_range"
    keyword = str(dtype.get("keyword", dtype.get("name", ""))).lower()
    scalar_widths = {
        "bit": 1,
        "logic": 1,
        "reg": 1,
        "byte": 8,
        "shortint": 16,
        "int": 32,
        "integer": 32,
        "longint": 64,
        "time": 64,
    }
    if keyword in scalar_widths:
        return scalar_widths[keyword], "resolved_scalar"
    return None, "unsupported_dtype"


def _normal_path(raw_path: str, source_root: Path) -> str:
    if raw_path.startswith("<") and raw_path.endswith(">"):
        return raw_path
    candidate = Path(raw_path)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(source_root)
        except ValueError as error:
            raise SidecarError(
                f"source path is outside --source-root: {raw_path}"
            ) from error
    normalized = candidate.as_posix()
    if normalized == ".." or normalized.startswith("../"):
        raise SidecarError(f"source path escapes --source-root: {raw_path}")
    return normalized


def _source_location(
    raw_location: Any,
    files: Mapping[str, Any],
    source_root: Path,
) -> dict[str, Any]:
    if not isinstance(raw_location, str):
        return {"status": "unknown"}
    match = re.fullmatch(r"([^,]+),(\d+):(\d+),(\d+):(\d+)", raw_location)
    if not match:
        return {"status": "unknown", "raw": raw_location}
    file_key, line, column, end_line, end_column = match.groups()
    file_record = files.get(file_key, {})
    filename = str(file_record.get("filename", file_key))
    return {
        "status": "resolved",
        "path": _normal_path(filename, source_root),
        "line": int(line),
        "column": int(column),
        "end_line": int(end_line),
        "end_column": int(end_column),
    }


def _semantic_id(identity: Mapping[str, Any]) -> str:
    return "sem:v1:" + _sha256_bytes(_canonical_bytes(identity))


def _process_id(identity: Mapping[str, Any]) -> str:
    return "process:v1:" + _sha256_bytes(_canonical_bytes(identity))


def _module_id(identity: Mapping[str, Any]) -> str:
    return "module:v1:" + _sha256_bytes(_canonical_bytes(identity))


def _instance_id(identity: Mapping[str, Any]) -> str:
    return "instance:v1:" + _sha256_bytes(_canonical_bytes(identity))


def _nonblocking_write_targets(module: Mapping[str, Any]) -> set[str]:
    targets: set[str] = set()
    for node in _walk(module):
        if node.get("type") != "ASSIGNDLY":
            continue
        for lhs in node.get("lhsp", []):
            if not isinstance(lhs, Mapping) or lhs.get("type") != "VARREF":
                continue
            target = lhs.get("varp")
            if isinstance(target, str):
                targets.add(target)
    return targets


def _lifecycle(node: Mapping[str, Any], nonblocking_targets: set[str]) -> str:
    if node.get("isParam") or node.get("isConst"):
        return "immutable"
    direction = str(node.get("direction", "NONE"))
    if direction == "INPUT":
        return "external_input"
    if direction in {"OUTPUT", "INOUT"}:
        return "observable"
    address = node.get("addr")
    if isinstance(address, str) and address in nonblocking_targets:
        return "persistent_mutable"
    return "unknown"


def _top_module(tree: Mapping[str, Any], top: str) -> Mapping[str, Any]:
    matches = [
        module
        for module in tree.get("modulesp", [])
        if isinstance(module, Mapping) and module.get("name") == top
    ]
    if len(matches) != 1:
        raise SidecarError(
            f"expected exactly one top module named {top!r}, found {len(matches)}"
        )
    return matches[0]


def _module_definition_identity(
    module: Mapping[str, Any],
    *,
    files: Mapping[str, Any],
    source_root: Path,
) -> dict[str, Any]:
    return {
        "schema": "semantic-module-definition-v1",
        "generated_name": str(module.get("name", "")),
        "original_name": str(module.get("origName", module.get("name", ""))),
        "source": _source_location(module.get("loc"), files, source_root),
    }


def _hierarchical_cells(
    statements: Any,
) -> list[tuple[tuple[str, ...], Mapping[str, Any]]]:
    """Return cells with named generate/block scope segments.

    Verilator emits generated cells below ``GENBLOCK``/named ``BEGIN`` nodes.
    Cell names alone are therefore not unique (for example ``gen[0].fifo`` and
    ``gen[1].fifo``).  This walk retains those semantic scope segments and is
    iterative because large generated expressions can exceed Python's recursion
    limit even though the instance hierarchy itself is shallow.
    """

    result: list[tuple[tuple[str, ...], Mapping[str, Any]]] = []
    pending: list[tuple[Any, tuple[str, ...]]] = [(statements, ())]
    while pending:
        current, scope = pending.pop()
        if isinstance(current, list):
            pending.extend((child, scope) for child in reversed(current))
            continue
        if not isinstance(current, Mapping):
            continue
        node_type = current.get("type")
        if node_type == "CELL":
            cell_name = str(
                current.get("verilogName", current.get("name", ""))
            )
            result.append((scope + (cell_name,), current))
            continue
        if (
            node_type in {"GENBLOCK", "BEGIN"}
            and current.get("name")
            and not current.get("unnamed")
        ):
            scope = scope + (str(current["name"]),)
        pending.extend(
            (child, scope) for child in reversed(list(current.values()))
        )
    return result


def _extract_hierarchy(
    tree: Mapping[str, Any],
    meta: Mapping[str, Any],
    *,
    top: str,
    source_root: Path,
) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]], dict[str, str]]:
    files = meta.get("files", {})
    if not isinstance(files, Mapping):
        raise SidecarError("tree meta JSON has no files mapping")
    modules = tree.get("modulesp", [])
    if not isinstance(modules, list):
        raise SidecarError("Verilator JSON has no modules array")
    module_by_address = {
        module.get("addr"): module
        for module in modules
        if isinstance(module, Mapping) and isinstance(module.get("addr"), str)
    }
    top_module = _top_module(tree, top)
    definition_ids: dict[str, str] = {}
    cell_cache: dict[str, list[tuple[tuple[str, ...], Mapping[str, Any]]]] = {}

    def definition_id(module: Mapping[str, Any]) -> str:
        address = str(module.get("addr", ""))
        if address not in definition_ids:
            definition_ids[address] = _module_id(
                _module_definition_identity(
                    module,
                    files=files,
                    source_root=source_root,
                )
            )
        return definition_ids[address]

    def module_cells(
        module: Mapping[str, Any],
    ) -> list[tuple[tuple[str, ...], Mapping[str, Any]]]:
        address = str(module.get("addr", ""))
        if address not in cell_cache:
            cell_cache[address] = _hierarchical_cells(module.get("stmtsp", []))
        return cell_cache[address]

    instance_modules: dict[str, Mapping[str, Any]] = {}
    instance_ids: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    pending: list[
        tuple[
            str,
            Mapping[str, Any],
            str | None,
            Mapping[str, Any] | None,
            tuple[str, ...],
        ]
    ] = [(top, top_module, None, None, ())]
    while pending:
        path, module, parent_path, cell, ancestors = pending.pop()
        if path in instance_modules:
            unresolved.append(
                {
                    "canonical_path": path,
                    "status": "ambiguous_duplicate_path",
                    "source": _source_location(
                        cell.get("loc") if cell is not None else module.get("loc"),
                        files,
                        source_root,
                    ),
                }
            )
            continue
        module_definition_id = definition_id(module)
        identity = {
            "schema": "semantic-instance-v1",
            "canonical_path": path,
            "module_definition_id": module_definition_id,
        }
        current_instance_id = _instance_id(identity)
        instance_modules[path] = module
        instance_ids[path] = current_instance_id
        records.append(
            {
                "instance_id": current_instance_id,
                "canonical_path": path,
                "parent_instance_id": (
                    instance_ids.get(parent_path) if parent_path is not None else None
                ),
                "module_definition_id": module_definition_id,
                "module": str(module.get("name", "")),
                "original_module": str(
                    module.get("origName", module.get("name", ""))
                ),
                "source": _source_location(
                    cell.get("loc") if cell is not None else module.get("loc"),
                    files,
                    source_root,
                ),
            }
        )
        module_address = str(module.get("addr", ""))
        ancestry = ancestors + (module_address,)
        for segments, child_cell in module_cells(module):
            child_path = path + "." + ".".join(segments)
            child_address = child_cell.get("modp")
            child_module = (
                module_by_address.get(child_address)
                if isinstance(child_address, str)
                else None
            )
            if child_module is None:
                unresolved.append(
                    {
                        "canonical_path": child_path,
                        "status": "unresolved_module_definition",
                        "source": _source_location(
                            child_cell.get("loc"), files, source_root
                        ),
                    }
                )
                continue
            if str(child_address) in ancestry:
                unresolved.append(
                    {
                        "canonical_path": child_path,
                        "status": "recursive_module_cycle",
                        "source": _source_location(
                            child_cell.get("loc"), files, source_root
                        ),
                    }
                )
                continue
            pending.append(
                (child_path, child_module, path, child_cell, ancestry)
            )

    records.sort(key=lambda record: record["canonical_path"])
    unresolved.sort(
        key=lambda record: (record["canonical_path"], record["status"])
    )
    return (
        {
            "status": "resolved" if not unresolved else "partial",
            "top_instance": top,
            "instance_count": len(records),
            "module_definition_count": len(definition_ids),
            "unresolved_count": len(unresolved),
            "instances": records,
            "unresolved": unresolved,
        },
        instance_modules,
        instance_ids,
    )


def _dtype_index(tree: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for node in _walk(tree):
        node_type = str(node.get("type", ""))
        address = node.get("addr")
        if node_type.endswith("DTYPE") and isinstance(address, str):
            result[address] = node
    return result


def extract_semantic_projection(
    tree: Mapping[str, Any],
    meta: Mapping[str, Any],
    *,
    top: str,
    source_root: Path,
) -> dict[str, Any]:
    """Extract module semantics plus the elaborated instance hierarchy."""

    source_root = source_root.resolve()
    files = meta.get("files", {})
    if not isinstance(files, Mapping):
        raise SidecarError("tree meta JSON has no files mapping")
    dtype_by_address = _dtype_index(tree)

    module = _top_module(tree, top)
    nonblocking_targets = _nonblocking_write_targets(module)
    entities: list[dict[str, Any]] = []
    entity_by_address: dict[str, str] = {}
    instrumentation_excluded_count = 0
    statements = module.get("stmtsp", [])
    for node in statements:
        if not isinstance(node, Mapping) or node.get("type") != "VAR":
            continue
        name = str(node.get("verilogName", node.get("name", "")))
        if name.startswith("__Vtogcov__"):
            instrumentation_excluded_count += 1
            continue
        location = _source_location(node.get("loc"), files, source_root)
        dtype_address = node.get("dtypep")
        dtype = (
            dtype_by_address.get(dtype_address)
            if isinstance(dtype_address, str)
            else None
        )
        width_bits, width_status = _width_from_dtype(dtype)
        identity = {
            "schema": "semantic-identity-v1",
            "scope_kind": "module_definition",
            "module": top,
            "name": name,
            "kind": str(node.get("varType", "VAR")).lower(),
            "source": location,
            "width_bits": width_bits,
        }
        semantic_id = _semantic_id(identity)
        address = node.get("addr")
        if isinstance(address, str):
            entity_by_address[address] = semantic_id
        entities.append(
            {
                "semantic_id": semantic_id,
                "canonical_name": f"{top}.{name}",
                "scope_kind": "module_definition",
                "module": top,
                "name": name,
                "original_name": str(node.get("origName", name)),
                "kind": str(node.get("varType", "VAR")).lower(),
                "direction": str(node.get("direction", "NONE")).lower(),
                "dtype": str(node.get("dtypeName", "")),
                "width_bits": width_bits,
                "width_status": width_status,
                "constant": bool(node.get("isConst", False)),
                "lifecycle": _lifecycle(node, nonblocking_targets),
                "source": location,
            }
        )

    processes: list[dict[str, Any]] = []
    for node in statements:
        if not isinstance(node, Mapping) or node.get("type") != "ALWAYS":
            continue
        location = _source_location(node.get("loc"), files, source_root)
        process_identity = {
            "schema": "semantic-process-v1",
            "module": top,
            "keyword": str(node.get("keyword", "")),
            "source": location,
        }
        reads: set[str] = set()
        writes: set[str] = set()
        for child in _walk(node):
            if child.get("type") != "VARREF":
                continue
            target = child.get("varp")
            semantic_id = entity_by_address.get(target) if isinstance(target, str) else None
            if semantic_id is None:
                continue
            access = str(child.get("access", ""))
            if access in {"RD", "RW"}:
                reads.add(semantic_id)
            if access in {"WR", "RW"}:
                writes.add(semantic_id)
        processes.append(
            {
                "process_id": _process_id(process_identity),
                "module": top,
                "keyword": str(node.get("keyword", "")),
                "source": location,
                "reads": sorted(reads),
                "writes": sorted(writes),
            }
        )

    hierarchy, _, _ = _extract_hierarchy(
        tree,
        meta,
        top=top,
        source_root=source_root,
    )
    entities.sort(key=lambda entity: entity["canonical_name"])
    processes.sort(key=lambda process: process["process_id"])
    return {
        "status": "elaborated_hierarchy_projection",
        "top_module": top,
        "entity_count": len(entities),
        "instrumentation_excluded_count": instrumentation_excluded_count,
        "process_count": len(processes),
        "entities": entities,
        "processes": processes,
        "hierarchy": hierarchy,
        "limitations": [
            "entities_are_top_module_definitions_unless_explicitly_resolved",
            "lifecycle_is_a_conservative_ast_classification",
            "physical_storage_is_not_inferred_from_the_json_tree",
        ],
    }


def _decode_verilator_binding(binding: str, model_prefix: str) -> tuple[str, str]:
    prefixes = (
        (f"{model_prefix}___024root.", "root_object"),
        (f"{model_prefix}__Syms.TOP__", "syms_top"),
    )
    for prefix, binding_kind in prefixes:
        if binding.startswith(prefix):
            encoded = binding[len(prefix) :]
            canonical = encoded.replace("__DOT__", ".")
            canonical = re.sub(r"__BRA__(\d+)__KET__", r"[\1]", canonical)
            return canonical, binding_kind
    raise SidecarError(
        f"unsupported Verilator signal binding for model prefix {model_prefix!r}: "
        f"{binding}"
    )


def _resolve_instance_entity(
    canonical_name: str,
    *,
    instance_modules: Mapping[str, Mapping[str, Any]],
    instance_ids: Mapping[str, str],
    dtype_by_address: Mapping[str, Mapping[str, Any]],
    files: Mapping[str, Any],
    source_root: Path,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for instance_path, module in instance_modules.items():
        prefix = instance_path + "."
        if not canonical_name.startswith(prefix):
            continue
        variable_name = canonical_name[len(prefix) :]
        for node in module.get("stmtsp", []):
            if not isinstance(node, Mapping) or node.get("type") != "VAR":
                continue
            name = str(node.get("verilogName", node.get("name", "")))
            if name != variable_name or name.startswith("__Vtogcov__"):
                continue
            dtype_address = node.get("dtypep")
            dtype = (
                dtype_by_address.get(dtype_address)
                if isinstance(dtype_address, str)
                else None
            )
            width_bits, width_status = _width_from_dtype(dtype)
            source = _source_location(node.get("loc"), files, source_root)
            module_identity = _module_definition_identity(
                module,
                files=files,
                source_root=source_root,
            )
            identity = {
                "schema": "semantic-identity-v1",
                "scope_kind": "elaborated_instance",
                "canonical_name": canonical_name,
                "module_definition_id": _module_id(module_identity),
                "name": name,
                "kind": str(node.get("varType", "VAR")).lower(),
                "source": source,
                "width_bits": width_bits,
            }
            matches.append(
                {
                    "semantic_id": _semantic_id(identity),
                    "canonical_name": canonical_name,
                    "scope_kind": "elaborated_instance",
                    "instance_id": instance_ids[instance_path],
                    "instance_path": instance_path,
                    "module_definition_id": _module_id(module_identity),
                    "module": str(module.get("name", "")),
                    "original_module": str(
                        module.get("origName", module.get("name", ""))
                    ),
                    "name": name,
                    "original_name": str(node.get("origName", name)),
                    "kind": str(node.get("varType", "VAR")).lower(),
                    "rtl_direction": str(node.get("direction", "NONE")).lower(),
                    "dtype": str(node.get("dtypeName", "")),
                    "width_bits": width_bits,
                    "width_status": width_status,
                    "constant": bool(node.get("isConst", False)),
                    "source": source,
                }
            )
    matches.sort(key=lambda match: match["semantic_id"])
    return matches


def verify_adapter_semantics(
    tree: Mapping[str, Any],
    meta: Mapping[str, Any],
    adapter: Mapping[str, Any],
    *,
    top: str,
    source_root: Path,
) -> dict[str, Any]:
    """Resolve adapter signal names against JSON semantics, never C++ offsets."""

    source_root = source_root.resolve()
    source_model = adapter.get("source_model", {})
    if not isinstance(source_model, Mapping):
        raise SidecarError("adapter source_model must be an object")
    model_prefix = source_model.get("prefix")
    if not isinstance(model_prefix, str) or not model_prefix:
        raise SidecarError("adapter source_model.prefix must be a non-empty string")
    requested_signals = adapter.get("signals")
    if not isinstance(requested_signals, Mapping) or not requested_signals:
        raise SidecarError("adapter signals must be a non-empty object")
    files = meta.get("files", {})
    if not isinstance(files, Mapping):
        raise SidecarError("tree meta JSON has no files mapping")
    hierarchy, instance_modules, instance_ids = _extract_hierarchy(
        tree,
        meta,
        top=top,
        source_root=source_root,
    )
    dtype_by_address = _dtype_index(tree)
    signal_results: list[dict[str, Any]] = []
    matched_count = 0
    for signal_name, raw_contract in sorted(requested_signals.items()):
        if not isinstance(raw_contract, Mapping):
            raise SidecarError(f"adapter signal {signal_name!r} must be an object")
        binding = raw_contract.get("binding")
        if not isinstance(binding, str):
            raise SidecarError(
                f"adapter signal {signal_name!r} has no string binding"
            )
        canonical_name, binding_kind = _decode_verilator_binding(
            binding, model_prefix
        )
        if canonical_name != top and not canonical_name.startswith(top + "."):
            canonical_name = top + "." + canonical_name
        matches = _resolve_instance_entity(
            canonical_name,
            instance_modules=instance_modules,
            instance_ids=instance_ids,
            dtype_by_address=dtype_by_address,
            files=files,
            source_root=source_root,
        )
        expected_width = raw_contract.get("width_bits")
        width_match = (
            len(matches) == 1
            and isinstance(expected_width, int)
            and matches[0]["width_bits"] == expected_width
        )
        status = "matched" if len(matches) == 1 and width_match else "mismatch"
        if status == "matched":
            matched_count += 1
        signal_results.append(
            {
                "name": str(signal_name),
                "status": status,
                "binding": binding,
                "binding_kind": binding_kind,
                "canonical_name": canonical_name,
                "match_count": len(matches),
                "width_match": width_match,
                "contract": {
                    "width_bits": expected_width,
                    "direction": raw_contract.get("direction"),
                    "role": raw_contract.get("role"),
                },
                "semantic_entity": matches[0] if len(matches) == 1 else None,
                "direction_authority": "adapter_contract_not_derived_from_ast",
            }
        )
    signal_count = len(signal_results)
    return {
        "surface": "verilator_adapter_semantic_verification",
        "schema_version": 1,
        "status": "matched" if matched_count == signal_count else "mismatch",
        "target": adapter.get("target"),
        "adapter_id": adapter.get("adapter_id"),
        "model_prefix": model_prefix,
        "top_module": top,
        "signal_count": signal_count,
        "matched_count": matched_count,
        "unmatched_count": signal_count - matched_count,
        "hierarchy_status": hierarchy["status"],
        "hierarchy_instance_count": hierarchy["instance_count"],
        "signals": signal_results,
        "non_claims": [
            "not_a_physical_offset_verification",
            "drive_observe_direction_is_adapter_owned",
            "not_a_coverage_mapping_verification",
        ],
    }


def _physical_header_fingerprints(
    obj_dir: Path,
    model_prefix: str,
) -> dict[str, str]:
    syms_header = obj_dir / f"{model_prefix}__Syms.h"
    root_header = obj_dir / f"{model_prefix}___024root.h"
    if not syms_header.is_file() or not root_header.is_file():
        raise SidecarError(
            f"generated layout headers for {model_prefix} are missing under {obj_dir}"
        )
    return {
        "syms_header_sha256": _sha256_file(syms_header),
        "root_header_sha256": _sha256_file(root_header),
    }


def _physical_oracle(
    oracle: Mapping[str, Any],
    *,
    signal_names: set[str],
    model_prefix: str,
    target: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    if oracle.get("surface") != "verilator_physical_binding_oracle":
        raise SidecarError("unexpected physical binding oracle surface")
    if oracle.get("schema_version") != 1:
        raise SidecarError("unsupported physical binding oracle schema_version")
    if oracle.get("model_prefix") != model_prefix:
        raise SidecarError("physical oracle model prefix does not match adapter")
    if oracle.get("target") is not None and oracle.get("target") != target:
        raise SidecarError("physical oracle target does not match adapter")
    headers = oracle.get("headers")
    state_image = oracle.get("state_image")
    bindings = oracle.get("bindings")
    if not isinstance(headers, Mapping):
        raise SidecarError("physical oracle headers must be an object")
    if not isinstance(state_image, Mapping):
        raise SidecarError("physical oracle state_image must be an object")
    if not isinstance(bindings, Mapping):
        raise SidecarError("physical oracle bindings must be an object")
    if set(bindings) != signal_names:
        raise SidecarError("physical oracle bindings do not match adapter signals")
    for key in ("syms_header_sha256", "root_header_sha256"):
        if not isinstance(headers.get(key), str):
            raise SidecarError(f"physical oracle header {key} must be a string")
    for key in ("bytes", "root_offset_bytes"):
        if not isinstance(state_image.get(key), int):
            raise SidecarError(f"physical oracle state_image.{key} must be an integer")
    for name, row in bindings.items():
        if not isinstance(row, Mapping) or not isinstance(
            row.get("state_offset"), int
        ) or not isinstance(row.get("size_bytes"), int):
            raise SidecarError(f"physical oracle binding {name!r} is malformed")
    return headers, state_image, bindings


def resolve_physical_bindings(
    *,
    adapter_verification: Mapping[str, Any],
    layout_observation: Mapping[str, Any],
    actual_headers: Mapping[str, str],
    producer: str,
    oracle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Join measured C++ offsets to already-resolved semantic identities."""

    from .physical import PhysicalProbeError, validate_layout_observation

    try:
        validate_layout_observation(layout_observation)
    except PhysicalProbeError as error:
        raise SidecarError(str(error)) from error
    model_prefix = adapter_verification.get("model_prefix")
    if not isinstance(model_prefix, str):
        raise SidecarError("adapter verification has no model prefix")
    semantic_signals = adapter_verification.get("signals")
    if not isinstance(semantic_signals, list) or not semantic_signals:
        raise SidecarError("adapter verification has no semantic signals")
    signal_names = {
        str(signal.get("name"))
        for signal in semantic_signals
        if isinstance(signal, Mapping)
    }
    if len(signal_names) != len(semantic_signals):
        raise SidecarError("adapter verification signal names must be unique")
    observed_bindings = layout_observation.get("bindings")
    if not isinstance(observed_bindings, list):
        raise SidecarError("layout observation bindings must be an array")
    observed_by_name = {
        str(binding.get("name")): binding
        for binding in observed_bindings
        if isinstance(binding, Mapping)
    }
    issues: list[str] = []
    if set(observed_by_name) != signal_names:
        issues.append("layout_binding_names_do_not_match_semantic_signals")
    if layout_observation.get("producer") != producer:
        issues.append("layout_producer_mismatch")
    if layout_observation.get("model_prefix") != model_prefix:
        issues.append("layout_model_prefix_mismatch")
    observed_headers = layout_observation.get("headers")
    if not isinstance(observed_headers, Mapping):
        raise SidecarError("layout observation headers must be an object")
    for header_name, actual_hash in actual_headers.items():
        if observed_headers.get(header_name) != actual_hash:
            issues.append(f"layout_{header_name}_does_not_match_obj_dir")
    observed_state = layout_observation.get("state_image")
    if not isinstance(observed_state, Mapping):
        raise SidecarError("layout observation state_image must be an object")

    oracle_headers: Mapping[str, Any] | None = None
    oracle_state: Mapping[str, Any] | None = None
    oracle_bindings: Mapping[str, Any] | None = None
    if oracle is not None:
        oracle_headers, oracle_state, oracle_bindings = _physical_oracle(
            oracle,
            signal_names=signal_names,
            model_prefix=model_prefix,
            target=adapter_verification.get("target"),
        )
        for header_name, expected_hash in oracle_headers.items():
            if observed_headers.get(header_name) != expected_hash:
                issues.append(f"oracle_{header_name}_mismatch")
        if observed_state.get("bytes") != oracle_state.get("bytes"):
            issues.append("oracle_state_image_bytes_mismatch")
        if observed_state.get("root_offset_bytes") != oracle_state.get(
            "root_offset_bytes"
        ):
            issues.append("oracle_root_offset_mismatch")

    binding_records: list[dict[str, Any]] = []
    for semantic_signal in sorted(
        semantic_signals,
        key=lambda signal: str(signal.get("name")) if isinstance(signal, Mapping) else "",
    ):
        if not isinstance(semantic_signal, Mapping):
            raise SidecarError("adapter semantic signal must be an object")
        name = str(semantic_signal.get("name"))
        semantic_entity = semantic_signal.get("semantic_entity")
        row_issues: list[str] = []
        if semantic_signal.get("status") != "matched" or not isinstance(
            semantic_entity, Mapping
        ):
            row_issues.append("semantic_signal_not_uniquely_resolved")
        observed = observed_by_name.get(name)
        if not isinstance(observed, Mapping):
            row_issues.append("physical_binding_missing")
            observed = {}
        if observed.get("binding") != semantic_signal.get("binding"):
            row_issues.append("cpp_binding_mismatch")
        width_bits = (
            semantic_entity.get("width_bits")
            if isinstance(semantic_entity, Mapping)
            else None
        )
        size_bytes = observed.get("size_bytes")
        if (
            not isinstance(width_bits, int)
            or not isinstance(size_bytes, int)
            or size_bytes * 8 < width_bits
        ):
            row_issues.append("physical_storage_narrower_than_semantic_width")
        expected: Mapping[str, Any] | None = None
        if oracle_bindings is not None:
            raw_expected = oracle_bindings.get(name)
            if not isinstance(raw_expected, Mapping):
                row_issues.append("oracle_binding_missing")
            else:
                expected = raw_expected
                if observed.get("state_offset") != expected.get("state_offset"):
                    row_issues.append("oracle_state_offset_mismatch")
                if observed.get("size_bytes") != expected.get("size_bytes"):
                    row_issues.append("oracle_size_bytes_mismatch")
        row_status = "mismatch" if row_issues else (
            "verified" if oracle is not None else "resolved"
        )
        binding_records.append(
            {
                "name": name,
                "status": row_status,
                "semantic_id": (
                    semantic_entity.get("semantic_id")
                    if isinstance(semantic_entity, Mapping)
                    else None
                ),
                "canonical_name": semantic_signal.get("canonical_name"),
                "cpp_binding": observed.get("binding"),
                "state_offset": observed.get("state_offset"),
                "size_bytes": observed.get("size_bytes"),
                "width_bits": width_bits,
                "expected": (
                    {
                        "state_offset": expected.get("state_offset"),
                        "size_bytes": expected.get("size_bytes"),
                    }
                    if expected is not None
                    else None
                ),
                "issues": row_issues,
            }
        )
    mismatch_count = sum(
        binding["status"] == "mismatch" for binding in binding_records
    )
    if issues or mismatch_count:
        status = "mismatch"
    elif oracle is not None:
        status = "verified"
    else:
        status = "resolved"
    state_status = "mismatch" if any(
        issue.startswith("oracle_state_image")
        or issue.startswith("oracle_root_offset")
        or "header" in issue
        or issue in {"layout_producer_mismatch", "layout_model_prefix_mismatch"}
        for issue in issues
    ) else ("verified" if oracle is not None else "resolved")
    return {
        "status": status,
        "authority": "measured_generated_cpp_abi",
        "model_prefix": model_prefix,
        "measurement": layout_observation.get("measurement"),
        "observation_fingerprint": layout_observation.get(
            "observation_fingerprint"
        ),
        "compiler": layout_observation.get("compiler"),
        "cxx_standard": layout_observation.get("cxx_standard"),
        "headers": dict(observed_headers),
        "state_image": {
            "status": state_status,
            "bytes": observed_state.get("bytes"),
            "root_offset_bytes": observed_state.get("root_offset_bytes"),
            "expected": (
                {
                    "bytes": oracle_state.get("bytes"),
                    "root_offset_bytes": oracle_state.get("root_offset_bytes"),
                }
                if oracle_state is not None
                else None
            ),
        },
        "binding_count": len(binding_records),
        "verified_count": sum(
            binding["status"] == "verified" for binding in binding_records
        ),
        "resolved_count": sum(
            binding["status"] == "resolved" for binding in binding_records
        ),
        "mismatch_count": mismatch_count,
        "bindings": binding_records,
        "issues": sorted(set(issues)),
        "non_claims": [
            "not_a_pointer_free_semantic_state",
            "not_a_cross_version_layout_guarantee",
            "not_a_checkpoint_pack_unpack_proof",
        ],
    }


def _normalized_meta(meta: Mapping[str, Any], source_root: Path) -> dict[str, Any]:
    files = meta.get("files", {})
    if not isinstance(files, Mapping):
        raise SidecarError("tree meta JSON has no files mapping")
    normalized_files: dict[str, dict[str, str]] = {}
    for key, record in files.items():
        if not isinstance(record, Mapping):
            continue
        filename = _normal_path(str(record.get("filename", key)), source_root)
        normalized_files[str(key)] = {
            "filename": filename,
            "language": str(record.get("language", "")),
        }
    return {"files": normalized_files}


def _generated_cpp_fingerprint(obj_dir: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in sorted(obj_dir.rglob("*")):
        if not path.is_file() or path.suffix not in {".cpp", ".h"}:
            continue
        records.append(
            {
                "path": path.relative_to(obj_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if not records:
        raise SidecarError(f"Verilator generated no C++ or headers under {obj_dir}")
    return {
        "status": "captured_not_analyzed",
        "file_count": len(records),
        "aggregate_sha256": _sha256_bytes(_canonical_bytes(records)),
    }


def _run(command: Sequence[str], *, cwd: Path) -> None:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout).strip()
        raise SidecarError(
            f"command failed with exit code {completed.returncode}: "
            f"{command[0]}\n{diagnostic}"
        )


def _verilator_version(executable: str) -> str:
    completed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SidecarError(f"cannot execute Verilator: {executable}")
    version = completed.stdout.strip()
    _validate_producer(version)
    return version


def _validate_producer(version: str) -> None:
    if not version.startswith(SUPPORTED_VERILATOR_PREFIX):
        raise SidecarError(
            f"unsupported Verilator producer {version!r}; expected 5.050"
        )


def _source_records(source_root: Path, sources: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source in sources:
        absolute = source if source.is_absolute() else source_root / source
        absolute = absolute.resolve()
        try:
            relative = absolute.relative_to(source_root)
        except ValueError as error:
            raise SidecarError(f"source is outside --source-root: {source}") from error
        if not absolute.is_file():
            raise SidecarError(f"source does not exist: {source}")
        records.append(
            {
                "path": relative.as_posix(),
                "bytes": absolute.stat().st_size,
                "sha256": _sha256_file(absolute),
            }
        )
    if not records:
        raise SidecarError("at least one --source is required")
    records.sort(key=lambda record: record["path"])
    return records


def _read_json_object(path: Path, description: str) -> Mapping[str, Any]:
    if not path.is_file():
        raise SidecarError(f"{description} does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise SidecarError(f"{description} root must be an object")
    return value


def _manifest_from_artifacts(
    *,
    source_root: Path,
    top: str,
    tree_path: Path,
    meta_path: Path,
    obj_dir: Path,
    producer: str,
    artifact_mode: str,
    source_records: Sequence[Mapping[str, Any]],
    invocation: Mapping[str, Any] | None = None,
    adapter: Mapping[str, Any] | None = None,
    layout_observation: Mapping[str, Any] | None = None,
    physical_oracle: Mapping[str, Any] | None = None,
    coverage_contract: Mapping[str, Any] | None = None,
    coverage_oracle: Mapping[str, Any] | None = None,
    eval_effect_observation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_producer(producer)
    tree = _read_json_object(tree_path, "Verilator JSON tree")
    meta = _read_json_object(meta_path, "Verilator JSON metadata")
    if not obj_dir.is_dir():
        raise SidecarError(f"Verilator object directory does not exist: {obj_dir}")
    normalized_meta = _normalized_meta(meta, source_root)
    generated_cpp = _generated_cpp_fingerprint(obj_dir)
    tree_sha256 = _sha256_file(tree_path)
    semantic_meta_sha256 = _sha256_bytes(_canonical_bytes(normalized_meta))
    provenance_identity = {
        "producer": producer,
        "top_module": top,
        "tree_sha256": tree_sha256,
        "semantic_meta_sha256": semantic_meta_sha256,
        "generated_cpp_sha256": generated_cpp["aggregate_sha256"],
    }
    adapter_contract_sha256 = None
    if adapter is not None:
        adapter_contract_sha256 = _sha256_bytes(_canonical_bytes(adapter))
        provenance_identity["adapter_contract_sha256"] = adapter_contract_sha256
    if layout_observation is not None:
        provenance_identity["layout_observation_fingerprint"] = (
            layout_observation.get("observation_fingerprint")
        )
    physical_oracle_sha256 = None
    if physical_oracle is not None:
        physical_oracle_sha256 = _sha256_bytes(_canonical_bytes(physical_oracle))
        provenance_identity["physical_oracle_sha256"] = physical_oracle_sha256
    coverage_contract_sha256 = None
    if coverage_contract is not None:
        coverage_contract_sha256 = _sha256_bytes(_canonical_bytes(coverage_contract))
        provenance_identity["coverage_contract_sha256"] = coverage_contract_sha256
    coverage_oracle_sha256 = None
    if coverage_oracle is not None:
        coverage_oracle_sha256 = _sha256_bytes(_canonical_bytes(coverage_oracle))
        provenance_identity["coverage_oracle_sha256"] = coverage_oracle_sha256
    eval_effect_observation_fingerprint = None
    eval_effect_oracle_sha256 = None
    if eval_effect_observation is not None:
        try:
            validate_eval_effects(eval_effect_observation)
        except EvalEffectError as error:
            raise SidecarError(str(error)) from error
        if eval_effect_observation.get("producer") != producer:
            raise SidecarError("eval-effect observation producer does not match model")
        eval_effect_observation_fingerprint = eval_effect_observation.get(
            "observation_fingerprint"
        )
        provenance_identity["eval_effect_observation_fingerprint"] = (
            eval_effect_observation_fingerprint
        )
        if "oracle_sha256" in eval_effect_observation:
            eval_effect_oracle_sha256 = eval_effect_observation["oracle_sha256"]
            provenance_identity["eval_effect_oracle_sha256"] = (
                eval_effect_oracle_sha256
            )
    semantic_projection = extract_semantic_projection(
        tree,
        meta,
        top=top,
        source_root=source_root,
    )
    adapter_verification = (
        verify_adapter_semantics(
            tree,
            meta,
            adapter,
            top=top,
            source_root=source_root,
        )
        if adapter is not None
        else None
    )
    if layout_observation is not None and adapter_verification is None:
        raise SidecarError("layout observation requires an adapter semantic contract")
    if physical_oracle is not None and layout_observation is None:
        raise SidecarError("physical oracle requires a layout observation")
    if coverage_contract is not None and layout_observation is None:
        raise SidecarError("coverage contract requires a layout observation")
    if coverage_contract is not None and adapter_verification is None:
        raise SidecarError("coverage contract requires an adapter semantic contract")
    if coverage_oracle is not None and coverage_contract is None:
        raise SidecarError("coverage oracle requires a coverage contract")
    physical_bindings = {"status": "not_analyzed", "bindings": []}
    if layout_observation is not None and adapter_verification is not None:
        model_prefix = adapter_verification.get("model_prefix")
        if not isinstance(model_prefix, str):
            raise SidecarError("adapter verification has no model prefix")
        physical_bindings = resolve_physical_bindings(
            adapter_verification=adapter_verification,
            layout_observation=layout_observation,
            actual_headers=_physical_header_fingerprints(obj_dir, model_prefix),
            producer=producer,
            oracle=physical_oracle,
        )
    coverage_mapping: dict[str, Any] = {
        "status": "not_analyzed",
        "mappings": [],
    }
    if coverage_contract is not None and layout_observation is not None:
        if adapter_verification is None:
            raise SidecarError("coverage mapping requires adapter verification")
        model_prefix = adapter_verification.get("model_prefix")
        if not isinstance(model_prefix, str):
            raise SidecarError("adapter verification has no model prefix")
        try:
            coverage_mapping = build_toggle_coverage_mapping(
                tree=tree,
                meta=meta,
                semantic_hierarchy=semantic_projection["hierarchy"],
                source_root=source_root,
                obj_dir=obj_dir,
                model_prefix=model_prefix,
                producer=producer,
                coverage_contract=coverage_contract,
                layout_observation=layout_observation,
                oracle=coverage_oracle,
            )
        except CoverageMappingError as error:
            raise SidecarError(str(error)) from error
    manifest_status = "semantic_projection_only"
    if adapter_verification is not None:
        manifest_status = (
            "semantic_projection_verified"
            if adapter_verification["status"] == "matched"
            else "semantic_projection_mismatch"
        )
    if physical_bindings["status"] != "not_analyzed":
        manifest_status = {
            "resolved": "physical_bindings_resolved",
            "verified": "physical_bindings_verified",
            "mismatch": "physical_bindings_mismatch",
        }[physical_bindings["status"]]
    if coverage_mapping["status"] != "not_analyzed":
        manifest_status = {
            "resolved": "coverage_mapping_resolved",
            "verified": "coverage_mapping_verified",
            "mismatch": "coverage_mapping_mismatch",
        }[coverage_mapping["status"]]
    eval_effects: dict[str, Any] = (
        dict(eval_effect_observation)
        if eval_effect_observation is not None
        else {"status": "not_analyzed", "regions": []}
    )
    if eval_effects["status"] != "not_analyzed":
        manifest_status = {
            "resolved": "eval_effects_resolved",
            "verified": "eval_effects_verified",
            "mismatch": "eval_effects_mismatch",
        }[eval_effects["status"]]
    provenance: dict[str, Any] = {
        "artifact_mode": artifact_mode,
        "producer": producer,
        "top_module": top,
        "analysis_fingerprint": _sha256_bytes(
            _canonical_bytes(provenance_identity)
        ),
        "tree_sha256": tree_sha256,
        "semantic_meta_sha256": semantic_meta_sha256,
        "sources": list(source_records),
        "generated_cpp": generated_cpp,
    }
    if adapter_contract_sha256 is not None:
        provenance["adapter_contract_sha256"] = adapter_contract_sha256
    if layout_observation is not None:
        provenance["layout_observation_fingerprint"] = layout_observation.get(
            "observation_fingerprint"
        )
    if physical_oracle_sha256 is not None:
        provenance["physical_oracle_sha256"] = physical_oracle_sha256
    if coverage_contract_sha256 is not None:
        provenance["coverage_contract_sha256"] = coverage_contract_sha256
    if coverage_oracle_sha256 is not None:
        provenance["coverage_oracle_sha256"] = coverage_oracle_sha256
    if eval_effect_observation_fingerprint is not None:
        provenance["eval_effect_observation_fingerprint"] = (
            eval_effect_observation_fingerprint
        )
    if eval_effect_oracle_sha256 is not None:
        provenance["eval_effect_oracle_sha256"] = eval_effect_oracle_sha256
    if invocation is not None:
        provenance["invocation_fingerprint"] = _sha256_bytes(
            _canonical_bytes(invocation)
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "surface": MANIFEST_SURFACE,
        "status": manifest_status,
        "provenance": provenance,
        "semantic_projection": semantic_projection,
        "physical_bindings": physical_bindings,
        "checkpoint_projection": {"status": "not_analyzed", "fields": []},
        "coverage_mapping": coverage_mapping,
        "eval_effects": eval_effects,
        "non_claims": [
            "not_a_stable_upstream_verilator_abi",
            "not_a_pointer_free_semantic_state",
            (
                "physical_layout_not_analyzed"
                if physical_bindings["status"] == "not_analyzed"
                else "physical_layout_is_generated_cpp_abi_specific"
            ),
            (
                "eval_effects_not_analyzed"
                if eval_effects["status"] == "not_analyzed"
                else "device_clean_classification_is_policy_and_artifact_specific"
            ),
            "not_a_gpu_backend",
        ],
    }
    if adapter_verification is not None:
        manifest["adapter_verification"] = adapter_verification
    validate_manifest(manifest)
    return manifest


def analyze_manifest(
    *,
    source_root: Path,
    top: str,
    tree_path: Path,
    meta_path: Path,
    obj_dir: Path,
    producer: str,
    adapter: Mapping[str, Any] | None = None,
    layout_observation: Mapping[str, Any] | None = None,
    physical_oracle: Mapping[str, Any] | None = None,
    coverage_contract: Mapping[str, Any] | None = None,
    coverage_oracle: Mapping[str, Any] | None = None,
    eval_effect_observation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze existing artifacts without invoking Verilator or a shell."""

    return _manifest_from_artifacts(
        source_root=source_root.resolve(),
        top=top,
        tree_path=tree_path.resolve(),
        meta_path=meta_path.resolve(),
        obj_dir=obj_dir.resolve(),
        producer=producer,
        artifact_mode="external",
        source_records=[],
        adapter=adapter,
        layout_observation=layout_observation,
        physical_oracle=physical_oracle,
        coverage_contract=coverage_contract,
        coverage_oracle=coverage_oracle,
        eval_effect_observation=eval_effect_observation,
    )


def capture_manifest(
    *,
    source_root: Path,
    top: str,
    sources: Sequence[Path],
    work_dir: Path,
    verilator: str = "verilator",
    adapter: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the two pinned Verilator producer modes and return one manifest."""

    source_root = source_root.resolve()
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    source_records = _source_records(source_root, sources)
    source_arguments = [record["path"] for record in source_records]
    version = _verilator_version(verilator)
    common_arguments = [
        "--top-module",
        top,
        "--coverage-toggle",
        "--public-flat-rw",
        *source_arguments,
    ]
    tree_path = work_dir / f"V{top}.tree.json"
    meta_path = work_dir / f"V{top}.tree.meta.json"
    obj_dir = work_dir / "obj_dir"
    _run(
        [
            verilator,
            "--json-only",
            "--no-json-edit-nums",
            "--json-only-output",
            tree_path.as_posix(),
            "--json-only-meta-output",
            meta_path.as_posix(),
            *common_arguments,
        ],
        cwd=source_root,
    )
    _run(
        [
            verilator,
            "--cc",
            "-Mdir",
            obj_dir.as_posix(),
            *common_arguments,
        ],
        cwd=source_root,
    )

    invocation = {
        "producer": version,
        "top_module": top,
        "common_arguments": common_arguments,
        "sources": source_records,
    }
    return _manifest_from_artifacts(
        source_root=source_root,
        top=top,
        tree_path=tree_path,
        meta_path=meta_path,
        obj_dir=obj_dir,
        producer=version,
        artifact_mode="captured",
        source_records=source_records,
        invocation=invocation,
        adapter=adapter,
    )


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise SidecarError("unsupported manifest schema_version")
    if manifest.get("surface") != MANIFEST_SURFACE:
        raise SidecarError("unexpected manifest surface")
    semantic = manifest.get("semantic_projection")
    if not isinstance(semantic, Mapping):
        raise SidecarError("semantic_projection must be an object")
    entities = semantic.get("entities")
    if not isinstance(entities, list):
        raise SidecarError("semantic_projection.entities must be an array")
    identifiers = [
        entity.get("semantic_id")
        for entity in entities
        if isinstance(entity, Mapping)
    ]
    if len(identifiers) != len(entities) or any(
        not isinstance(identifier, str) for identifier in identifiers
    ):
        raise SidecarError("every semantic entity must have a string semantic_id")
    if len(set(identifiers)) != len(identifiers):
        raise SidecarError("semantic entity identifiers must be unique")
    hierarchy = semantic.get("hierarchy")
    if not isinstance(hierarchy, Mapping):
        raise SidecarError("semantic_projection.hierarchy must be an object")
    instances = hierarchy.get("instances")
    unresolved = hierarchy.get("unresolved")
    if not isinstance(instances, list) or not isinstance(unresolved, list):
        raise SidecarError("hierarchy instances and unresolved must be arrays")
    if hierarchy.get("instance_count") != len(instances):
        raise SidecarError("hierarchy instance_count does not match instances")
    if hierarchy.get("unresolved_count") != len(unresolved):
        raise SidecarError("hierarchy unresolved_count does not match unresolved")
    instance_identifiers = [
        instance.get("instance_id")
        for instance in instances
        if isinstance(instance, Mapping)
    ]
    instance_paths = [
        instance.get("canonical_path")
        for instance in instances
        if isinstance(instance, Mapping)
    ]
    if (
        len(instance_identifiers) != len(instances)
        or any(not isinstance(value, str) for value in instance_identifiers)
        or len(set(instance_identifiers)) != len(instance_identifiers)
    ):
        raise SidecarError("hierarchy instance identifiers must be unique strings")
    if (
        len(instance_paths) != len(instances)
        or any(not isinstance(value, str) for value in instance_paths)
        or len(set(instance_paths)) != len(instance_paths)
    ):
        raise SidecarError("hierarchy canonical paths must be unique strings")
    for section, expected_status in (
        ("checkpoint_projection", "not_analyzed"),
    ):
        value = manifest.get(section)
        if not isinstance(value, Mapping) or value.get("status") != expected_status:
            raise SidecarError(f"{section} must fail closed as {expected_status}")
    eval_effects = manifest.get("eval_effects")
    if not isinstance(eval_effects, Mapping):
        raise SidecarError("eval_effects must be an object")
    if eval_effects.get("status") == "not_analyzed":
        if eval_effects.get("regions") != []:
            raise SidecarError("unexamined eval effects must have no regions")
    else:
        try:
            validate_eval_effects(eval_effects)
        except EvalEffectError as error:
            raise SidecarError(str(error)) from error
    coverage = manifest.get("coverage_mapping")
    if not isinstance(coverage, Mapping):
        raise SidecarError("coverage_mapping must be an object")
    try:
        validate_coverage_mapping(coverage)
    except CoverageMappingError as error:
        raise SidecarError(str(error)) from error
    adapter_verification = manifest.get("adapter_verification")
    if adapter_verification is not None:
        if not isinstance(adapter_verification, Mapping):
            raise SidecarError("adapter_verification must be an object")
        signal_results = adapter_verification.get("signals")
        if not isinstance(signal_results, list):
            raise SidecarError("adapter_verification.signals must be an array")
        if adapter_verification.get("signal_count") != len(signal_results):
            raise SidecarError("adapter signal_count does not match signals")
        matched = sum(
            isinstance(signal, Mapping) and signal.get("status") == "matched"
            for signal in signal_results
        )
        if adapter_verification.get("matched_count") != matched:
            raise SidecarError("adapter matched_count does not match signals")
        if adapter_verification.get("unmatched_count") != len(signal_results) - matched:
            raise SidecarError("adapter unmatched_count does not match signals")
    physical = manifest.get("physical_bindings")
    if not isinstance(physical, Mapping):
        raise SidecarError("physical_bindings must be an object")
    physical_status = physical.get("status")
    if physical_status not in {"not_analyzed", "resolved", "verified", "mismatch"}:
        raise SidecarError("physical_bindings has an unsupported status")
    physical_rows = physical.get("bindings")
    if not isinstance(physical_rows, list):
        raise SidecarError("physical_bindings.bindings must be an array")
    if physical_status == "not_analyzed":
        if physical_rows:
            raise SidecarError("unexamined physical bindings must be empty")
    else:
        if adapter_verification is None:
            raise SidecarError("physical bindings require adapter verification")
        if physical.get("binding_count") != len(physical_rows):
            raise SidecarError("physical binding_count does not match bindings")
        physical_names: list[str] = []
        physical_ids: list[str] = []
        state_image = physical.get("state_image")
        if not isinstance(state_image, Mapping):
            raise SidecarError("physical state_image must be an object")
        storage_size = state_image.get("bytes")
        root_offset = state_image.get("root_offset_bytes")
        if (
            not isinstance(storage_size, int)
            or storage_size <= 0
            or not isinstance(root_offset, int)
            or root_offset < 0
            or root_offset >= storage_size
        ):
            raise SidecarError("physical state image size/root offset is invalid")
        statuses: list[str] = []
        for row in physical_rows:
            if not isinstance(row, Mapping):
                raise SidecarError("physical binding must be an object")
            name = row.get("name")
            semantic_id = row.get("semantic_id")
            row_status = row.get("status")
            offset = row.get("state_offset")
            size = row.get("size_bytes")
            if not isinstance(name, str) or not isinstance(semantic_id, str):
                raise SidecarError("physical binding identity must use strings")
            if row_status not in {"resolved", "verified", "mismatch"}:
                raise SidecarError(f"physical binding {name!r} has invalid status")
            if (
                not isinstance(offset, int)
                or offset < 0
                or not isinstance(size, int)
                or size <= 0
                or offset + size > storage_size
            ):
                raise SidecarError(f"physical binding {name!r} is outside state image")
            physical_names.append(name)
            physical_ids.append(semantic_id)
            statuses.append(row_status)
        if len(set(physical_names)) != len(physical_names):
            raise SidecarError("physical binding names must be unique")
        if len(set(physical_ids)) != len(physical_ids):
            raise SidecarError("physical semantic identifiers must be unique")
        if physical.get("verified_count") != statuses.count("verified"):
            raise SidecarError("physical verified_count does not match bindings")
        if physical.get("resolved_count") != statuses.count("resolved"):
            raise SidecarError("physical resolved_count does not match bindings")
        if physical.get("mismatch_count") != statuses.count("mismatch"):
            raise SidecarError("physical mismatch_count does not match bindings")
        issues = physical.get("issues")
        if not isinstance(issues, list) or any(
            not isinstance(issue, str) for issue in issues
        ):
            raise SidecarError("physical issues must be an array of strings")
        if physical_status == "verified" and (
            statuses.count("verified") != len(statuses) or issues
        ):
            raise SidecarError("verified physical bindings must all be verified")
        if physical_status == "resolved" and (
            statuses.count("resolved") != len(statuses) or issues
        ):
            raise SidecarError("resolved physical bindings must all be resolved")
        if physical_status == "mismatch" and not (
            statuses.count("mismatch") or issues
        ):
            raise SidecarError("mismatched physical bindings need mismatch evidence")
    serialized = json.dumps(manifest, ensure_ascii=False)
    if re.search(r'(?<![A-Za-z0-9_])/(?:home|tmp)/', serialized):
        raise SidecarError("manifest contains a local absolute path")


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    validate_manifest(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
