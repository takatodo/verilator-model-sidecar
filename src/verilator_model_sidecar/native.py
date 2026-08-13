"""Verify adapter signals from Verilator's native experimental manifest."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


NATIVE_MANIFEST_SCHEMA_VERSION = 1
NATIVE_MANIFEST_SURFACE = "verilator_model_manifest_experimental"
NATIVE_VERIFICATION_SCHEMA_VERSION = 1
NATIVE_VERIFICATION_SURFACE = "verilator_native_adapter_verification"
NATIVE_CHECKPOINT_SCHEMA_VERSION = 1
NATIVE_CHECKPOINT_SURFACE = "verilator_native_checkpoint_projection"

_CHECKPOINT_INCLUDED_REASON = "serialized_field"
_CHECKPOINT_EXCLUDED_REASONS = {
    "systemc_top_io",
    "parameter",
    "static_const",
    "nba_commit_queue",
    "mtask_state",
}


class NativeManifestError(ValueError):
    """Raised when a native manifest cannot prove an adapter binding."""


def _records(value: object, name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise NativeManifestError(f"{name} must be an array of objects")
    return list(value)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise NativeManifestError(f"{name} must be a non-empty string")
    return value


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _binding(row: Mapping[str, Any], name: str) -> tuple[str, str]:
    binding = row.get("generated_binding")
    if not isinstance(binding, Mapping):
        raise NativeManifestError(f"{name}.generated_binding must be an object")
    return (
        _string(binding.get("container"), f"{name}.generated_binding.container"),
        _string(binding.get("member"), f"{name}.generated_binding.member"),
    )


def _unique_strings(
    records: list[Mapping[str, Any]], key: str, name: str
) -> list[str]:
    values = [_string(row.get(key), f"{name}.{key}") for row in records]
    if len(values) != len(set(values)):
        raise NativeManifestError(f"{name} {key} values must be unique")
    return values


def validate_native_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate only the native fields required by adapter verification."""

    if manifest.get("schema_version") != NATIVE_MANIFEST_SCHEMA_VERSION:
        raise NativeManifestError("unsupported native manifest schema_version")
    if manifest.get("surface") != NATIVE_MANIFEST_SURFACE:
        raise NativeManifestError("unexpected native manifest surface")
    model = manifest.get("model")
    if not isinstance(model, Mapping):
        raise NativeManifestError("native manifest model must be an object")
    _string(model.get("top"), "native manifest model.top")
    prefix = _string(model.get("prefix"), "native manifest model.prefix")
    fields = _records(manifest.get("fields"), "native manifest fields")
    instances = _records(manifest.get("instances"), "native manifest instances")
    if manifest.get("field_count") != len(fields):
        raise NativeManifestError("native manifest field_count does not match fields")
    if manifest.get("instance_count") != len(instances):
        raise NativeManifestError("native manifest instance_count does not match instances")
    _unique_strings(fields, "field_id", "native manifest fields")
    instance_ids = _unique_strings(
        instances, "instance_id", "native manifest instances"
    )
    instance_id_set = set(instance_ids)
    field_bindings: set[tuple[str, str]] = set()
    for index, field in enumerate(fields):
        binding = _binding(field, f"native manifest fields[{index}]")
        if binding in field_bindings:
            raise NativeManifestError("native manifest field bindings must be unique")
        field_bindings.add(binding)
        if field.get("origin") not in {"rtl", "compiler_generated"}:
            raise NativeManifestError("native manifest field origin is unsupported")
        width_bits = field.get("width_bits")
        if not isinstance(width_bits, int) or width_bits < 0:
            raise NativeManifestError("native manifest field width_bits must be non-negative")
        if field.get("origin") == "rtl" and width_bits == 0:
            raise NativeManifestError("native RTL field width_bits must be positive")
    instance_bindings: set[tuple[str, str]] = set()
    semantic_paths: set[str] = set()
    top_instance_count = 0
    for index, instance in enumerate(instances):
        name = f"native manifest instances[{index}]"
        binding = _binding(instance, name)
        if binding in instance_bindings:
            raise NativeManifestError("native manifest instance bindings must be unique")
        instance_bindings.add(binding)
        if binding[0] != f"{prefix}__Syms":
            raise NativeManifestError("native instance binding is outside the symbol table")
        path = _string(instance.get("semantic_path"), f"{name}.semantic_path")
        if path in semantic_paths:
            raise NativeManifestError("native instance semantic paths must be unique")
        semantic_paths.add(path)
        parent = instance.get("parent_instance_id")
        if not isinstance(parent, str) or (parent and parent not in instance_id_set):
            raise NativeManifestError("native instance parent is unresolved")
        is_top = instance.get("is_top")
        if not isinstance(is_top, bool):
            raise NativeManifestError("native instance is_top must be boolean")
        if is_top:
            top_instance_count += 1
            if parent:
                raise NativeManifestError("native top instance must not have a parent")
        module_binding = instance.get("module_binding")
        if not isinstance(module_binding, Mapping):
            raise NativeManifestError(f"{name}.module_binding must be an object")
        _string(module_binding.get("container"), f"{name}.module_binding.container")
    if top_instance_count != 1:
        raise NativeManifestError("native manifest must contain exactly one top instance")
    limitations = manifest.get("limitations")
    if (
        not isinstance(limitations, Mapping)
        or limitations.get("generated_storage_instances") != "provided"
    ):
        raise NativeManifestError("native manifest storage instances are not provided")
    if limitations.get("semantic_instance_topology") != "not_provided":
        raise NativeManifestError("native manifest overclaims semantic instance topology")


def validate_native_checkpoint_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate compiler-owned ``--savable`` field membership only."""

    validate_native_manifest(manifest)
    fields = _records(manifest.get("fields"), "native manifest fields")
    projection = manifest.get("checkpoint_projection")
    if not isinstance(projection, Mapping):
        raise NativeManifestError("native checkpoint projection must be an object")
    if projection.get("status") != "field_membership_only":
        raise NativeManifestError(
            "native checkpoint projection is not field-membership-only"
        )
    if projection.get("authority") != "verilator_savable_field_selection":
        raise NativeManifestError(
            "native checkpoint projection authority is unsupported"
        )
    if (
        projection.get("runtime_state") != "not_provided"
        or projection.get("packing") != "not_provided"
    ):
        raise NativeManifestError(
            "native checkpoint projection overclaims runtime or packing"
        )
    included = 0
    excluded = 0
    for index, field in enumerate(fields):
        membership = field.get("checkpoint_membership")
        if not isinstance(membership, Mapping):
            raise NativeManifestError(
                f"native manifest fields[{index}] has no checkpoint membership"
            )
        if membership.get("authority") != "verilator_savable_field_selection":
            raise NativeManifestError(
                "native checkpoint field authority is unsupported"
            )
        status = membership.get("status")
        reason = membership.get("reason")
        if status == "included" and reason == _CHECKPOINT_INCLUDED_REASON:
            included += 1
        elif status == "excluded" and reason in _CHECKPOINT_EXCLUDED_REASONS:
            excluded += 1
        else:
            raise NativeManifestError(
                "native checkpoint field membership is unsupported"
            )
    if projection.get("included_definition_field_count") != included:
        raise NativeManifestError(
            "native checkpoint included field count is inconsistent"
        )
    if projection.get("excluded_definition_field_count") != excluded:
        raise NativeManifestError(
            "native checkpoint excluded field count is inconsistent"
        )
    if included + excluded != len(fields):
        raise NativeManifestError("native checkpoint membership is incomplete")
    limitations = manifest.get("limitations")
    assert isinstance(limitations, Mapping)
    if limitations.get("checkpoint_field_membership") != "provided":
        raise NativeManifestError("native checkpoint field membership is not provided")
    if limitations.get("pointer_free_checkpoint") != "not_provided":
        raise NativeManifestError("native checkpoint overclaims pointer-free packing")


def _candidate_index(manifest: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    fields = _records(manifest["fields"], "native manifest fields")
    instances = _records(manifest["instances"], "native manifest instances")
    rtl_fields = [field for field in fields if field.get("origin") == "rtl"]
    top_instance = next(instance for instance in instances if instance.get("is_top"))
    top_module_binding = top_instance.get("module_binding")
    assert isinstance(top_module_binding, Mapping)
    top_container = _string(
        top_module_binding.get("container"), "native top instance module container"
    )
    fields_by_container: dict[str, list[Mapping[str, Any]]] = {}
    candidates: dict[str, list[dict[str, Any]]] = {}
    for field in rtl_fields:
        container, member = _binding(field, "native manifest field")
        fields_by_container.setdefault(container, []).append(field)
        if container == top_container:
            candidates.setdefault(f"{container}.{member}", []).append(
                {
                    "access_kind": "direct_field",
                    "canonical_name": _string(
                        field.get("semantic_path"), "native field semantic_path"
                    ),
                    "field": field,
                    "instance": top_instance,
                }
            )
    for instance in instances:
        syms_container, instance_member = _binding(
            instance, "native manifest instance"
        )
        module_binding = instance["module_binding"]
        assert isinstance(module_binding, Mapping)
        module_container = _string(
            module_binding.get("container"), "native instance module container"
        )
        for field in fields_by_container.get(module_container, []):
            _, field_member = _binding(field, "native manifest field")
            rtl_name = _string(field.get("rtl_name"), "native field rtl_name")
            binding = f"{syms_container}.{instance_member}.{field_member}"
            candidates.setdefault(binding, []).append(
                {
                    "access_kind": "instance_field",
                    "canonical_name": f"{instance['semantic_path']}.{rtl_name}",
                    "field": field,
                    "instance": instance,
                }
            )
    return candidates


def verify_native_adapter(
    manifest: Mapping[str, Any], adapter: Mapping[str, Any]
) -> dict[str, Any]:
    """Resolve every adapter binding without parsing generated C++ or JSON AST."""

    validate_native_manifest(manifest)
    source_model = adapter.get("source_model")
    if not isinstance(source_model, Mapping):
        raise NativeManifestError("adapter source_model must be an object")
    model = manifest["model"]
    assert isinstance(model, Mapping)
    model_prefix = _string(source_model.get("prefix"), "adapter source_model.prefix")
    if model_prefix != model.get("prefix"):
        raise NativeManifestError("adapter model prefix does not match native manifest")
    requested = adapter.get("signals")
    if not isinstance(requested, Mapping) or not requested:
        raise NativeManifestError("adapter signals must be a non-empty object")
    candidates = _candidate_index(manifest)
    results: list[dict[str, Any]] = []
    matched_count = 0
    for name, contract in sorted(requested.items()):
        if not isinstance(contract, Mapping):
            raise NativeManifestError(f"adapter signal {name!r} must be an object")
        binding = _string(contract.get("binding"), f"adapter signal {name!r} binding")
        matches = candidates.get(binding, [])
        expected_width = contract.get("width_bits")
        width_match = (
            len(matches) == 1
            and isinstance(expected_width, int)
            and matches[0]["field"].get("width_bits") == expected_width
        )
        status = "matched" if len(matches) == 1 and width_match else "mismatch"
        if status == "matched":
            matched_count += 1
        match = matches[0] if len(matches) == 1 else None
        field = match["field"] if match is not None else None
        instance = match["instance"] if match is not None else None
        field_container = field_member = None
        state_container = instance_member = None
        if field is not None and instance is not None:
            field_container, field_member = _binding(field, "matched native field")
            state_container, instance_member = _binding(
                instance, "matched native instance"
            )
        results.append(
            {
                "name": str(name),
                "status": status,
                "binding": binding,
                "binding_kind": match["access_kind"] if match is not None else "unresolved",
                "canonical_name": match["canonical_name"] if match is not None else None,
                "match_count": len(matches),
                "width_match": width_match,
                "contract": {
                    "width_bits": expected_width,
                    "direction": contract.get("direction"),
                    "role": contract.get("role"),
                },
                "native_entity": (
                    {
                        "field_id": field["field_id"],
                        "instance_id": instance.get("instance_id"),
                        "canonical_name": match["canonical_name"],
                        "width_bits": field["width_bits"],
                        "rtl_direction": str(field.get("direction", "NONE")).lower(),
                        "source": field.get("source"),
                        "generated_storage": {
                            "state_container": state_container,
                            "instance_member": instance_member,
                            "field_container": field_container,
                            "field_member": field_member,
                        },
                    }
                    if field is not None
                    else None
                ),
                "direction_authority": "adapter_contract_not_derived_from_native_manifest",
            }
        )
    signal_count = len(results)
    return {
        "schema_version": NATIVE_VERIFICATION_SCHEMA_VERSION,
        "surface": NATIVE_VERIFICATION_SURFACE,
        "status": "matched" if matched_count == signal_count else "mismatch",
        "producer": manifest.get("producer"),
        "target": adapter.get("target"),
        "adapter_id": adapter.get("adapter_id"),
        "model_prefix": model_prefix,
        "top_module": model.get("top"),
        "signal_count": signal_count,
        "matched_count": matched_count,
        "unmatched_count": signal_count - matched_count,
        "signals": results,
        "non_claims": [
            "not_a_byte_offset_verification",
            "not_a_checkpoint_projection",
            "not_a_coverage_mapping_verification",
        ],
    }


def project_native_checkpoint(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Expand definition-level savable membership onto stored model instances."""

    validate_native_checkpoint_manifest(manifest)
    fields = _records(manifest["fields"], "native manifest fields")
    instances = _records(manifest["instances"], "native manifest instances")
    instances_by_container: dict[str, list[Mapping[str, Any]]] = {}
    for instance in instances:
        module_binding = instance.get("module_binding")
        assert isinstance(module_binding, Mapping)
        container = _string(
            module_binding.get("container"), "native instance module container"
        )
        instances_by_container.setdefault(container, []).append(instance)
    for rows in instances_by_container.values():
        rows.sort(key=lambda row: str(row["instance_id"]))

    occurrences: list[dict[str, Any]] = []
    uninstantiated: list[str] = []
    unsupported: list[dict[str, str]] = []
    included_definition_count = 0
    excluded_definition_count = 0
    for field in sorted(fields, key=lambda row: str(row["field_id"])):
        membership = field["checkpoint_membership"]
        assert isinstance(membership, Mapping)
        if membership["status"] == "excluded":
            excluded_definition_count += 1
            continue
        included_definition_count += 1
        field_container, field_member = _binding(field, "native checkpoint field")
        generated_binding = field["generated_binding"]
        assert isinstance(generated_binding, Mapping)
        storage = generated_binding.get("storage")
        if storage != "instance_member":
            unsupported.append(
                {
                    "field_id": _string(field.get("field_id"), "checkpoint field ID"),
                    "storage": str(storage),
                }
            )
            continue
        stored_instances = instances_by_container.get(field_container, [])
        if not stored_instances:
            uninstantiated.append(
                _string(field.get("field_id"), "uninstantiated checkpoint field ID")
            )
            continue
        for instance in stored_instances:
            state_container, instance_member = _binding(
                instance, "native checkpoint instance"
            )
            field_id = _string(field.get("field_id"), "checkpoint field ID")
            instance_id = _string(
                instance.get("instance_id"), "checkpoint instance ID"
            )
            canonical_name: str | None = None
            if field.get("origin") == "rtl":
                if instance.get("is_top") is True:
                    canonical_name = _string(
                        field.get("semantic_path"), "checkpoint semantic path"
                    )
                else:
                    instance_path = _string(
                        instance.get("semantic_path"), "checkpoint instance path"
                    )
                    rtl_name = _string(field.get("rtl_name"), "checkpoint RTL name")
                    canonical_name = f"{instance_path}.{rtl_name}"
            occurrences.append(
                {
                    "occurrence_id": f"{instance_id}|{field_id}",
                    "field_id": field_id,
                    "instance_id": instance_id,
                    "origin": field.get("origin"),
                    "canonical_name": canonical_name,
                    "width_bits": field.get("width_bits"),
                    "generated_storage": {
                        "state_container": state_container,
                        "instance_member": instance_member,
                        "field_container": field_container,
                        "field_member": field_member,
                    },
                }
            )
    occurrences.sort(key=lambda row: str(row["occurrence_id"]))
    unsupported.sort(key=lambda row: row["field_id"])
    status = "projected" if not unsupported else "incomplete"
    report: dict[str, Any] = {
        "schema_version": NATIVE_CHECKPOINT_SCHEMA_VERSION,
        "surface": NATIVE_CHECKPOINT_SURFACE,
        "status": status,
        "producer": manifest.get("producer"),
        "model": manifest.get("model"),
        "authority": "verilator_savable_field_selection",
        "definition_field_count": len(fields),
        "included_definition_field_count": included_definition_count,
        "excluded_definition_field_count": excluded_definition_count,
        "stored_instance_count": len(instances),
        "stored_field_occurrence_count": len(occurrences),
        "stored_field_occurrences": occurrences,
        "uninstantiated_included_definition_field_count": len(uninstantiated),
        "uninstantiated_included_definition_fields": sorted(uninstantiated),
        "unsupported_included_definition_field_count": len(unsupported),
        "unsupported_included_definition_fields": unsupported,
        "runtime_state": "not_provided",
        "packing": "not_provided",
        "non_claims": [
            "not_a_serialized_byte_order",
            "not_a_runtime_context_projection",
            "not_a_pointer_free_checkpoint",
            "not_coverage_or_timing_checkpoint_compatibility",
        ],
    }
    report["projection_fingerprint"] = _fingerprint(report)
    return report


def write_native_verification(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_native_checkpoint(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
