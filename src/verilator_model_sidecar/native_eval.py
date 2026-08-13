"""Validate and project Verilator-owned evaluation-effect metadata."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from .native import NativeManifestError, validate_native_manifest


NATIVE_EVAL_PROJECTION_SCHEMA_VERSION = 1
NATIVE_EVAL_PROJECTION_SURFACE = "verilator_native_eval_projection"

_CLASSIFICATIONS = {
    "proven_device_clean",
    "unknown",
    "host_dependent",
}
_CLASSIFICATION_PRECEDENCE = [
    "host_dependent",
    "unknown",
    "proven_device_clean",
]
_CLASSIFICATION_RANK = {
    "proven_device_clean": 0,
    "unknown": 1,
    "host_dependent": 2,
}


def _canonical_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _records(value: object, name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(row, Mapping) for row in value
    ):
        raise NativeManifestError(f"{name} must be an array of objects")
    return list(value)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise NativeManifestError(f"{name} must be a non-empty string")
    return value


def _count(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        requirement = "positive" if positive else "non-negative"
        raise NativeManifestError(f"{name} must be a {requirement} integer")
    return value


def _unique_sorted_records(
    value: object,
    *,
    key: str,
    name: str,
) -> list[Mapping[str, Any]]:
    records = _records(value, name)
    identities = [_string(row.get(key), f"{name}.{key}") for row in records]
    if identities != sorted(set(identities)):
        raise NativeManifestError(f"{name} must have uniquely sorted {key} values")
    return records


def _direct_classification(function: Mapping[str, Any]) -> str:
    effects = function["direct_effects"]
    assert isinstance(effects, Mapping)
    if effects["host_dependencies"]:
        return "host_dependent"
    if effects["unknown_effects"]:
        return "unknown"
    return "proven_device_clean"


def _classification_reason(
    function: Mapping[str, Any],
    classifications: Mapping[str, str],
) -> str:
    direct = function["direct_classification"]
    final = classifications[function["function_id"]]
    if final == "host_dependent":
        return (
            "direct_host_dependency"
            if direct == "host_dependent"
            else "transitive_host_dependency"
        )
    if final == "unknown":
        return (
            "direct_unknown_effect"
            if direct == "unknown"
            else "transitive_unknown_effect"
        )
    return "no_host_or_unknown_effect_in_final_ast_closure"


def _validate_direct_effects(function: Mapping[str, Any], name: str) -> None:
    effects = function.get("direct_effects")
    if not isinstance(effects, Mapping):
        raise NativeManifestError(f"{name}.direct_effects must be an object")
    _count(
        effects.get("coverage_update_site_count"),
        f"{name}.direct_effects.coverage_update_site_count",
    )
    for array_name, identity in (
        ("host_dependencies", "category"),
        ("unknown_effects", "kind"),
    ):
        rows = _unique_sorted_records(
            effects.get(array_name),
            key=identity,
            name=f"{name}.direct_effects.{array_name}",
        )
        for index, row in enumerate(rows):
            _count(
                row.get("site_count"),
                f"{name}.direct_effects.{array_name}[{index}].site_count",
                positive=True,
            )


def _validate_function_shape(
    function: Mapping[str, Any],
    *,
    field_ids: set[str],
    index: int,
) -> None:
    name = f"native eval functions[{index}]"
    function_id = _string(function.get("function_id"), f"{name}.function_id")
    if not function_id.startswith("eval-function:v1:"):
        raise NativeManifestError("native eval function ID scheme is unsupported")
    binding = function.get("generated_binding")
    if not isinstance(binding, Mapping):
        raise NativeManifestError(f"{name}.generated_binding must be an object")
    _string(binding.get("container"), f"{name}.generated_binding.container")
    _string(binding.get("name"), f"{name}.generated_binding.name")
    if binding.get("kind") not in {"method", "loose_function"}:
        raise NativeManifestError(f"{name}.generated_binding.kind is unsupported")
    for boolean in ("is_eval_entry", "entry_point", "slow"):
        if not isinstance(function.get(boolean), bool):
            raise NativeManifestError(f"{name}.{boolean} must be boolean")

    accesses = _unique_sorted_records(
        function.get("direct_state_accesses"),
        key="field_id",
        name=f"{name}.direct_state_accesses",
    )
    for access_index, access in enumerate(accesses):
        field_id = access["field_id"]
        if field_id not in field_ids:
            raise NativeManifestError(
                "native eval state access references an unknown field"
            )
        reads = _count(
            access.get("read_site_count"),
            f"{name}.direct_state_accesses[{access_index}].read_site_count",
        )
        writes = _count(
            access.get("write_site_count"),
            f"{name}.direct_state_accesses[{access_index}].write_site_count",
        )
        if reads + writes == 0:
            raise NativeManifestError(
                "native eval state access has no read or write site"
            )

    calls = _unique_sorted_records(
        function.get("direct_calls"),
        key="callee_function_id",
        name=f"{name}.direct_calls",
    )
    for call_index, call in enumerate(calls):
        _count(
            call.get("site_count"),
            f"{name}.direct_calls[{call_index}].site_count",
            positive=True,
        )
    _validate_direct_effects(function, name)
    direct = function.get("direct_classification")
    final = function.get("classification")
    if direct not in _CLASSIFICATIONS or final not in _CLASSIFICATIONS:
        raise NativeManifestError("native eval classification is unsupported")
    if direct != _direct_classification(function):
        raise NativeManifestError("native eval direct classification is inconsistent")


def _fixed_point(functions: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    by_id = {function["function_id"]: function for function in functions}
    classifications = {
        function_id: function["direct_classification"]
        for function_id, function in by_id.items()
    }
    changed = True
    while changed:
        changed = False
        for function_id in sorted(by_id):
            function = by_id[function_id]
            rank = _CLASSIFICATION_RANK[function["direct_classification"]]
            for call in function["direct_calls"]:
                rank = max(
                    rank,
                    _CLASSIFICATION_RANK[classifications[call["callee_function_id"]]],
                )
            classification = next(
                name
                for name, candidate_rank in _CLASSIFICATION_RANK.items()
                if candidate_rank == rank
            )
            if classification != classifications[function_id]:
                classifications[function_id] = classification
                changed = True
    return classifications


def _inventory_metrics(
    functions: Sequence[Mapping[str, Any]], region_count: int
) -> dict[str, int]:
    classifications = Counter(function["classification"] for function in functions)
    direct_classifications = Counter(
        function["direct_classification"] for function in functions
    )
    return {
        "function_count": len(functions),
        "region_count": region_count,
        "direct_call_edge_count": sum(
            len(function["direct_calls"]) for function in functions
        ),
        "direct_call_site_count": sum(
            call["site_count"]
            for function in functions
            for call in function["direct_calls"]
        ),
        "state_access_binding_count": sum(
            len(function["direct_state_accesses"]) for function in functions
        ),
        "state_read_site_count": sum(
            access["read_site_count"]
            for function in functions
            for access in function["direct_state_accesses"]
        ),
        "state_write_site_count": sum(
            access["write_site_count"]
            for function in functions
            for access in function["direct_state_accesses"]
        ),
        "coverage_update_site_count": sum(
            function["direct_effects"]["coverage_update_site_count"]
            for function in functions
        ),
        "host_dependency_site_count": sum(
            dependency["site_count"]
            for function in functions
            for dependency in function["direct_effects"]["host_dependencies"]
        ),
        "unknown_effect_site_count": sum(
            effect["site_count"]
            for function in functions
            for effect in function["direct_effects"]["unknown_effects"]
        ),
        "direct_proven_device_clean_function_count": direct_classifications[
            "proven_device_clean"
        ],
        "direct_unknown_function_count": direct_classifications["unknown"],
        "direct_host_dependent_function_count": direct_classifications[
            "host_dependent"
        ],
        "proven_device_clean_function_count": classifications["proven_device_clean"],
        "unknown_function_count": classifications["unknown"],
        "host_dependent_function_count": classifications["host_dependent"],
    }


def _reachable_function_ids(
    functions: Mapping[str, Mapping[str, Any]], entry: str
) -> set[str]:
    reachable: set[str] = set()
    pending = deque([entry])
    while pending:
        function_id = pending.popleft()
        if function_id in reachable:
            continue
        reachable.add(function_id)
        pending.extend(
            call["callee_function_id"]
            for call in functions[function_id]["direct_calls"]
        )
    return reachable


def extract_native_eval_closure(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate native eval facts and return the compiler-owned main-eval closure."""

    validate_native_manifest(manifest)
    eval_regions = manifest.get("eval_regions")
    if not isinstance(eval_regions, Mapping):
        raise NativeManifestError("native eval_regions must be an object")
    if eval_regions.get("status") != "provided":
        raise NativeManifestError("native eval regions are not provided")
    if eval_regions.get("authority") != "verilator_final_ast":
        raise NativeManifestError("native eval authority is unsupported")
    if eval_regions.get("function_id_scheme") != "sha256_length_prefixed_utf8_v1":
        raise NativeManifestError("native eval function ID scheme is unsupported")
    if eval_regions.get("region_id_scheme") != "sha256_length_prefixed_utf8_v1":
        raise NativeManifestError("native eval region ID scheme is unsupported")
    policy = eval_regions.get("classification_policy")
    if not isinstance(policy, Mapping):
        raise NativeManifestError("native eval classification policy must be an object")
    if policy.get("policy") != "conservative_final_ast_effects_v1":
        raise NativeManifestError("native eval classification policy is unsupported")
    if policy.get("precedence") != _CLASSIFICATION_PRECEDENCE:
        raise NativeManifestError(
            "native eval classification precedence is unsupported"
        )
    if policy.get("propagation") != "transitive_call_closure_fixed_point":
        raise NativeManifestError("native eval propagation policy is unsupported")

    fields = _records(manifest.get("fields"), "native manifest fields")
    field_ids = {_string(field.get("field_id"), "native field ID") for field in fields}
    functions = _unique_sorted_records(
        eval_regions.get("functions"),
        key="function_id",
        name="native eval functions",
    )
    if not functions:
        raise NativeManifestError("native eval functions must be non-empty")
    for index, function in enumerate(functions):
        _validate_function_shape(function, field_ids=field_ids, index=index)
    by_id = {function["function_id"]: function for function in functions}
    for function in functions:
        for call in function["direct_calls"]:
            if call["callee_function_id"] not in by_id:
                raise NativeManifestError(
                    "native eval call references an unknown function"
                )

    classifications = _fixed_point(functions)
    for function in functions:
        function_id = function["function_id"]
        if function["classification"] != classifications[function_id]:
            raise NativeManifestError(
                "native eval fixed-point classification is inconsistent"
            )
        if function.get("reason") != _classification_reason(function, classifications):
            raise NativeManifestError(
                "native eval classification reason is inconsistent"
            )

    regions = _records(eval_regions.get("regions"), "native eval regions")
    if len(regions) != 1:
        raise NativeManifestError("native main eval region must be unique")
    region = regions[0]
    entry = _string(region.get("entry_function_id"), "native main eval entry")
    if entry not in by_id:
        raise NativeManifestError("native main eval entry is absent from functions")
    eval_entries = [
        function["function_id"] for function in functions if function["is_eval_entry"]
    ]
    if eval_entries != [entry]:
        raise NativeManifestError("native compiler-owned eval entry is inconsistent")
    if not _string(region.get("region_id"), "native main eval region ID").startswith(
        "eval-region:v1:"
    ):
        raise NativeManifestError("native eval region ID scheme is unsupported")
    if region.get("kind") != "main_eval":
        raise NativeManifestError("native eval region kind is unsupported")
    if region.get("classification") != classifications[entry]:
        raise NativeManifestError("native main eval classification is inconsistent")
    if region.get("reason") != _classification_reason(by_id[entry], classifications):
        raise NativeManifestError("native main eval reason is inconsistent")
    if region.get("dependency_graph") != "function_direct_calls":
        raise NativeManifestError("native eval dependency graph is unsupported")
    if (
        region.get("schedule_semantics") != "not_provided"
        or region.get("convergence_semantics") != "not_provided"
    ):
        raise NativeManifestError(
            "native eval region overclaims schedule or convergence"
        )

    metrics = eval_regions.get("metrics")
    expected_metrics = _inventory_metrics(functions, len(regions))
    if metrics != expected_metrics:
        raise NativeManifestError("native eval inventory metrics are inconsistent")
    limitations = manifest.get("limitations")
    if (
        not isinstance(limitations, Mapping)
        or limitations.get("eval_regions") != "provided"
    ):
        raise NativeManifestError(
            "native manifest limitations do not provide eval regions"
        )

    reachable_ids = _reachable_function_ids(by_id, entry)
    closure_functions = [
        dict(by_id[function_id]) for function_id in sorted(reachable_ids)
    ]
    projection: dict[str, Any] = {
        "schema_version": NATIVE_EVAL_PROJECTION_SCHEMA_VERSION,
        "surface": NATIVE_EVAL_PROJECTION_SURFACE,
        "status": "projected",
        "producer": manifest.get("producer"),
        "model": manifest.get("model"),
        "authority": eval_regions["authority"],
        "classification_policy": dict(policy),
        "entry_function_id": entry,
        "classification": classifications[entry],
        "reason": region["reason"],
        "reachable_function_count": len(closure_functions),
        "functions": closure_functions,
        "schedule_semantics": "not_provided",
        "convergence_semantics": "not_provided",
        "non_claims": list(eval_regions.get("non_claims", [])),
    }
    projection["projection_fingerprint"] = _canonical_fingerprint(projection)
    return projection
