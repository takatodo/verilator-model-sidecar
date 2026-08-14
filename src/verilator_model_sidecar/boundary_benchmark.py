"""Fail-closed adjudication for externally executed RTL boundary benchmarks."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Mapping, Sequence

from .sweep_boundary import (
    RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
    RTL_BOUNDARY_SCHEMA_VERSION,
    SweepBoundaryError,
    analyze_boundary_ground_truth,
    analyze_boundary_policy_trial,
    enumerate_sweep_space,
    select_boundary_points,
)


RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION = 1
RTL_BOUNDARY_EXPERIMENT_CONTRACT_SURFACE = "rtl_boundary_experiment_contract"
RTL_BOUNDARY_EVIDENCE_BUNDLE_SURFACE = "rtl_boundary_evidence_bundle"
RTL_BOUNDARY_SEMANTIC_MANIFEST_SURFACE = "rtl_boundary_semantic_manifest"
RTL_BOUNDARY_ADJUDICATION_SURFACE = "rtl_boundary_adjudication"


class BoundaryBenchmarkError(ValueError):
    """A fail-closed boundary benchmark Contract violation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise BoundaryBenchmarkError(
            "canonical_json_invalid", "value is not canonical JSON"
        ) from error


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_equal(left: Any, right: Any) -> bool:
    return _canonical_bytes(left) == _canonical_bytes(right)


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_object(value: Any, code: str, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BoundaryBenchmarkError(code, f"{label} must be an object")
    return value


def _reject_unknown(
    value: Mapping[str, Any], allowed: set[str], code: str, label: str
) -> None:
    unknown = sorted(set(value) - allowed, key=repr)
    if unknown:
        raise BoundaryBenchmarkError(code, f"{label} has unknown fields: {unknown}")


def _nonempty_string(value: Any, code: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BoundaryBenchmarkError(code, f"{label} must be a nonempty string")
    return value


def _positive_integer(value: Any, code: str, label: str) -> int:
    if not _integer(value) or value <= 0:
        raise BoundaryBenchmarkError(code, f"{label} must be a positive integer")
    return value


def _nonnegative_integer(value: Any, code: str, label: str) -> int:
    if not _integer(value) or value < 0:
        raise BoundaryBenchmarkError(code, f"{label} must be a nonnegative integer")
    return value


def _hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _string_set(value: Any, code: str, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise BoundaryBenchmarkError(code, f"{label} must be a nonempty list")
    if not all(isinstance(item, str) and item for item in value):
        raise BoundaryBenchmarkError(code, f"{label} must contain nonempty strings")
    if len(set(value)) != len(value):
        raise BoundaryBenchmarkError(code, f"{label} must not contain duplicates")
    return list(value)


def _validate_target(raw_target: Any) -> dict[str, Any]:
    target = _require_object(raw_target, "target_invalid", "target")
    _reject_unknown(
        target,
        {
            "target_id",
            "issue",
            "ip",
            "checkpoint_identity",
            "oracle_identity",
            "revisions",
            "semantic_observables",
            "oracle_field",
            "semantic_manifest_sha256",
        },
        "target_unknown_field",
        "target",
    )
    revisions = _require_object(
        target.get("revisions"), "target_revisions_invalid", "target revisions"
    )
    manifests = _require_object(
        target.get("semantic_manifest_sha256"),
        "target_manifests_invalid",
        "target semantic manifests",
    )
    if set(revisions) != {"bad", "fixed"}:
        raise BoundaryBenchmarkError(
            "target_revisions_invalid", "target revisions must contain exactly bad and fixed"
        )
    if set(manifests) != {"bad", "fixed"}:
        raise BoundaryBenchmarkError(
            "target_manifests_invalid",
            "target semantic manifests must contain exactly bad and fixed",
        )
    for label in ("bad", "fixed"):
        if not _hex(revisions[label], 40):
            raise BoundaryBenchmarkError(
                "target_revision_sha_invalid",
                f"target {label} revision must be lowercase 40-hex",
            )
        if not _hex(manifests[label], 64):
            raise BoundaryBenchmarkError(
                "target_manifest_sha_invalid",
                f"target {label} semantic manifest must be lowercase 64-hex",
            )
    if revisions["bad"] == revisions["fixed"]:
        raise BoundaryBenchmarkError(
            "target_revisions_not_distinct", "bad and fixed revisions must differ"
        )
    if manifests["bad"] == manifests["fixed"]:
        raise BoundaryBenchmarkError(
            "target_manifests_not_distinct",
            "bad and fixed semantic manifests must differ",
        )
    semantic_observables = _string_set(
        target.get("semantic_observables"),
        "target_semantic_observables_invalid",
        "target semantic_observables",
    )
    oracle_field = _nonempty_string(
        target.get("oracle_field"), "target_oracle_field_invalid", "oracle_field"
    )
    if oracle_field in semantic_observables:
        raise BoundaryBenchmarkError(
            "target_oracle_field_overlaps_observables",
            "oracle_field must be separate from semantic_observables",
        )
    return {
        "target_id": _nonempty_string(
            target.get("target_id"), "target_id_invalid", "target_id"
        ),
        "issue": _nonempty_string(target.get("issue"), "issue_invalid", "issue"),
        "ip": _nonempty_string(target.get("ip"), "ip_invalid", "ip"),
        "checkpoint_identity": _nonempty_string(
            target.get("checkpoint_identity"),
            "checkpoint_identity_invalid",
            "checkpoint_identity",
        ),
        "oracle_identity": _nonempty_string(
            target.get("oracle_identity"),
            "oracle_identity_invalid",
            "oracle_identity",
        ),
        "revisions": dict(revisions),
        "semantic_observables": semantic_observables,
        "oracle_field": oracle_field,
        "semantic_manifest_sha256": dict(manifests),
    }


def _validate_backends(raw_backends: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_backends, list) or len(raw_backends) < 2:
        raise BoundaryBenchmarkError(
            "backends_invalid", "backends must contain CPU and GPU adapters"
        )
    backends: dict[str, dict[str, Any]] = {}
    kinds: set[str] = set()
    for index, raw_backend in enumerate(raw_backends):
        backend = _require_object(
            raw_backend, "backend_invalid", f"backend {index}"
        )
        _reject_unknown(
            backend,
            {"backend_id", "kind", "executor_identity", "resident_width"},
            "backend_unknown_field",
            f"backend {index}",
        )
        backend_id = _nonempty_string(
            backend.get("backend_id"), "backend_id_invalid", f"backend {index} id"
        )
        if backend_id in backends:
            raise BoundaryBenchmarkError(
                "backend_id_duplicate", f"backend id {backend_id!r} is duplicated"
            )
        kind = backend.get("kind")
        if kind not in {"cpu", "gpu"}:
            raise BoundaryBenchmarkError(
                "backend_kind_invalid", f"backend {backend_id!r} kind is invalid"
            )
        kinds.add(kind)
        backends[backend_id] = {
            "backend_id": backend_id,
            "kind": kind,
            "executor_identity": _nonempty_string(
                backend.get("executor_identity"),
                "executor_identity_invalid",
                f"backend {backend_id!r} executor_identity",
            ),
            "resident_width": _positive_integer(
                backend.get("resident_width"),
                "resident_width_invalid",
                f"backend {backend_id!r} resident_width",
            ),
        }
    if kinds != {"cpu", "gpu"}:
        raise BoundaryBenchmarkError(
            "backend_kinds_incomplete", "backends must include CPU and GPU kinds"
        )
    return backends


def _validate_trial_contracts(
    raw_trials: Any,
    sweep_space: Mapping[str, Any],
    backends: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_trials, list) or not raw_trials:
        raise BoundaryBenchmarkError("trial_contracts_invalid", "trials must be nonempty")
    trials: dict[str, dict[str, Any]] = {}
    for index, raw_trial in enumerate(raw_trials):
        trial = _require_object(
            raw_trial, "trial_contract_invalid", f"trial contract {index}"
        )
        _reject_unknown(
            trial,
            {
                "trial_id",
                "backend_id",
                "policy",
                "requested_count",
                "budget_logical_bad_queries",
            },
            "trial_contract_unknown_field",
            f"trial contract {index}",
        )
        trial_id = _nonempty_string(
            trial.get("trial_id"), "trial_id_invalid", f"trial contract {index} id"
        )
        if trial_id in trials:
            raise BoundaryBenchmarkError(
                "trial_id_duplicate", f"trial id {trial_id!r} is duplicated"
            )
        backend_id = trial.get("backend_id")
        if backend_id not in backends:
            raise BoundaryBenchmarkError(
                "trial_backend_unknown", f"trial {trial_id!r} backend is unknown"
            )
        requested = _positive_integer(
            trial.get("requested_count"),
            "trial_requested_count_invalid",
            f"trial {trial_id!r} requested_count",
        )
        budget = _positive_integer(
            trial.get("budget_logical_bad_queries"),
            "trial_budget_invalid",
            f"trial {trial_id!r} budget",
        )
        if requested > budget:
            raise BoundaryBenchmarkError(
                "trial_requested_exceeds_budget",
                f"trial {trial_id!r} requested_count exceeds budget",
            )
        policy = _require_object(
            trial.get("policy"), "trial_policy_invalid", f"trial {trial_id!r} policy"
        )
        try:
            select_boundary_points(sweep_space, policy, [], 1)
        except SweepBoundaryError as error:
            raise BoundaryBenchmarkError(
                "trial_policy_invalid", f"trial {trial_id!r} policy is invalid: {error}"
            ) from error
        trials[trial_id] = {
            "trial_id": trial_id,
            "backend_id": backend_id,
            "policy": dict(policy),
            "requested_count": requested,
            "budget_logical_bad_queries": budget,
        }
    return trials


def _validate_action_domain(
    raw_action_domain: Any,
    raw_digest: Any,
    enumeration: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw_action_domain, list):
        raise BoundaryBenchmarkError(
            "action_domain_invalid", "action_domain must be a list"
        )
    expected_points = list(enumeration["points"])
    if len(raw_action_domain) != len(expected_points):
        raise BoundaryBenchmarkError(
            "action_domain_incomplete",
            "action_domain must contain exactly one row per sweep point",
        )
    actions: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, expected_point in enumerate(expected_points):
        raw_row = _require_object(
            raw_action_domain[index],
            "action_domain_row_invalid",
            f"action_domain row {index}",
        )
        _reject_unknown(
            raw_row,
            {"point_id", "action", "parameters"},
            "action_domain_unknown_field",
            f"action_domain row {index}",
        )
        action = _nonempty_string(
            raw_row.get("action"), "action_domain_action_invalid", f"action_domain row {index} action"
        )
        if action in actions:
            raise BoundaryBenchmarkError(
                "action_domain_action_duplicate",
                f"action_domain action {action!r} is duplicated",
            )
        actions.add(action)
        if raw_row.get("point_id") != expected_point["point_id"]:
            raise BoundaryBenchmarkError(
                "action_domain_point_mismatch",
                f"action_domain row {index} point_id does not match sweep enumeration",
            )
        parameters = _require_object(
            raw_row.get("parameters"),
            "action_domain_parameters_invalid",
            f"action_domain row {index} parameters",
        )
        if not _exact_equal(parameters, expected_point["parameters"]):
            raise BoundaryBenchmarkError(
                "action_domain_parameters_mismatch",
                f"action_domain row {index} parameters do not match sweep enumeration",
            )
        normalized.append(
            {
                "point_id": expected_point["point_id"],
                "action": action,
                "parameters": dict(parameters),
            }
        )
    digest = _sha256(normalized)
    if raw_digest != digest:
        raise BoundaryBenchmarkError(
            "action_domain_hash_mismatch",
            "action_domain_sha256 does not recompute",
        )
    return {"action_domain": normalized, "action_domain_sha256": digest}


def _validate_comparisons(
    raw_comparisons: Any,
    trials: Mapping[str, Mapping[str, Any]],
    backends: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_comparisons, list) or not raw_comparisons:
        raise BoundaryBenchmarkError(
            "comparisons_invalid", "comparisons must be nonempty"
        )
    comparisons: list[dict[str, Any]] = []
    comparison_ids: set[str] = set()
    referenced_trials: set[str] = set()
    kinds: set[str] = set()
    for index, raw_comparison in enumerate(raw_comparisons):
        comparison = _require_object(
            raw_comparison, "comparison_invalid", f"comparison {index}"
        )
        _reject_unknown(
            comparison,
            {"comparison_id", "kind", "trial_ids"},
            "comparison_unknown_field",
            f"comparison {index}",
        )
        comparison_id = _nonempty_string(
            comparison.get("comparison_id"),
            "comparison_id_invalid",
            f"comparison {index} id",
        )
        if comparison_id in comparison_ids:
            raise BoundaryBenchmarkError(
                "comparison_id_duplicate",
                f"comparison id {comparison_id!r} is duplicated",
            )
        comparison_ids.add(comparison_id)
        kind = comparison.get("kind")
        if kind not in {"selector", "backend"}:
            raise BoundaryBenchmarkError(
                "comparison_kind_invalid", f"comparison {comparison_id!r} kind is invalid"
            )
        kinds.add(kind)
        trial_ids = _string_set(
            comparison.get("trial_ids"),
            "comparison_trial_ids_invalid",
            f"comparison {comparison_id!r} trial_ids",
        )
        if any(trial_id not in trials for trial_id in trial_ids):
            raise BoundaryBenchmarkError(
                "comparison_trial_unknown",
                f"comparison {comparison_id!r} references an unknown trial",
            )
        members = [trials[trial_id] for trial_id in trial_ids]
        if len(members) < 2:
            raise BoundaryBenchmarkError(
                "comparison_too_small",
                f"comparison {comparison_id!r} requires at least two trials",
            )
        budgets = {
            (member["requested_count"], member["budget_logical_bad_queries"])
            for member in members
        }
        if len(budgets) != 1:
            raise BoundaryBenchmarkError(
                "comparison_budget_mismatch",
                f"comparison {comparison_id!r} trials do not share a budget",
            )
        if kind == "selector":
            backend_ids = {member["backend_id"] for member in members}
            if len(backend_ids) != 1:
                raise BoundaryBenchmarkError(
                    "selector_comparison_backend_mismatch",
                    f"selector comparison {comparison_id!r} must use one backend",
                )
            backend_id = next(iter(backend_ids))
            if backends[backend_id]["kind"] != "gpu":
                raise BoundaryBenchmarkError(
                    "selector_comparison_not_gpu",
                    f"selector comparison {comparison_id!r} must use a GPU backend",
                )
            if len({_sha256(member["policy"]) for member in members}) != len(members):
                raise BoundaryBenchmarkError(
                    "selector_comparison_policy_duplicate",
                    f"selector comparison {comparison_id!r} must compare distinct policies",
                )
        else:
            if len(members) != 2:
                raise BoundaryBenchmarkError(
                    "backend_comparison_size_invalid",
                    f"backend comparison {comparison_id!r} requires exactly two trials",
                )
            if not _exact_equal(members[0]["policy"], members[1]["policy"]):
                raise BoundaryBenchmarkError(
                    "backend_comparison_policy_mismatch",
                    f"backend comparison {comparison_id!r} must use one policy",
                )
            backend_kinds = {
                backends[member["backend_id"]]["kind"] for member in members
            }
            if backend_kinds != {"cpu", "gpu"}:
                raise BoundaryBenchmarkError(
                    "backend_comparison_kinds_invalid",
                    f"backend comparison {comparison_id!r} requires CPU and GPU",
                )
        referenced_trials.update(trial_ids)
        comparisons.append(
            {"comparison_id": comparison_id, "kind": kind, "trial_ids": trial_ids}
        )
    if kinds != {"selector", "backend"}:
        raise BoundaryBenchmarkError(
            "comparison_kinds_incomplete",
            "comparisons must include selector and backend comparisons",
        )
    if referenced_trials != set(trials):
        raise BoundaryBenchmarkError(
            "comparison_trials_incomplete", "every trial must belong to a comparison"
        )
    return comparisons


def _validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    contract = _require_object(contract, "contract_invalid", "experiment contract")
    _reject_unknown(
        contract,
        {
            "schema_version",
            "surface",
            "experiment_id",
            "target",
            "sweep_space",
            "sweep_space_sha256",
            "action_domain",
            "action_domain_sha256",
            "reconstructor",
            "backends",
            "trials",
            "comparisons",
        },
        "contract_unknown_field",
        "experiment contract",
    )
    if not _integer(contract.get("schema_version")) or (
        contract.get("schema_version") != RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION
    ):
        raise BoundaryBenchmarkError(
            "contract_schema_version_invalid", "unsupported experiment schema_version"
        )
    if contract.get("surface") != RTL_BOUNDARY_EXPERIMENT_CONTRACT_SURFACE:
        raise BoundaryBenchmarkError(
            "contract_surface_invalid", "experiment contract surface is invalid"
        )
    sweep_space = _require_object(
        contract.get("sweep_space"), "contract_sweep_invalid", "sweep_space"
    )
    try:
        enumeration = enumerate_sweep_space(sweep_space)
    except SweepBoundaryError as error:
        raise BoundaryBenchmarkError(
            "contract_sweep_invalid", f"sweep_space is invalid: {error}"
        ) from error
    if contract.get("sweep_space_sha256") != enumeration["sweep_space_sha256"]:
        raise BoundaryBenchmarkError(
            "contract_sweep_hash_mismatch", "sweep_space_sha256 does not recompute"
        )
    action_domain = _validate_action_domain(
        contract.get("action_domain"),
        contract.get("action_domain_sha256"),
        enumeration,
    )
    reconstructor = _require_object(
        contract.get("reconstructor"),
        "contract_reconstructor_invalid",
        "reconstructor",
    )
    if set(reconstructor) != {"kind", "algorithm_version"} or (
        reconstructor.get("kind") != "nearest_observed_graph"
        or not _integer(reconstructor.get("algorithm_version"))
        or reconstructor.get("algorithm_version") != 1
    ):
        raise BoundaryBenchmarkError(
            "contract_reconstructor_invalid", "reconstructor is not supported"
        )
    target = _validate_target(contract.get("target"))
    backends = _validate_backends(contract.get("backends"))
    trials = _validate_trial_contracts(contract.get("trials"), sweep_space, backends)
    comparisons = _validate_comparisons(
        contract.get("comparisons"), trials, backends
    )
    return {
        "experiment_id": _nonempty_string(
            contract.get("experiment_id"),
            "experiment_id_invalid",
            "experiment_id",
        ),
        "target": target,
        "sweep_space": dict(sweep_space),
        "sweep_space_sha256": enumeration["sweep_space_sha256"],
        "point_count": enumeration["point_count"],
        "action_domain": action_domain["action_domain"],
        "action_domain_sha256": action_domain["action_domain_sha256"],
        "reconstructor": dict(reconstructor),
        "backends": backends,
        "trials": trials,
        "comparisons": comparisons,
    }


def _validate_semantic_manifest(
    manifest: Any,
    target: Mapping[str, Any],
    label: str,
) -> None:
    manifest = _require_object(
        manifest, "semantic_manifest_invalid", f"{label} semantic manifest"
    )
    _reject_unknown(
        manifest,
        {
            "schema_version",
            "surface",
            "target_id",
            "revision_label",
            "revision_sha",
            "checkpoint_identity",
            "oracle_identity",
            "observables",
        },
        "semantic_manifest_unknown_field",
        f"{label} semantic manifest",
    )
    if not _integer(manifest.get("schema_version")):
        raise BoundaryBenchmarkError(
            "semantic_manifest_identity_mismatch",
            f"{label} semantic manifest schema_version is invalid",
        )
    expected = {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_SEMANTIC_MANIFEST_SURFACE,
        "target_id": target["target_id"],
        "revision_label": label,
        "revision_sha": target["revisions"][label],
        "checkpoint_identity": target["checkpoint_identity"],
        "oracle_identity": target["oracle_identity"],
    }
    for field, expected_value in expected.items():
        if manifest.get(field) != expected_value:
            raise BoundaryBenchmarkError(
                "semantic_manifest_identity_mismatch",
                f"{label} semantic manifest {field} does not match the Contract",
            )
    raw_observables = manifest.get("observables")
    if not isinstance(raw_observables, list):
        raise BoundaryBenchmarkError(
            "semantic_manifest_observables_invalid",
            f"{label} semantic manifest observables must be a list",
        )
    expected_names = list(target["semantic_observables"]) + [target["oracle_field"]]
    names: list[str] = []
    semantic_ids: list[str] = []
    for index, raw_observable in enumerate(raw_observables):
        observable = _require_object(
            raw_observable,
            "semantic_manifest_observable_invalid",
            f"{label} semantic observable {index}",
        )
        _reject_unknown(
            observable,
            {"name", "semantic_id", "width_bits"},
            "semantic_manifest_observable_unknown_field",
            f"{label} semantic observable {index}",
        )
        names.append(
            _nonempty_string(
                observable.get("name"),
                "semantic_manifest_observable_name_invalid",
                f"{label} semantic observable {index} name",
            )
        )
        semantic_ids.append(
            _nonempty_string(
                observable.get("semantic_id"),
                "semantic_manifest_observable_id_invalid",
                f"{label} semantic observable {index} semantic_id",
            )
        )
        _positive_integer(
            observable.get("width_bits"),
            "semantic_manifest_observable_width_invalid",
            f"{label} semantic observable {index} width_bits",
        )
    if names != expected_names:
        raise BoundaryBenchmarkError(
            "semantic_manifest_observable_names_mismatch",
            f"{label} semantic manifest observable names do not match the Contract",
        )
    if len(set(semantic_ids)) != len(semantic_ids):
        raise BoundaryBenchmarkError(
            "semantic_manifest_observable_ids_duplicate",
            f"{label} semantic manifest semantic IDs are duplicated",
        )
    if _sha256(manifest) != target["semantic_manifest_sha256"][label]:
        raise BoundaryBenchmarkError(
            "semantic_manifest_hash_mismatch",
            f"{label} semantic manifest hash does not match the Contract",
        )


def _validate_semantics(
    raw_semantics: Any,
    target: Mapping[str, Any],
    ground_truth: Mapping[str, Any],
) -> None:
    if not isinstance(raw_semantics, list):
        raise BoundaryBenchmarkError(
            "semantic_observations_invalid", "semantic_observations must be a list"
        )
    truth_by_point = {
        row["point_id"]: row for row in ground_truth["observations"]
    }
    semantic_by_point: dict[str, Mapping[str, Any]] = {}
    expected_projection_keys = set(target["semantic_observables"]) | {
        target["oracle_field"]
    }
    for index, raw_row in enumerate(raw_semantics):
        row = _require_object(
            raw_row, "semantic_observation_invalid", f"semantic observation {index}"
        )
        _reject_unknown(
            row,
            {"point_id", "revisions"},
            "semantic_observation_unknown_field",
            f"semantic observation {index}",
        )
        point_id = row.get("point_id")
        if point_id not in truth_by_point or point_id in semantic_by_point:
            raise BoundaryBenchmarkError(
                "semantic_observation_point_invalid",
                f"semantic observation {index} point is unknown or duplicated",
            )
        revisions = _require_object(
            row.get("revisions"),
            "semantic_observation_revisions_invalid",
            f"semantic observation {index} revisions",
        )
        if set(revisions) != {"bad", "fixed"}:
            raise BoundaryBenchmarkError(
                "semantic_observation_revisions_invalid",
                f"semantic observation {index} must contain bad and fixed",
            )
        for label in ("bad", "fixed"):
            revision = _require_object(
                revisions[label],
                "semantic_projection_invalid",
                f"semantic observation {index} {label}",
            )
            _reject_unknown(
                revision,
                {"cpu", "gpu"},
                "semantic_projection_unknown_field",
                f"semantic observation {index} {label}",
            )
            cpu = _require_object(
                revision.get("cpu"),
                "semantic_cpu_invalid",
                f"semantic observation {index} {label} CPU projection",
            )
            gpu = _require_object(
                revision.get("gpu"),
                "semantic_gpu_invalid",
                f"semantic observation {index} {label} GPU projection",
            )
            if set(cpu) != expected_projection_keys or set(gpu) != expected_projection_keys:
                raise BoundaryBenchmarkError(
                    "semantic_projection_keys_mismatch",
                    f"semantic observation {index} {label} projection keys mismatch",
                )
            if not _exact_equal(cpu, gpu):
                raise BoundaryBenchmarkError(
                    "semantic_cpu_gpu_mismatch",
                    f"semantic observation {index} {label} CPU/GPU mismatch",
                )
            oracle = cpu[target["oracle_field"]]
            if not _integer(oracle) or oracle not in (0, 1):
                raise BoundaryBenchmarkError(
                    "semantic_oracle_invalid",
                    f"semantic observation {index} {label} oracle must be 0 or 1",
                )
            if oracle != truth_by_point[point_id][f"{label}_oracle"]:
                raise BoundaryBenchmarkError(
                    "semantic_oracle_ground_truth_mismatch",
                    f"semantic observation {index} {label} oracle mismatches ground truth",
                )
        semantic_by_point[point_id] = row
    if set(semantic_by_point) != set(truth_by_point):
        raise BoundaryBenchmarkError(
            "semantic_observations_incomplete",
            "semantic observations must cover every sweep point",
        )


def _selection_trace(policy_trial: Mapping[str, Any]) -> list[list[str]]:
    return [list(epoch["selected_point_ids"]) for epoch in policy_trial["epochs"]]


def _observation_trace(policy_trial: Mapping[str, Any]) -> list[list[dict[str, Any]]]:
    return [
        [dict(observation) for observation in epoch["bad_observations"]]
        for epoch in policy_trial["epochs"]
    ]


def _validate_trial_evidence(
    raw_trial: Any,
    trial_contract: Mapping[str, Any],
    contract: Mapping[str, Any],
    ground_truth: Mapping[str, Any],
) -> dict[str, Any]:
    trial_id = trial_contract["trial_id"]
    trial = _require_object(raw_trial, "trial_evidence_invalid", f"trial {trial_id!r}")
    _reject_unknown(
        trial,
        {
            "trial_id",
            "policy_trial",
            "fixed_confirmations",
            "executions",
            "launches",
            "trial_wall_time_ns",
        },
        "trial_evidence_unknown_field",
        f"trial {trial_id!r}",
    )
    if trial.get("trial_id") != trial_id:
        raise BoundaryBenchmarkError(
            "trial_evidence_id_mismatch", f"trial evidence {trial_id!r} id mismatch"
        )
    policy_trial = _require_object(
        trial.get("policy_trial"),
        "policy_trial_invalid",
        f"trial {trial_id!r} policy_trial",
    )
    expected_policy_fields = {
        "surface": RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "sweep_space_sha256": contract["sweep_space_sha256"],
        "policy": trial_contract["policy"],
        "reconstructor": contract["reconstructor"],
        "requested_count": trial_contract["requested_count"],
        "budget_logical_bad_queries": trial_contract["budget_logical_bad_queries"],
    }
    for field, expected in expected_policy_fields.items():
        if not _exact_equal(policy_trial.get(field), expected):
            raise BoundaryBenchmarkError(
                "policy_trial_contract_mismatch",
                f"trial {trial_id!r} policy_trial {field} mismatches the Contract",
            )
    try:
        policy_analysis = analyze_boundary_policy_trial(
            contract["sweep_space"], ground_truth, policy_trial
        )
    except SweepBoundaryError as error:
        raise BoundaryBenchmarkError(
            "policy_trial_replay_failed",
            f"trial {trial_id!r} policy replay failed: {error}",
        ) from error

    truth_by_point = {
        row["point_id"]: row for row in ground_truth["observations"]
    }
    selected_failure_epoch: dict[str, int] = {}
    expected_bad_executions: list[tuple[int, str, str, str]] = []
    for epoch in policy_trial["epochs"]:
        for observation in epoch["bad_observations"]:
            point_id = observation["point_id"]
            expected_bad_executions.append(
                (epoch["epoch_index"], point_id, "bad", "bad_search")
            )
            if observation["bad_oracle"] == 1:
                selected_failure_epoch[point_id] = epoch["epoch_index"]

    raw_confirmations = trial.get("fixed_confirmations")
    if not isinstance(raw_confirmations, list):
        raise BoundaryBenchmarkError(
            "fixed_confirmations_invalid",
            f"trial {trial_id!r} fixed_confirmations must be a list",
        )
    confirmations: dict[str, dict[str, Any]] = {}
    for index, raw_confirmation in enumerate(raw_confirmations):
        confirmation = _require_object(
            raw_confirmation,
            "fixed_confirmation_invalid",
            f"trial {trial_id!r} fixed confirmation {index}",
        )
        _reject_unknown(
            confirmation,
            {"point_id", "epoch_index", "fixed_oracle"},
            "fixed_confirmation_unknown_field",
            f"trial {trial_id!r} fixed confirmation {index}",
        )
        point_id = confirmation.get("point_id")
        if point_id not in selected_failure_epoch or point_id in confirmations:
            raise BoundaryBenchmarkError(
                "fixed_confirmation_point_invalid",
                f"trial {trial_id!r} fixed confirmation point is invalid",
            )
        if confirmation.get("epoch_index") != selected_failure_epoch[point_id]:
            raise BoundaryBenchmarkError(
                "fixed_confirmation_epoch_mismatch",
                f"trial {trial_id!r} fixed confirmation epoch mismatch",
            )
        fixed_oracle = confirmation.get("fixed_oracle")
        if not _integer(fixed_oracle) or fixed_oracle not in (0, 1):
            raise BoundaryBenchmarkError(
                "fixed_confirmation_oracle_invalid",
                f"trial {trial_id!r} fixed confirmation oracle is invalid",
            )
        if fixed_oracle != truth_by_point[point_id]["fixed_oracle"] or fixed_oracle != 0:
            raise BoundaryBenchmarkError(
                "fixed_confirmation_not_disappeared",
                f"trial {trial_id!r} failure did not disappear on fixed revision",
            )
        confirmations[point_id] = dict(confirmation)
    if set(confirmations) != set(selected_failure_epoch):
        raise BoundaryBenchmarkError(
            "fixed_confirmations_incomplete",
            f"trial {trial_id!r} must confirm every selected bad failure",
        )

    raw_executions = trial.get("executions")
    if not isinstance(raw_executions, list) or not raw_executions:
        raise BoundaryBenchmarkError(
            "executions_invalid", f"trial {trial_id!r} executions must be nonempty"
        )
    executions: dict[str, dict[str, Any]] = {}
    actual_execution_keys: list[tuple[int, str, str, str]] = []
    for index, raw_execution in enumerate(raw_executions):
        execution = _require_object(
            raw_execution,
            "execution_invalid",
            f"trial {trial_id!r} execution {index}",
        )
        _reject_unknown(
            execution,
            {
                "execution_id",
                "epoch_index",
                "point_id",
                "revision",
                "purpose",
                "launch_id",
                "cycle_evals",
            },
            "execution_unknown_field",
            f"trial {trial_id!r} execution {index}",
        )
        execution_id = _nonempty_string(
            execution.get("execution_id"),
            "execution_id_invalid",
            f"trial {trial_id!r} execution {index} id",
        )
        if execution_id in executions:
            raise BoundaryBenchmarkError(
                "execution_id_duplicate",
                f"trial {trial_id!r} execution id is duplicated",
            )
        epoch_index = _nonnegative_integer(
            execution.get("epoch_index"),
            "execution_epoch_invalid",
            f"trial {trial_id!r} execution {index} epoch_index",
        )
        point_id = execution.get("point_id")
        if point_id not in truth_by_point:
            raise BoundaryBenchmarkError(
                "execution_point_unknown",
                f"trial {trial_id!r} execution {index} point is unknown",
            )
        revision = execution.get("revision")
        purpose = execution.get("purpose")
        if (revision, purpose) not in {
            ("bad", "bad_search"),
            ("fixed", "fixed_confirmation"),
        }:
            raise BoundaryBenchmarkError(
                "execution_role_invalid",
                f"trial {trial_id!r} execution {index} role is invalid",
            )
        launch_id = _nonempty_string(
            execution.get("launch_id"),
            "execution_launch_invalid",
            f"trial {trial_id!r} execution {index} launch_id",
        )
        cycle_evals = _nonnegative_integer(
            execution.get("cycle_evals"),
            "execution_cycles_invalid",
            f"trial {trial_id!r} execution {index} cycle_evals",
        )
        normalized = {
            "execution_id": execution_id,
            "epoch_index": epoch_index,
            "point_id": point_id,
            "revision": revision,
            "purpose": purpose,
            "launch_id": launch_id,
            "cycle_evals": cycle_evals,
        }
        executions[execution_id] = normalized
        actual_execution_keys.append((epoch_index, point_id, revision, purpose))
    expected_execution_keys = expected_bad_executions + [
        (confirmation["epoch_index"], point_id, "fixed", "fixed_confirmation")
        for point_id, confirmation in confirmations.items()
    ]
    if Counter(actual_execution_keys) != Counter(expected_execution_keys):
        raise BoundaryBenchmarkError(
            "execution_selection_link_mismatch",
            f"trial {trial_id!r} executions do not exactly cover search and confirmation",
        )

    backend = contract["backends"][trial_contract["backend_id"]]
    raw_launches = trial.get("launches")
    if not isinstance(raw_launches, list) or not raw_launches:
        raise BoundaryBenchmarkError(
            "launches_invalid", f"trial {trial_id!r} launches must be nonempty"
        )
    launch_ids: set[str] = set()
    launched_execution_ids: list[str] = []
    maximum_end = 0
    for index, raw_launch in enumerate(raw_launches):
        launch = _require_object(
            raw_launch, "launch_invalid", f"trial {trial_id!r} launch {index}"
        )
        _reject_unknown(
            launch,
            {
                "launch_id",
                "backend_id",
                "executor_identity",
                "resident_width",
                "execution_ids",
                "start_offset_ns",
                "end_offset_ns",
            },
            "launch_unknown_field",
            f"trial {trial_id!r} launch {index}",
        )
        launch_id = _nonempty_string(
            launch.get("launch_id"),
            "launch_id_invalid",
            f"trial {trial_id!r} launch {index} id",
        )
        if launch_id in launch_ids:
            raise BoundaryBenchmarkError(
                "launch_id_duplicate", f"trial {trial_id!r} launch id is duplicated"
            )
        launch_ids.add(launch_id)
        for field in ("backend_id", "executor_identity", "resident_width"):
            if launch.get(field) != backend[field]:
                raise BoundaryBenchmarkError(
                    "launch_backend_identity_mismatch",
                    f"trial {trial_id!r} launch {index} {field} mismatches backend",
                )
        execution_ids = _string_set(
            launch.get("execution_ids"),
            "launch_execution_ids_invalid",
            f"trial {trial_id!r} launch {index} execution_ids",
        )
        if len(execution_ids) > backend["resident_width"]:
            raise BoundaryBenchmarkError(
                "launch_resident_width_exceeded",
                f"trial {trial_id!r} launch {index} exceeds resident width",
            )
        if any(execution_id not in executions for execution_id in execution_ids):
            raise BoundaryBenchmarkError(
                "launch_execution_unknown",
                f"trial {trial_id!r} launch {index} references unknown execution",
            )
        roles = {
            (
                executions[execution_id]["epoch_index"],
                executions[execution_id]["revision"],
                executions[execution_id]["purpose"],
            )
            for execution_id in execution_ids
        }
        if len(roles) != 1:
            raise BoundaryBenchmarkError(
                "launch_feedback_epoch_mixed",
                f"trial {trial_id!r} launch {index} mixes feedback epochs or roles",
            )
        if any(executions[execution_id]["launch_id"] != launch_id for execution_id in execution_ids):
            raise BoundaryBenchmarkError(
                "launch_execution_link_mismatch",
                f"trial {trial_id!r} launch {index} execution linkage mismatches",
            )
        start = _nonnegative_integer(
            launch.get("start_offset_ns"),
            "launch_time_invalid",
            f"trial {trial_id!r} launch {index} start_offset_ns",
        )
        end = _nonnegative_integer(
            launch.get("end_offset_ns"),
            "launch_time_invalid",
            f"trial {trial_id!r} launch {index} end_offset_ns",
        )
        if end < start:
            raise BoundaryBenchmarkError(
                "launch_time_order_invalid",
                f"trial {trial_id!r} launch {index} ends before it starts",
            )
        maximum_end = max(maximum_end, end)
        launched_execution_ids.extend(execution_ids)
    if Counter(launched_execution_ids) != Counter(executions.keys()):
        raise BoundaryBenchmarkError(
            "launch_execution_partition_mismatch",
            f"trial {trial_id!r} launches do not partition executions",
        )
    wall_time = _nonnegative_integer(
        trial.get("trial_wall_time_ns"),
        "trial_wall_time_invalid",
        f"trial {trial_id!r} trial_wall_time_ns",
    )
    if wall_time < maximum_end:
        raise BoundaryBenchmarkError(
            "trial_wall_time_too_small",
            f"trial {trial_id!r} wall time precedes a launch end",
        )

    scheduled = len(executions)
    unique = len(
        {(row["revision"], row["point_id"]) for row in executions.values()}
    )
    accounting = {
        "scheduled_state_evals": scheduled,
        "unique_state_evals": unique,
        "duplicate_state_evals": scheduled - unique,
        "bad_search_state_evals": sum(
            row["purpose"] == "bad_search" for row in executions.values()
        ),
        "fixed_confirmation_state_evals": sum(
            row["purpose"] == "fixed_confirmation" for row in executions.values()
        ),
        "cycle_evals": sum(row["cycle_evals"] for row in executions.values()),
        "batch_launches": len(raw_launches),
        "wall_time_ns": wall_time,
    }
    if accounting["scheduled_state_evals"] != (
        accounting["bad_search_state_evals"]
        + accounting["fixed_confirmation_state_evals"]
    ):
        raise BoundaryBenchmarkError(
            "execution_accounting_invalid",
            f"trial {trial_id!r} execution accounting does not balance",
        )
    return {
        "trial_id": trial_id,
        "backend": dict(backend),
        "policy_analysis": policy_analysis,
        "selection_trace": _selection_trace(policy_trial),
        "observation_trace": _observation_trace(policy_trial),
        "fixed_confirmations": sorted(
            confirmations.values(),
            key=lambda row: (row["epoch_index"], row["point_id"]),
        ),
        "accounting": accounting,
    }


def _comparison_results(
    contract: Mapping[str, Any], trial_results: Mapping[str, Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selector_results: list[dict[str, Any]] = []
    backend_results: list[dict[str, Any]] = []
    for comparison in contract["comparisons"]:
        members = [trial_results[trial_id] for trial_id in comparison["trial_ids"]]
        rows = [
            {
                "trial_id": member["trial_id"],
                "backend": member["backend"],
                "policy": member["policy_analysis"]["policy"],
                "accounting": member["accounting"],
                "first_violation": member["policy_analysis"]["first_violation"],
                "first_bracket": member["policy_analysis"]["first_bracket"],
                "first_exact_boundary": member["policy_analysis"][
                    "prediction_metrics"
                ]["first_exact_boundary"],
                "final_prediction_metrics": member["policy_analysis"][
                    "prediction_metrics"
                ]["epochs"][-1],
            }
            for member in members
        ]
        result = {
            "comparison_id": comparison["comparison_id"],
            "trial_ids": list(comparison["trial_ids"]),
            "rows": rows,
        }
        if comparison["kind"] == "selector":
            result["backend_id"] = members[0]["backend"]["backend_id"]
            selector_results.append(result)
        else:
            reference = members[0]
            for member in members[1:]:
                if not _exact_equal(
                    member["selection_trace"], reference["selection_trace"]
                ) or not _exact_equal(
                    member["observation_trace"], reference["observation_trace"]
                ):
                    raise BoundaryBenchmarkError(
                        "backend_comparison_trace_mismatch",
                        f"backend comparison {comparison['comparison_id']!r} selection trace differs",
                    )
                if not _exact_equal(
                    member["fixed_confirmations"], reference["fixed_confirmations"]
                ):
                    raise BoundaryBenchmarkError(
                        "backend_comparison_confirmation_mismatch",
                        f"backend comparison {comparison['comparison_id']!r} confirmations differ",
                    )
            result["selection_trace_sha256"] = _sha256(
                {
                    "selection_trace": reference["selection_trace"],
                    "observation_trace": reference["observation_trace"],
                    "fixed_confirmations": reference["fixed_confirmations"],
                }
            )
            backend_results.append(result)
    return selector_results, backend_results


def _pass_adjudication(
    contract_hash: str,
    evidence_hash: str,
    contract: Mapping[str, Any],
    ground_truth_analysis: Mapping[str, Any],
    trial_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    selector_comparisons, backend_comparisons = _comparison_results(
        contract, trial_results
    )
    return {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_ADJUDICATION_SURFACE,
        "status": "pass",
        "issues": [],
        "input_canonical_sha256": {
            "experiment_contract": contract_hash,
            "evidence_bundle": evidence_hash,
        },
        "verified_identity": {
            "experiment_id": contract["experiment_id"],
            "target": contract["target"],
            "sweep_space_sha256": contract["sweep_space_sha256"],
            "point_count": contract["point_count"],
            "action_domain_sha256": contract["action_domain_sha256"],
            "reconstructor": contract["reconstructor"],
        },
        "ground_truth_analysis": ground_truth_analysis,
        "trial_results": [trial_results[trial_id] for trial_id in contract["trials"]],
        "selector_comparisons": selector_comparisons,
        "backend_comparisons": backend_comparisons,
    }


def _fail_adjudication(
    contract_hash: str | None,
    evidence_hash: str | None,
    error: BoundaryBenchmarkError,
) -> dict[str, Any]:
    return {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_ADJUDICATION_SURFACE,
        "status": "fail",
        "issues": [{"code": error.code, "message": str(error)}],
        "input_canonical_sha256": {
            "experiment_contract": contract_hash,
            "evidence_bundle": evidence_hash,
        },
    }


def adjudicate_boundary_benchmark(
    experiment_contract: Mapping[str, Any],
    evidence_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify an externally executed boundary benchmark without invoking a DUT."""

    contract_hash: str | None = None
    evidence_hash: str | None = None
    try:
        contract_hash = _sha256(experiment_contract)
        evidence_hash = _sha256(evidence_bundle)
        contract = _validate_contract(experiment_contract)
        evidence = _require_object(
            evidence_bundle, "evidence_invalid", "boundary evidence bundle"
        )
        _reject_unknown(
            evidence,
            {
                "schema_version",
                "surface",
                "experiment_contract_sha256",
                "runner",
                "semantic_manifests",
                "ground_truth",
                "semantic_observations",
                "trials",
            },
            "evidence_unknown_field",
            "boundary evidence bundle",
        )
        if not _integer(evidence.get("schema_version")) or (
            evidence.get("schema_version") != RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION
        ):
            raise BoundaryBenchmarkError(
                "evidence_schema_version_invalid", "unsupported evidence schema_version"
            )
        if evidence.get("surface") != RTL_BOUNDARY_EVIDENCE_BUNDLE_SURFACE:
            raise BoundaryBenchmarkError(
                "evidence_surface_invalid", "boundary evidence surface is invalid"
            )
        if evidence.get("experiment_contract_sha256") != contract_hash:
            raise BoundaryBenchmarkError(
                "evidence_contract_hash_mismatch",
                "evidence does not bind the experiment Contract bytes",
            )
        runner = _require_object(
            evidence.get("runner"), "runner_invalid", "external runner"
        )
        _reject_unknown(
            runner,
            {"status", "identity", "completed_at"},
            "runner_unknown_field",
            "external runner",
        )
        if runner.get("status") != "pass":
            raise BoundaryBenchmarkError(
                "runner_not_complete", "external runner status must be pass"
            )
        _nonempty_string(runner.get("identity"), "runner_identity_invalid", "runner identity")
        _nonempty_string(
            runner.get("completed_at"), "runner_completed_at_invalid", "runner completed_at"
        )
        manifests = _require_object(
            evidence.get("semantic_manifests"),
            "semantic_manifests_invalid",
            "semantic_manifests",
        )
        if set(manifests) != {"bad", "fixed"}:
            raise BoundaryBenchmarkError(
                "semantic_manifests_invalid",
                "semantic_manifests must contain exactly bad and fixed",
            )
        for label in ("bad", "fixed"):
            _validate_semantic_manifest(manifests[label], contract["target"], label)
        if not _exact_equal(
            manifests["bad"]["observables"], manifests["fixed"]["observables"]
        ):
            raise BoundaryBenchmarkError(
                "semantic_manifest_revision_drift",
                "bad and fixed semantic observable bindings must be identical",
            )

        ground_truth = _require_object(
            evidence.get("ground_truth"), "ground_truth_invalid", "ground_truth"
        )
        try:
            ground_truth_analysis = analyze_boundary_ground_truth(
                contract["sweep_space"], ground_truth
            )
        except SweepBoundaryError as error:
            raise BoundaryBenchmarkError(
                "ground_truth_invalid", f"ground truth is invalid: {error}"
            ) from error
        bad = ground_truth_analysis["revisions"]["bad"]
        fixed = ground_truth_analysis["revisions"]["fixed"]
        if not bad["fail_point_ids"]:
            raise BoundaryBenchmarkError(
                "ground_truth_has_no_failure", "bad revision has no failing point"
            )
        if fixed["fail_point_ids"]:
            raise BoundaryBenchmarkError(
                "ground_truth_fixed_failure",
                "fixed revision retains or introduces a failing point",
            )
        if set(ground_truth_analysis["bad_to_fixed"]["disappeared_failure_point_ids"]) != set(
            bad["fail_point_ids"]
        ):
            raise BoundaryBenchmarkError(
                "ground_truth_disappearance_incomplete",
                "bad failures do not all disappear on the fixed revision",
            )
        _validate_semantics(
            evidence.get("semantic_observations"), contract["target"], ground_truth
        )

        raw_trials = evidence.get("trials")
        if not isinstance(raw_trials, list):
            raise BoundaryBenchmarkError(
                "trial_evidence_invalid", "evidence trials must be a list"
            )
        raw_by_id: dict[str, Mapping[str, Any]] = {}
        for raw_trial in raw_trials:
            if not isinstance(raw_trial, Mapping):
                raise BoundaryBenchmarkError(
                    "trial_evidence_invalid", "each evidence trial must be an object"
                )
            trial_id = raw_trial.get("trial_id")
            if trial_id not in contract["trials"] or trial_id in raw_by_id:
                raise BoundaryBenchmarkError(
                    "trial_evidence_id_invalid",
                    "evidence trial id is unknown or duplicated",
                )
            raw_by_id[trial_id] = raw_trial
        if set(raw_by_id) != set(contract["trials"]):
            raise BoundaryBenchmarkError(
                "trial_evidence_incomplete", "evidence must contain every Contract trial"
            )
        trial_results = {
            trial_id: _validate_trial_evidence(
                raw_by_id[trial_id],
                trial_contract,
                contract,
                ground_truth,
            )
            for trial_id, trial_contract in contract["trials"].items()
        }
        return _pass_adjudication(
            contract_hash,
            evidence_hash,
            contract,
            ground_truth_analysis,
            trial_results,
        )
    except BoundaryBenchmarkError as error:
        return _fail_adjudication(contract_hash, evidence_hash, error)
