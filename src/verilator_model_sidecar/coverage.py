"""Fail-closed semantic-to-physical mapping for Verilator toggle coverage."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .native_coverage import NativeCoverageError, project_native_toggle_coverage
from .producer import is_supported_semantic_producer


COVERAGE_CONTRACT_SURFACE = "verilator_toggle_coverage_contract"
COVERAGE_ORACLE_SURFACE = "verilator_toggle_coverage_oracle"


class CoverageMappingError(RuntimeError):
    """Raised when coverage artifacts cannot prove an exact mapping."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _walk(value: Any) -> Iterator[Mapping[str, Any]]:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            yield current
            pending.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            pending.extend(reversed(current))


def coverage_region_contracts(
    contract: Mapping[str, Any],
    *,
    expected_model_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Validate and normalize the physical arrays named by a coverage contract."""

    if contract.get("surface") != COVERAGE_CONTRACT_SURFACE:
        raise CoverageMappingError("unexpected coverage contract surface")
    if contract.get("schema_version") != 1:
        raise CoverageMappingError("unsupported coverage contract schema_version")
    source_model = contract.get("source_model")
    if not isinstance(source_model, Mapping):
        raise CoverageMappingError("coverage contract source_model must be an object")
    model_prefix = source_model.get("prefix")
    if not isinstance(model_prefix, str) or re.fullmatch(r"[A-Za-z_]\w*", model_prefix) is None:
        raise CoverageMappingError("coverage contract has an invalid model prefix")
    if expected_model_prefix is not None and model_prefix != expected_model_prefix:
        raise CoverageMappingError("coverage contract model prefix mismatch")
    raw_regions = contract.get("regions")
    if not isinstance(raw_regions, Mapping) or not raw_regions:
        raise CoverageMappingError("coverage contract regions must be a non-empty object")
    regions: list[dict[str, Any]] = []
    for name, raw_region in sorted(raw_regions.items()):
        if not isinstance(name, str) or not isinstance(raw_region, Mapping):
            raise CoverageMappingError("coverage contract region must be a named object")
        binding = raw_region.get("binding")
        word_bits = raw_region.get("word_bits")
        kind = raw_region.get("kind")
        if not isinstance(binding, str):
            raise CoverageMappingError(f"coverage region {name!r} has no binding")
        if (
            isinstance(word_bits, bool)
            or not isinstance(word_bits, int)
            or word_bits <= 0
            or word_bits % 8
        ):
            raise CoverageMappingError(
                f"coverage region {name!r} has an invalid word width"
            )
        if kind != "toggle_direction_counters":
            raise CoverageMappingError(
                f"coverage region {name!r} has unsupported kind {kind!r}"
            )
        regions.append(
            {
                "name": name,
                "binding": binding,
                "kind": kind,
                "word_bits": word_bits,
            }
        )
    return regions


def _normal_path(raw_path: str, source_root: Path) -> str:
    if raw_path.startswith("<") and raw_path.endswith(">"):
        return raw_path
    candidate = Path(raw_path)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(source_root)
        except ValueError as error:
            raise CoverageMappingError(
                f"coverage source path is outside --source-root: {raw_path}"
            ) from error
    normalized = candidate.as_posix()
    if normalized == ".." or normalized.startswith("../"):
        raise CoverageMappingError(
            f"coverage source path escapes --source-root: {raw_path}"
        )
    return normalized


def _source_location(
    raw_location: Any,
    files: Mapping[str, Any],
    source_root: Path,
) -> dict[str, Any]:
    if not isinstance(raw_location, str):
        raise CoverageMappingError("toggle declaration has no source location")
    match = re.fullmatch(r"([^,]+),(\d+):(\d+),(\d+):(\d+)", raw_location)
    if match is None:
        raise CoverageMappingError(
            f"unsupported toggle declaration source location {raw_location!r}"
        )
    file_key, line, column, end_line, end_column = match.groups()
    file_record = files.get(file_key)
    if not isinstance(file_record, Mapping):
        raise CoverageMappingError(
            f"toggle declaration refers to unknown source key {file_key!r}"
        )
    filename = str(file_record.get("filename", file_key))
    return {
        "path": _normal_path(filename, source_root),
        "line": int(line),
        "column": int(column),
        "end_line": int(end_line),
        "end_column": int(end_column),
    }


def _dtype_index(tree: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for node in _walk(tree):
        address = node.get("addr")
        if str(node.get("type", "")).endswith("DTYPE") and isinstance(address, str):
            result[address] = node
    return result


def _dtype_shape(
    address: Any,
    dtypes: Mapping[str, Mapping[str, Any]],
    seen: tuple[str, ...] = (),
) -> tuple[int | None, bool | None]:
    if not isinstance(address, str) or address in seen:
        return None, None
    dtype = dtypes.get(address)
    if not isinstance(dtype, Mapping):
        return None, None
    raw_range = dtype.get("range")
    if isinstance(raw_range, str):
        match = re.fullmatch(r"\s*(-?\d+)\s*:\s*(-?\d+)\s*", raw_range)
        if match is None:
            return None, None
        high, low = (int(value) for value in match.groups())
        return abs(high - low) + 1, True
    node_type = str(dtype.get("type", ""))
    if node_type == "BASICDTYPE":
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
        return scalar_widths.get(str(dtype.get("keyword", "")).lower()), False
    for key in ("refDTypep", "childDTypep"):
        target = dtype.get(key)
        if isinstance(target, str):
            width, ranged = _dtype_shape(target, dtypes, seen + (address,))
            if width is not None:
                return width, ranged
    return None, None


def _selection_base(expression: Mapping[str, Any]) -> str | None:
    current: Mapping[str, Any] = expression
    while current.get("type") in {"SEL", "ARRAYSEL"}:
        from_nodes = current.get("fromp")
        if (
            not isinstance(from_nodes, list)
            or len(from_nodes) != 1
            or not isinstance(from_nodes[0], Mapping)
        ):
            return None
        current = from_nodes[0]
    if current.get("type") == "VARREF" and isinstance(current.get("name"), str):
        return str(current["name"])
    return None


def _expression_descriptor(
    expression: Mapping[str, Any],
    dtypes: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], int]:
    node_type = str(expression.get("type", ""))
    width, source_ranged = _dtype_shape(expression.get("dtypep"), dtypes)
    width_const = expression.get("widthConst")
    if isinstance(width_const, int) and not isinstance(width_const, bool):
        width = width_const
    literal = expression.get("name")
    if width is None and node_type == "CONST" and isinstance(literal, str):
        match = re.match(r"(\d+)'", literal)
        if match is not None:
            width = int(match.group(1))
    if not isinstance(width, int) or width <= 0:
        raise CoverageMappingError(
            f"cannot resolve width of toggle expression type {node_type!r}"
        )
    if node_type == "VARREF":
        name = expression.get("name")
        if not isinstance(name, str) or not name:
            raise CoverageMappingError("toggle VARREF has no name")
        descriptor = {
            "kind": "variable",
            "base_name": name,
            "source_ranged": bool(source_ranged),
        }
    elif node_type in {"SEL", "ARRAYSEL"}:
        base_name = _selection_base(expression)
        if not isinstance(base_name, str) or not base_name:
            raise CoverageMappingError("toggle selection has no variable base")
        selector_key = "lsbp" if node_type == "SEL" else "bitp"
        selector_nodes = expression.get(selector_key)
        selector_literal = None
        if (
            isinstance(selector_nodes, list)
            and len(selector_nodes) == 1
            and isinstance(selector_nodes[0], Mapping)
        ):
            selector_literal = selector_nodes[0].get("name")
        descriptor = {
            "kind": "selection" if node_type == "SEL" else "array_selection",
            "base_name": base_name,
            "lsb" if node_type == "SEL" else "index": selector_literal,
        }
    elif node_type == "CONST":
        if not isinstance(literal, str) or not literal:
            raise CoverageMappingError("toggle constant has no literal")
        descriptor = {"kind": "constant", "literal": literal}
    else:
        raise CoverageMappingError(
            f"unsupported toggle expression type {node_type!r}"
        )
    descriptor["width_bits"] = width
    return descriptor, width


def _declaration_id(identity: Mapping[str, Any]) -> str:
    return "toggle-decl:v1:" + _sha256_bytes(_canonical_bytes(identity))


def _extract_ast_declarations(
    tree: Mapping[str, Any],
    meta: Mapping[str, Any],
    *,
    source_root: Path,
    module_definition_ids: Mapping[str, str],
) -> dict[str, list[dict[str, Any]]]:
    files = meta.get("files")
    if not isinstance(files, Mapping):
        raise CoverageMappingError("tree metadata has no files mapping")
    dtypes = _dtype_index(tree)
    modules = tree.get("modulesp")
    if not isinstance(modules, list):
        raise CoverageMappingError("Verilator JSON has no modules array")
    result: dict[str, list[dict[str, Any]]] = {}
    for module in modules:
        if not isinstance(module, Mapping):
            continue
        module_name = str(module.get("name", ""))
        declarations = {
            node["addr"]: node
            for node in _walk(module.get("stmtsp", []))
            if node.get("type") == "COVERTOGGLEDECL"
            and isinstance(node.get("addr"), str)
        }
        if not declarations:
            continue
        if module_name in result:
            raise CoverageMappingError(
                f"duplicate covered module name {module_name!r}"
            )
        definition_id = module_definition_ids.get(module_name)
        if not isinstance(definition_id, str):
            raise CoverageMappingError(
                f"covered module {module_name!r} is absent from elaborated hierarchy"
            )
        rows: list[dict[str, Any]] = []
        referenced: Counter[str] = Counter()
        for ordinal, toggle in enumerate(
            node
            for node in _walk(module.get("stmtsp", []))
            if node.get("type") == "COVERTOGGLE"
        ):
            increments = toggle.get("incp")
            expressions = toggle.get("origp")
            if (
                not isinstance(increments, list)
                or len(increments) != 1
                or not isinstance(increments[0], Mapping)
                or not isinstance(expressions, list)
                or len(expressions) != 1
                or not isinstance(expressions[0], Mapping)
            ):
                raise CoverageMappingError(
                    f"covered module {module_name!r} has unsupported toggle shape"
                )
            declaration_address = increments[0].get("declp")
            declaration = declarations.get(declaration_address)
            if not isinstance(declaration, Mapping) or not isinstance(
                declaration_address, str
            ):
                raise CoverageMappingError(
                    f"covered module {module_name!r} has an unlinked toggle declaration"
                )
            referenced[declaration_address] += 1
            page = declaration.get("page")
            if not isinstance(page, str) or not page:
                raise CoverageMappingError("toggle declaration has no coverage page")
            source = _source_location(
                declaration.get("loc"), files, source_root
            )
            expression, width_bits = _expression_descriptor(expressions[0], dtypes)
            identity = {
                "schema": "ast-toggle-declaration-v1",
                "module_definition_id": definition_id,
                "source": source,
                "page": page,
                "expression": expression,
                "module_ordinal": ordinal,
            }
            rows.append(
                {
                    "declaration_id": _declaration_id(identity),
                    "module_definition_id": definition_id,
                    "module": module_name,
                    "original_module": str(module.get("origName", module_name)),
                    "module_ordinal": ordinal,
                    "source": source,
                    "page": page,
                    "expression": expression,
                    "width_bits": width_bits,
                }
            )
        if set(referenced) != set(declarations) or any(
            count != 1 for count in referenced.values()
        ):
            raise CoverageMappingError(
                f"covered module {module_name!r} does not have a one-to-one "
                "COVERTOGGLEDECL/COVERTOGGLE relation"
            )
        if len({row["declaration_id"] for row in rows}) != len(rows):
            raise CoverageMappingError(
                f"covered module {module_name!r} has duplicate declaration identities"
            )
        result[module_name] = rows
    if not result:
        raise CoverageMappingError("JSON AST contains no toggle declarations")
    return result


def _canonical_payload(
    call: Mapping[str, Any],
    *,
    bit_index: int | None,
    transition: str,
) -> dict[str, Any]:
    return {
        "filename": call["filename"],
        "line": call["line"],
        "column": call["column"],
        "hierarchy_suffix": call["hierarchy_suffix"],
        "page": call["page"],
        "comment": call["comment"],
        "bit_index": bit_index,
        "transition": transition,
    }


def _canonical_id(payload: Mapping[str, Any]) -> str:
    return "toggle:" + _sha256_bytes(_canonical_bytes(payload))


def _canonical_group_id(member_ids: Iterable[str]) -> str:
    return "toggle-group:" + _sha256_bytes(
        _canonical_bytes(sorted(member_ids))
    )


def _label_matches_expression(comment: str, expression: Mapping[str, Any]) -> bool:
    kind = expression.get("kind")
    base_name = expression.get("base_name")
    if kind == "variable" and isinstance(base_name, str):
        # Verilator retains lexical generate/block scopes in C++ coverage labels
        # even though the optimized VARREF only retains the leaf identifier.
        return re.search(rf"(?:^|\.){re.escape(base_name)}$", comment) is not None
    if kind in {"selection", "array_selection"} and isinstance(base_name, str):
        # The same lexical prefix may precede a selected aggregate.  Requiring a
        # component boundary on both sides still rejects unrelated identifiers.
        return (
            re.search(
                rf"(?:^|\.){re.escape(base_name)}(?:$|[.\[])", comment
            )
            is not None
        )
    if kind == "constant":
        # The optimized AST contains the literal but no surviving source label.
        # Source/page/width/order still link this declaration fail-closed.
        return True
    return False


def _validate_coverage_oracle(oracle: Mapping[str, Any], model_prefix: str) -> None:
    if oracle.get("surface") != COVERAGE_ORACLE_SURFACE:
        raise CoverageMappingError("unexpected coverage oracle surface")
    if oracle.get("schema_version") != 1:
        raise CoverageMappingError("unsupported coverage oracle schema_version")
    if oracle.get("model_prefix") != model_prefix:
        raise CoverageMappingError("coverage oracle model prefix mismatch")
    if not isinstance(oracle.get("region"), Mapping):
        raise CoverageMappingError("coverage oracle region must be an object")
    if not isinstance(oracle.get("metrics"), Mapping):
        raise CoverageMappingError("coverage oracle metrics must be an object")
    if not isinstance(oracle.get("fingerprints"), Mapping):
        raise CoverageMappingError("coverage oracle fingerprints must be an object")


def build_toggle_coverage_mapping(
    *,
    tree: Mapping[str, Any],
    meta: Mapping[str, Any],
    semantic_hierarchy: Mapping[str, Any],
    source_root: Path,
    model_prefix: str,
    producer: str,
    native_manifest: Mapping[str, Any],
    coverage_contract: Mapping[str, Any],
    layout_observation: Mapping[str, Any],
    oracle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Join AST declarations, native lowering, and measured counter storage."""

    if not is_supported_semantic_producer(producer):
        raise CoverageMappingError(
            "coverage mapping requires a supported semantic producer"
        )
    regions = coverage_region_contracts(
        coverage_contract, expected_model_prefix=model_prefix
    )
    if len(regions) != 1:
        raise CoverageMappingError("toggle mapping currently requires one coverage region")
    region_contract = regions[0]
    if region_contract["word_bits"] != 32:
        raise CoverageMappingError("Verilator 5.050 toggle counters must be uint32 words")
    if layout_observation.get("model_prefix") != model_prefix:
        raise CoverageMappingError("coverage layout model prefix mismatch")
    layout_regions = layout_observation.get("coverage_regions")
    if not isinstance(layout_regions, list):
        raise CoverageMappingError(
            "layout observation contains no measured coverage regions"
        )
    measured_matches = [
        row
        for row in layout_regions
        if isinstance(row, Mapping) and row.get("name") == region_contract["name"]
    ]
    if len(measured_matches) != 1:
        raise CoverageMappingError(
            "coverage contract does not resolve to one measured layout region"
        )
    measured = measured_matches[0]
    for key in ("binding", "word_bits"):
        if measured.get(key) != region_contract[key]:
            raise CoverageMappingError(f"coverage layout {key} mismatch")
    word_count = measured.get("word_count")
    state_offset = measured.get("state_offset")
    size_bytes = measured.get("size_bytes")
    if (
        not isinstance(word_count, int)
        or word_count <= 0
        or not isinstance(state_offset, int)
        or state_offset < 0
        or not isinstance(size_bytes, int)
        or size_bytes != word_count * 4
    ):
        raise CoverageMappingError("measured coverage region shape is invalid")

    hierarchy_instances = semantic_hierarchy.get("instances")
    if not isinstance(hierarchy_instances, list):
        raise CoverageMappingError("semantic hierarchy has no instances")
    instance_by_path: dict[str, Mapping[str, Any]] = {}
    module_definition_ids: dict[str, str] = {}
    for instance in hierarchy_instances:
        if not isinstance(instance, Mapping):
            raise CoverageMappingError("semantic hierarchy instance is malformed")
        path = instance.get("canonical_path")
        module = instance.get("module")
        definition_id = instance.get("module_definition_id")
        if not all(isinstance(value, str) for value in (path, module, definition_id)):
            raise CoverageMappingError("semantic hierarchy identity is malformed")
        instance_by_path[str(path)] = instance
        previous = module_definition_ids.setdefault(str(module), str(definition_id))
        if previous != definition_id:
            raise CoverageMappingError(
                f"module name {module!r} maps to multiple definitions"
            )

    ast_by_module = _extract_ast_declarations(
        tree,
        meta,
        source_root=source_root.resolve(),
        module_definition_ids=module_definition_ids,
    )
    try:
        native = project_native_toggle_coverage(
            native_manifest, expected_model_prefix=model_prefix
        )
    except NativeCoverageError as error:
        raise CoverageMappingError(str(error)) from error
    native_storage_matches = [
        storage
        for storage in native["storages"]
        if storage["binding"] == region_contract["binding"]
    ]
    if len(native_storage_matches) != 1:
        raise CoverageMappingError(
            "coverage contract does not resolve to one native coverage storage"
        )
    native_storage = native_storage_matches[0]
    if native_storage["word_count"] != word_count:
        raise CoverageMappingError("native/measured coverage word count mismatch")
    native_storage_id = native_storage["storage_id"]
    calls: list[dict[str, Any]] = []
    for declaration in native["lowering_declarations"]:
        if declaration["storage_id"] != native_storage_id:
            continue
        source = declaration["source"]
        range_record = declaration["range"]
        calls.append(
            {
                "lowering_id": declaration["lowering_id"],
                "template_ordinal": declaration["template_ordinal"],
                "begin": range_record["begin"],
                "end": range_record["end"],
                "ranged": range_record["ranged"],
                "raw_base_word": declaration["raw_base_word"],
                "filename": _normal_path(str(source["file"]), source_root.resolve()),
                "line": source["line"],
                "column": source["column"],
                "hierarchy_suffix": declaration["hierarchy_suffix"],
                "page": declaration["page"],
                "comment": declaration["comment"],
            }
        )
    updates = [
        region
        for region in native["update_regions"]
        if region["storage_id"] == native_storage_id
    ]
    transitions = native["transition_order"]
    helper = {
        "authority": native["authority"],
        "native_producer": native["producer"],
        "word_offset_order": transitions,
        "counter_stride_words_per_bit": native["counter_stride_words_per_bit"],
    }

    calls_by_hierarchy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        suffix = call["hierarchy_suffix"]
        if not isinstance(suffix, str) or not suffix.startswith("."):
            raise CoverageMappingError("generated toggle hierarchy is not a suffix")
        calls_by_hierarchy[suffix[1:]].append(call)
    expected_covered_instances = {
        path
        for path, instance in instance_by_path.items()
        if instance.get("module") in ast_by_module
    }
    if set(calls_by_hierarchy) != expected_covered_instances:
        missing = sorted(expected_covered_instances - set(calls_by_hierarchy))
        unexpected = sorted(set(calls_by_hierarchy) - expected_covered_instances)
        raise CoverageMappingError(
            "generated/AST covered instance sets differ: "
            f"missing={missing[:4]}, unexpected={unexpected[:4]}"
        )

    canonical_records: list[dict[str, Any]] = []
    semantic_bindings: list[dict[str, Any]] = []
    normalized_calls: list[dict[str, Any]] = []
    by_raw_word: dict[int, list[str]] = defaultdict(list)
    for hierarchy_path in sorted(calls_by_hierarchy):
        instance = instance_by_path[hierarchy_path]
        module_name = str(instance["module"])
        declarations = ast_by_module[module_name]
        instance_calls = calls_by_hierarchy[hierarchy_path]
        if len(instance_calls) != len(declarations):
            raise CoverageMappingError(
                f"covered instance {hierarchy_path!r} has {len(instance_calls)} "
                f"insertions for {len(declarations)} AST declarations"
            )
        for call, declaration in zip(instance_calls, declarations, strict=True):
            width = abs(int(call["end"]) - int(call["begin"])) + 1
            source = declaration["source"]
            metadata_matches = (
                call["filename"] == source["path"]
                and call["line"] == source["line"]
                and call["column"] == source["column"]
                and call["page"] == declaration["page"]
                and width == declaration["width_bits"]
            )
            if not metadata_matches:
                raise CoverageMappingError(
                    f"generated insertion does not match AST declaration "
                    f"{declaration['declaration_id']}"
                )
            if not _label_matches_expression(
                str(call["comment"]), declaration["expression"]
            ):
                raise CoverageMappingError(
                    f"generated label {call['comment']!r} does not match AST "
                    f"expression for {declaration['declaration_id']}"
                )
            base = int(call["raw_base_word"])
            if base < 0 or base + width * 2 > word_count:
                raise CoverageMappingError(
                    "generated toggle insertion lies outside measured coverage storage"
                )
            normalized_calls.append(
                {
                    **call,
                    "ast_declaration_id": declaration["declaration_id"],
                    "instance_id": instance["instance_id"],
                    "width_bits": width,
                }
            )
            step = 1 if int(call["end"]) >= int(call["begin"]) else -1
            bits = range(int(call["begin"]), int(call["end"]) + step, step)
            for ordinal, bit_index in enumerate(bits):
                for direction_offset, transition in enumerate(transitions):
                    payload = _canonical_payload(
                        call,
                        bit_index=bit_index if call["ranged"] else None,
                        transition=transition,
                    )
                    canonical_id = _canonical_id(payload)
                    raw_word_index = base + ordinal * 2 + direction_offset
                    canonical_records.append(
                        {"canonical_id": canonical_id, **payload}
                    )
                    semantic_bindings.append(
                        {
                            "canonical_id": canonical_id,
                            "ast_declaration_id": declaration["declaration_id"],
                            "instance_id": instance["instance_id"],
                            "raw_word_index": raw_word_index,
                        }
                    )
                    by_raw_word[raw_word_index].append(canonical_id)
    canonical_ids = [record["canonical_id"] for record in canonical_records]
    if len(set(canonical_ids)) != len(canonical_ids):
        raise CoverageMappingError("canonical toggle identities are not unique")
    if set(by_raw_word) != set(range(word_count)):
        missing = sorted(set(range(word_count)) - set(by_raw_word))
        raise CoverageMappingError(
            f"generated toggle mapping has unbound physical words {missing[:8]}"
        )

    insertion_regions = Counter(
        (int(call["raw_base_word"]), int(call["width_bits"]))
        for call in normalized_calls
    )
    update_regions = Counter(
        {
            (int(update["raw_base_word"]), int(update["width_bits"])): int(
                update["site_count"]
            )
            for update in updates
        }
    )
    if set(insertion_regions) != set(update_regions):
        raise CoverageMappingError(
            "generated insertion and update physical regions differ"
        )
    updated_words: set[int] = set()
    region_records: list[dict[str, Any]] = []
    for base, width in sorted(insertion_regions):
        if base < 0 or base + 2 * width > word_count:
            raise CoverageMappingError("coverage update region lies outside storage")
        updated_words.update(range(base, base + 2 * width))
        region_records.append(
            {
                "raw_base_word": base,
                "width_bits": width,
                "insertion_call_count": insertion_regions[(base, width)],
                "update_site_count": update_regions[(base, width)],
            }
        )
    if updated_words != set(range(word_count)):
        missing = sorted(set(range(word_count)) - updated_words)
        raise CoverageMappingError(
            f"generated updates do not cover physical words {missing[:8]}"
        )

    declarations = sorted(
        (row for rows in ast_by_module.values() for row in rows),
        key=lambda row: (row["module"], row["module_ordinal"]),
    )
    canonical_records.sort(key=lambda record: record["canonical_id"])
    semantic_bindings.sort(key=lambda record: record["canonical_id"])
    adapter_bindings = [
        {
            "canonical_id": binding["canonical_id"],
            "raw_word_index": binding["raw_word_index"],
        }
        for binding in semantic_bindings
    ]
    physical_words: list[dict[str, Any]] = []
    canonical_groups: list[dict[str, Any]] = []
    group_bindings: list[dict[str, Any]] = []
    for word_index in range(word_count):
        members = sorted(by_raw_word[word_index])
        group_id = _canonical_group_id(members)
        canonical_groups.append(
            {
                "canonical_group_id": group_id,
                "member_canonical_ids": members,
            }
        )
        group_bindings.append(
            {"canonical_group_id": group_id, "raw_word_index": word_index}
        )
        physical_words.append(
            {
                "raw_word_index": word_index,
                "state_offset": state_offset + word_index * 4,
                "canonical_group_id": group_id,
                "member_canonical_ids": members,
                "member_count": len(members),
                "hit_aggregation": (
                    "direct" if len(members) == 1 else "logical_or_alias"
                ),
            }
        )

    metrics = {
        "ast_declaration_count": len(declarations),
        "covered_instance_count": len(calls_by_hierarchy),
        "insertion_call_count": len(normalized_calls),
        "semantic_observation_count": len(canonical_records),
        "raw_word_count": word_count,
        "aliased_raw_word_count": sum(
            len(members) > 1 for members in by_raw_word.values()
        ),
        "maximum_canonical_identities_per_raw_word": max(
            len(members) for members in by_raw_word.values()
        ),
        "update_site_count": sum(update_regions.values()),
        "update_region_count": len(region_records),
        "updated_raw_word_count": len(updated_words),
    }
    fingerprints = {
        "canonical_manifest_sha256": _sha256_bytes(
            _canonical_bytes(canonical_records)
        ),
        "canonical_group_manifest_sha256": _sha256_bytes(
            _canonical_bytes(canonical_groups)
        ),
        "verilator_binding_sha256": _sha256_bytes(
            _canonical_bytes(adapter_bindings)
        ),
        "verilator_group_binding_sha256": _sha256_bytes(
            _canonical_bytes(group_bindings)
        ),
        "ast_declaration_manifest_sha256": _sha256_bytes(
            _canonical_bytes(declarations)
        ),
        "ast_link_manifest_sha256": _sha256_bytes(
            _canonical_bytes(semantic_bindings)
        ),
        "lowering_region_manifest_sha256": _sha256_bytes(
            _canonical_bytes(region_records)
        ),
    }
    observed_region = {
        **region_contract,
        "state_offset": state_offset,
        "size_bytes": size_bytes,
        "word_count": word_count,
        "word_offset": 0,
        "hit_semantics": "nonzero_word",
    }
    issues: list[str] = []
    expected_region = None
    expected_metrics = None
    expected_fingerprints = None
    if oracle is not None:
        _validate_coverage_oracle(oracle, model_prefix)
        expected_region = oracle["region"]
        expected_metrics = oracle["metrics"]
        expected_fingerprints = oracle["fingerprints"]
        for key, actual in observed_region.items():
            if key in expected_region and expected_region.get(key) != actual:
                issues.append(f"oracle_region_{key}_mismatch")
        for key, actual in metrics.items():
            if key in expected_metrics and expected_metrics.get(key) != actual:
                issues.append(f"oracle_metric_{key}_mismatch")
        for key, actual in fingerprints.items():
            if key in expected_fingerprints and expected_fingerprints.get(key) != actual:
                issues.append(f"oracle_fingerprint_{key}_mismatch")
        missing_region = sorted(set(expected_region) - set(observed_region))
        missing_metrics = sorted(set(expected_metrics) - set(metrics))
        missing_fingerprints = sorted(set(expected_fingerprints) - set(fingerprints))
        issues.extend(f"unsupported_oracle_region_{key}" for key in missing_region)
        issues.extend(f"unsupported_oracle_metric_{key}" for key in missing_metrics)
        issues.extend(
            f"unsupported_oracle_fingerprint_{key}" for key in missing_fingerprints
        )
    status = "mismatch" if issues else ("verified" if oracle is not None else "resolved")
    result = {
        "status": status,
        "authority": {
            "semantic": "verilator_json_cover_toggle_declarations",
            "physical": "verilator_native_toggle_coverage_lowering",
            "storage": "measured_generated_cpp_abi",
        },
        "model_prefix": model_prefix,
        "region": {
            "status": status,
            **observed_region,
            "expected": dict(expected_region) if expected_region is not None else None,
        },
        "metrics": metrics,
        "expected_metrics": (
            dict(expected_metrics) if expected_metrics is not None else None
        ),
        "fingerprints": fingerprints,
        "expected_fingerprints": (
            dict(expected_fingerprints)
            if expected_fingerprints is not None
            else None
        ),
        "ast_declarations": declarations,
        "canonical_observations": canonical_records,
        "mappings": semantic_bindings,
        "physical_words": physical_words,
        "lowering": {
            "helper": helper,
            "generated_cpp_parse": "not_used",
            "generated_source_count": 0,
            "generated_sources": [],
            "regions": region_records,
        },
        "issues": sorted(set(issues)),
        "non_claims": [
            "raw_word_indices_are_verilator_local",
            "an_aliased_word_cannot_identify_which_member_toggled",
            "constant_expression_labels_are_linked_by_source_page_width_and_order",
            "not_a_cross_frontend_probe_implementation",
            "not_a_signoff_coverage_model",
        ],
    }
    validate_coverage_mapping(result)
    return result


def validate_coverage_mapping(mapping: Mapping[str, Any]) -> None:
    """Validate the integrity and fail-closed status of a coverage section."""

    status = mapping.get("status")
    if status == "not_analyzed":
        if mapping.get("mappings") != []:
            raise CoverageMappingError(
                "unexamined coverage mapping must have no mappings"
            )
        return
    if status not in {"resolved", "verified", "mismatch"}:
        raise CoverageMappingError("coverage mapping has an unsupported status")
    region = mapping.get("region")
    metrics = mapping.get("metrics")
    fingerprints = mapping.get("fingerprints")
    declarations = mapping.get("ast_declarations")
    observations = mapping.get("canonical_observations")
    bindings = mapping.get("mappings")
    physical_words = mapping.get("physical_words")
    lowering = mapping.get("lowering")
    issues = mapping.get("issues")
    if not all(
        isinstance(value, Mapping)
        for value in (region, metrics, fingerprints, lowering)
    ):
        raise CoverageMappingError("coverage mapping summary is malformed")
    if not all(
        isinstance(value, list)
        for value in (declarations, observations, bindings, physical_words, issues)
    ):
        raise CoverageMappingError("coverage mapping arrays are malformed")
    word_count = region.get("word_count")
    word_bits = region.get("word_bits")
    state_offset = region.get("state_offset")
    size_bytes = region.get("size_bytes")
    if (
        not isinstance(word_count, int)
        or word_count <= 0
        or word_bits != 32
        or not isinstance(state_offset, int)
        or state_offset < 0
        or size_bytes != word_count * 4
    ):
        raise CoverageMappingError("coverage region is invalid")
    if region.get("status") != status:
        raise CoverageMappingError("coverage region status mismatch")
    declaration_ids = [
        row.get("declaration_id") if isinstance(row, Mapping) else None
        for row in declarations
    ]
    if (
        any(not isinstance(value, str) for value in declaration_ids)
        or len(set(declaration_ids)) != len(declaration_ids)
        or metrics.get("ast_declaration_count") != len(declarations)
    ):
        raise CoverageMappingError("coverage declaration identities are invalid")
    observation_by_id: dict[str, Mapping[str, Any]] = {}
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise CoverageMappingError("canonical coverage observation is malformed")
        canonical_id = observation.get("canonical_id")
        payload = {
            key: observation.get(key)
            for key in (
                "filename",
                "line",
                "column",
                "hierarchy_suffix",
                "page",
                "comment",
                "bit_index",
                "transition",
            )
        }
        if not isinstance(canonical_id, str) or canonical_id != _canonical_id(payload):
            raise CoverageMappingError("canonical coverage identity mismatch")
        if canonical_id in observation_by_id:
            raise CoverageMappingError("canonical coverage identities are not unique")
        observation_by_id[canonical_id] = observation
    if metrics.get("semantic_observation_count") != len(observations):
        raise CoverageMappingError("semantic observation count mismatch")
    if fingerprints.get("canonical_manifest_sha256") != _sha256_bytes(
        _canonical_bytes(sorted(observations, key=lambda row: row["canonical_id"]))
    ):
        raise CoverageMappingError("canonical coverage manifest hash mismatch")
    bound_members: dict[int, list[str]] = defaultdict(list)
    binding_ids: set[str] = set()
    declaration_id_set = set(declaration_ids)
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise CoverageMappingError("coverage binding is malformed")
        canonical_id = binding.get("canonical_id")
        declaration_id = binding.get("ast_declaration_id")
        instance_id = binding.get("instance_id")
        raw_word_index = binding.get("raw_word_index")
        if (
            canonical_id not in observation_by_id
            or declaration_id not in declaration_id_set
            or not isinstance(instance_id, str)
            or not isinstance(raw_word_index, int)
            or raw_word_index < 0
            or raw_word_index >= word_count
        ):
            raise CoverageMappingError("coverage binding identity/range is invalid")
        if canonical_id in binding_ids:
            raise CoverageMappingError("canonical coverage binding is duplicated")
        binding_ids.add(str(canonical_id))
        bound_members[raw_word_index].append(str(canonical_id))
    if binding_ids != set(observation_by_id):
        raise CoverageMappingError("canonical coverage observations are not all bound")
    if metrics.get("raw_word_count") != word_count:
        raise CoverageMappingError("coverage raw word count mismatch")
    if len(physical_words) != word_count:
        raise CoverageMappingError("coverage physical word count mismatch")
    canonical_groups: list[dict[str, Any]] = []
    group_bindings: list[dict[str, Any]] = []
    group_ids: set[str] = set()
    for expected_index, physical in enumerate(physical_words):
        if not isinstance(physical, Mapping):
            raise CoverageMappingError("coverage physical word is malformed")
        members = sorted(bound_members[expected_index])
        if not members:
            raise CoverageMappingError("coverage physical word has no semantic member")
        group_id = _canonical_group_id(members)
        if group_id in group_ids:
            raise CoverageMappingError("coverage canonical groups are not unique")
        group_ids.add(group_id)
        if (
            physical.get("raw_word_index") != expected_index
            or physical.get("state_offset") != state_offset + expected_index * 4
            or physical.get("member_canonical_ids") != members
            or physical.get("member_count") != len(members)
            or physical.get("canonical_group_id") != group_id
            or physical.get("hit_aggregation")
            != ("direct" if len(members) == 1 else "logical_or_alias")
        ):
            raise CoverageMappingError("coverage physical word mapping mismatch")
        canonical_groups.append(
            {
                "canonical_group_id": group_id,
                "member_canonical_ids": members,
            }
        )
        group_bindings.append(
            {"canonical_group_id": group_id, "raw_word_index": expected_index}
        )
    if metrics.get("aliased_raw_word_count") != sum(
        len(members) > 1 for members in bound_members.values()
    ):
        raise CoverageMappingError("coverage alias count mismatch")
    if metrics.get("maximum_canonical_identities_per_raw_word") != max(
        len(members) for members in bound_members.values()
    ):
        raise CoverageMappingError("coverage maximum alias width mismatch")
    adapter_bindings = [
        {
            "canonical_id": binding["canonical_id"],
            "raw_word_index": binding["raw_word_index"],
        }
        for binding in bindings
    ]
    if fingerprints.get("verilator_binding_sha256") != _sha256_bytes(
        _canonical_bytes(adapter_bindings)
    ):
        raise CoverageMappingError("coverage binding hash mismatch")
    if fingerprints.get("canonical_group_manifest_sha256") != _sha256_bytes(
        _canonical_bytes(canonical_groups)
    ):
        raise CoverageMappingError("coverage group manifest hash mismatch")
    if fingerprints.get("verilator_group_binding_sha256") != _sha256_bytes(
        _canonical_bytes(group_bindings)
    ):
        raise CoverageMappingError("coverage group binding hash mismatch")
    if fingerprints.get("ast_declaration_manifest_sha256") != _sha256_bytes(
        _canonical_bytes(declarations)
    ):
        raise CoverageMappingError("coverage AST declaration hash mismatch")
    if fingerprints.get("ast_link_manifest_sha256") != _sha256_bytes(
        _canonical_bytes(bindings)
    ):
        raise CoverageMappingError("coverage AST link hash mismatch")
    regions = lowering.get("regions")
    if not isinstance(regions, list):
        raise CoverageMappingError("coverage lowering regions are malformed")
    if metrics.get("update_region_count") != len(regions):
        raise CoverageMappingError("coverage update region count mismatch")
    if fingerprints.get("lowering_region_manifest_sha256") != _sha256_bytes(
        _canonical_bytes(regions)
    ):
        raise CoverageMappingError("coverage lowering region hash mismatch")
    updated: set[int] = set()
    insertion_call_count = 0
    update_site_count = 0
    for row in regions:
        if not isinstance(row, Mapping):
            raise CoverageMappingError("coverage lowering region is malformed")
        base = row.get("raw_base_word")
        width = row.get("width_bits")
        if (
            not isinstance(base, int)
            or not isinstance(width, int)
            or width <= 0
            or base < 0
            or base + 2 * width > word_count
            or not isinstance(row.get("insertion_call_count"), int)
            or row.get("insertion_call_count") <= 0
            or not isinstance(row.get("update_site_count"), int)
            or row.get("update_site_count") <= 0
        ):
            raise CoverageMappingError("coverage lowering region is invalid")
        insertion_call_count += int(row["insertion_call_count"])
        update_site_count += int(row["update_site_count"])
        updated.update(range(base, base + 2 * width))
    if updated != set(range(word_count)) or metrics.get(
        "updated_raw_word_count"
    ) != len(updated):
        raise CoverageMappingError("coverage update closure is incomplete")
    if metrics.get("insertion_call_count") != insertion_call_count:
        raise CoverageMappingError("coverage insertion call count mismatch")
    if metrics.get("update_site_count") != update_site_count:
        raise CoverageMappingError("coverage update site count mismatch")
    if status == "verified" and issues:
        raise CoverageMappingError("verified coverage mapping has issues")
    expected_region = region.get("expected")
    expected_metrics = mapping.get("expected_metrics")
    expected_fingerprints = mapping.get("expected_fingerprints")
    if status == "verified" and not all(
        isinstance(value, Mapping)
        for value in (expected_region, expected_metrics, expected_fingerprints)
    ):
        raise CoverageMappingError("verified coverage mapping has no oracle evidence")
    if status == "resolved" and (
        issues
        or expected_region is not None
        or expected_metrics is not None
        or expected_fingerprints is not None
    ):
        raise CoverageMappingError("resolved coverage mapping has oracle evidence")
    if status == "mismatch" and (
        not issues
        or not all(
            isinstance(value, Mapping)
            for value in (expected_region, expected_metrics, expected_fingerprints)
        )
    ):
        raise CoverageMappingError(
            "mismatched coverage mapping has incomplete mismatch evidence"
        )
