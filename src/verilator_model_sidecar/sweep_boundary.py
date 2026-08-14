"""Deterministic finite-grid models for RTL bug-boundary benchmarks."""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


RTL_BOUNDARY_SCHEMA_VERSION = 1
RTL_BOUNDARY_SWEEP_SPACE_SURFACE = "rtl_boundary_sweep_space"
RTL_BOUNDARY_SWEEP_ENUMERATION_SURFACE = "rtl_boundary_sweep_enumeration"
RTL_BOUNDARY_GROUND_TRUTH_SURFACE = "rtl_boundary_ground_truth"
RTL_BOUNDARY_ANALYSIS_SURFACE = "rtl_boundary_analysis"
RTL_BOUNDARY_POLICY_TRIAL_SURFACE = "rtl_boundary_policy_trial"
RTL_BOUNDARY_POLICY_ANALYSIS_SURFACE = "rtl_boundary_policy_analysis"


class SweepBoundaryError(ValueError):
    """Raised when a finite sweep or its complete ground truth is invalid."""


@dataclass(frozen=True)
class _Axis:
    name: str
    kind: str
    values: tuple[str | int | bool, ...]
    adjacent_pairs: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Point:
    point_id: str
    coordinates: tuple[int, ...]
    parameters: dict[str, str | int | bool]


@dataclass(frozen=True)
class _SweepModel:
    axes: tuple[_Axis, ...]
    normalized_axes: list[dict[str, Any]]
    sweep_space_sha256: str
    points: tuple[_Point, ...]
    point_by_id: dict[str, _Point]
    point_by_coordinates: dict[tuple[int, ...], _Point]


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
        raise SweepBoundaryError("value is not canonical JSON") from error


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _stable_rank(*parts: Any) -> str:
    return _sha256({"rank": list(parts)})


def _exact_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_integer(value: Any, label: str) -> int:
    if not _exact_integer(value) or value <= 0:
        raise SweepBoundaryError(f"{label} must be a positive integer")
    return value


def _supported_axis_value(value: Any) -> bool:
    return isinstance(value, (str, bool)) or _exact_integer(value)


def _value_identity(value: str | int | bool) -> bytes:
    if isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, int):
        kind = "integer"
    else:
        kind = "string"
    return _canonical_bytes({"kind": kind, "value": value})


def _reject_unknown_fields(
    value: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    unknown = sorted(set(value) - allowed, key=repr)
    if unknown:
        raise SweepBoundaryError(f"{label} has unknown fields: {unknown}")


def _point_id(
    sweep_space_sha256: str, parameters: Mapping[str, str | int | bool]
) -> str:
    digest = _sha256(
        {
            "parameters": dict(parameters),
            "sweep_space_sha256": sweep_space_sha256,
        }
    )
    return f"point:v1:{digest}"


def _compile_sweep_space(sweep_space: Mapping[str, Any]) -> _SweepModel:
    if not isinstance(sweep_space, Mapping):
        raise SweepBoundaryError("sweep space must be an object")
    _reject_unknown_fields(
        sweep_space,
        {"schema_version", "surface", "axes"},
        "sweep space",
    )
    if not _exact_integer(sweep_space.get("schema_version")) or sweep_space.get(
        "schema_version"
    ) != RTL_BOUNDARY_SCHEMA_VERSION:
        raise SweepBoundaryError("unsupported sweep-space schema_version")
    if sweep_space.get("surface") != RTL_BOUNDARY_SWEEP_SPACE_SURFACE:
        raise SweepBoundaryError("unexpected sweep-space surface")
    raw_axes = sweep_space.get("axes")
    if not isinstance(raw_axes, list) or not raw_axes:
        raise SweepBoundaryError("sweep space must contain at least one axis")

    axes: list[_Axis] = []
    names: set[str] = set()
    for index, raw_axis in enumerate(raw_axes):
        if not isinstance(raw_axis, Mapping):
            raise SweepBoundaryError(f"axis {index} must be an object")
        _reject_unknown_fields(
            raw_axis,
            {"name", "kind", "values", "adjacent_value_pairs"},
            f"axis {index}",
        )
        name = raw_axis.get("name")
        kind = raw_axis.get("kind")
        raw_values = raw_axis.get("values")
        if not isinstance(name, str) or not name:
            raise SweepBoundaryError(f"axis {index} name must be nonempty")
        if name in names:
            raise SweepBoundaryError(f"axis name {name!r} is duplicated")
        names.add(name)
        if kind not in {"ordered", "categorical"}:
            raise SweepBoundaryError(f"axis {name!r} has unsupported kind")
        if not isinstance(raw_values, list) or len(raw_values) < 2:
            raise SweepBoundaryError(f"axis {name!r} must contain at least two values")
        values: list[str | int | bool] = []
        identities: set[bytes] = set()
        for value in raw_values:
            if not _supported_axis_value(value):
                raise SweepBoundaryError(
                    f"axis {name!r} values must be strings, integers, or booleans"
                )
            identity = _value_identity(value)
            if identity in identities:
                raise SweepBoundaryError(f"axis {name!r} contains a duplicate value")
            identities.add(identity)
            values.append(value)
        if kind == "categorical":
            values.sort(key=_value_identity)
        adjacent_pairs: list[tuple[int, int]] = []
        raw_pairs = raw_axis.get("adjacent_value_pairs")
        if kind == "ordered":
            if "adjacent_value_pairs" in raw_axis:
                raise SweepBoundaryError(
                    f"ordered axis {name!r} must not declare adjacent_value_pairs"
                )
        else:
            if not isinstance(raw_pairs, list):
                raise SweepBoundaryError(
                    f"categorical axis {name!r} must declare adjacent_value_pairs"
                )
            value_indices = {
                _value_identity(value): value_index
                for value_index, value in enumerate(values)
            }
            seen_pairs: set[tuple[int, int]] = set()
            for pair in raw_pairs:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise SweepBoundaryError(
                        f"categorical axis {name!r} adjacency must contain value pairs"
                    )
                if not all(_supported_axis_value(value) for value in pair):
                    raise SweepBoundaryError(
                        f"categorical axis {name!r} adjacency has an unsupported value"
                    )
                try:
                    endpoints = tuple(
                        value_indices[_value_identity(value)] for value in pair
                    )
                except KeyError as error:
                    raise SweepBoundaryError(
                        f"categorical axis {name!r} adjacency references an unknown value"
                    ) from error
                normalized_pair = tuple(sorted(endpoints))
                if normalized_pair[0] == normalized_pair[1]:
                    raise SweepBoundaryError(
                        f"categorical axis {name!r} adjacency cannot be a self-edge"
                    )
                if normalized_pair in seen_pairs:
                    raise SweepBoundaryError(
                        f"categorical axis {name!r} adjacency is duplicated"
                    )
                seen_pairs.add(normalized_pair)
                adjacent_pairs.append(normalized_pair)
            adjacent_pairs.sort()
        axes.append(
            _Axis(
                name=name,
                kind=kind,
                values=tuple(values),
                adjacent_pairs=tuple(adjacent_pairs),
            )
        )

    axes.sort(key=lambda axis: axis.name)
    normalized_axes = []
    for axis in axes:
        normalized_axis: dict[str, Any] = {
            "name": axis.name,
            "kind": axis.kind,
            "values": list(axis.values),
        }
        if axis.kind == "categorical":
            normalized_axis["adjacent_value_pairs"] = [
                [axis.values[left], axis.values[right]]
                for left, right in axis.adjacent_pairs
            ]
        normalized_axes.append(normalized_axis)
    normalized_space = {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_SWEEP_SPACE_SURFACE,
        "axes": normalized_axes,
    }
    sweep_space_sha256 = _sha256(normalized_space)

    points: list[_Point] = []
    point_by_coordinates: dict[tuple[int, ...], _Point] = {}
    for coordinates in itertools.product(
        *(range(len(axis.values)) for axis in axes)
    ):
        parameters = {
            axis.name: axis.values[value_index]
            for axis, value_index in zip(axes, coordinates)
        }
        point = _Point(
            point_id=_point_id(sweep_space_sha256, parameters),
            coordinates=tuple(coordinates),
            parameters=parameters,
        )
        points.append(point)
        point_by_coordinates[point.coordinates] = point
    point_by_id = {point.point_id: point for point in points}
    return _SweepModel(
        axes=tuple(axes),
        normalized_axes=normalized_axes,
        sweep_space_sha256=sweep_space_sha256,
        points=tuple(points),
        point_by_id=point_by_id,
        point_by_coordinates=point_by_coordinates,
    )


def enumerate_sweep_space(sweep_space: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a finite sweep space and return its canonical point enumeration."""

    model = _compile_sweep_space(sweep_space)
    return {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_SWEEP_ENUMERATION_SURFACE,
        "sweep_space_sha256": model.sweep_space_sha256,
        "axes": model.normalized_axes,
        "point_count": len(model.points),
        "points": [
            {"point_id": point.point_id, "parameters": point.parameters}
            for point in model.points
        ],
    }


def _normalize_parameters(
    raw_parameters: Any, model: _SweepModel
) -> dict[str, str | int | bool]:
    if not isinstance(raw_parameters, Mapping):
        raise SweepBoundaryError("observation parameters must be an object")
    expected_names = {axis.name for axis in model.axes}
    if set(raw_parameters) != expected_names:
        raise SweepBoundaryError("observation parameters do not match sweep axes")
    normalized: dict[str, str | int | bool] = {}
    for axis in model.axes:
        value = raw_parameters.get(axis.name)
        if not _supported_axis_value(value):
            raise SweepBoundaryError(
                f"observation parameter {axis.name!r} has an unsupported value"
            )
        allowed = {_value_identity(candidate) for candidate in axis.values}
        if _value_identity(value) not in allowed:
            raise SweepBoundaryError(
                f"observation parameter {axis.name!r} is outside its axis"
            )
        normalized[axis.name] = value
    return normalized


def _normalize_ground_truth(
    ground_truth: Mapping[str, Any], model: _SweepModel
) -> list[dict[str, Any]]:
    if not isinstance(ground_truth, Mapping):
        raise SweepBoundaryError("ground truth must be an object")
    _reject_unknown_fields(
        ground_truth,
        {"schema_version", "surface", "sweep_space_sha256", "observations"},
        "ground truth",
    )
    if not _exact_integer(ground_truth.get("schema_version")) or ground_truth.get(
        "schema_version"
    ) != RTL_BOUNDARY_SCHEMA_VERSION:
        raise SweepBoundaryError("unsupported ground-truth schema_version")
    if ground_truth.get("surface") != RTL_BOUNDARY_GROUND_TRUTH_SURFACE:
        raise SweepBoundaryError("unexpected ground-truth surface")
    if ground_truth.get("sweep_space_sha256") != model.sweep_space_sha256:
        raise SweepBoundaryError("ground truth sweep-space identity mismatch")
    raw_observations = ground_truth.get("observations")
    if not isinstance(raw_observations, list):
        raise SweepBoundaryError("ground truth observations must be a list")

    by_point: dict[str, dict[str, Any]] = {}
    for index, raw_observation in enumerate(raw_observations):
        if not isinstance(raw_observation, Mapping):
            raise SweepBoundaryError(f"observation {index} must be an object")
        _reject_unknown_fields(
            raw_observation,
            {"point_id", "parameters", "bad_oracle", "fixed_oracle"},
            f"observation {index}",
        )
        parameters = _normalize_parameters(raw_observation.get("parameters"), model)
        expected_point_id = _point_id(model.sweep_space_sha256, parameters)
        if raw_observation.get("point_id") != expected_point_id:
            raise SweepBoundaryError(f"observation {index} point identity mismatch")
        if expected_point_id not in model.point_by_id:
            raise SweepBoundaryError(f"observation {index} is outside the sweep grid")
        if expected_point_id in by_point:
            raise SweepBoundaryError(f"observation {index} duplicates a sweep point")
        bad_oracle = raw_observation.get("bad_oracle")
        fixed_oracle = raw_observation.get("fixed_oracle")
        if not _exact_integer(bad_oracle) or bad_oracle not in (0, 1):
            raise SweepBoundaryError(f"observation {index} bad_oracle must be 0 or 1")
        if not _exact_integer(fixed_oracle) or fixed_oracle not in (0, 1):
            raise SweepBoundaryError(
                f"observation {index} fixed_oracle must be 0 or 1"
            )
        by_point[expected_point_id] = {
            "point_id": expected_point_id,
            "parameters": parameters,
            "bad_oracle": bad_oracle,
            "fixed_oracle": fixed_oracle,
        }

    expected_ids = set(model.point_by_id)
    observed_ids = set(by_point)
    if observed_ids != expected_ids:
        missing = len(expected_ids - observed_ids)
        extra = len(observed_ids - expected_ids)
        raise SweepBoundaryError(
            f"ground truth must cover the complete sweep grid: missing={missing}, extra={extra}"
        )
    return [by_point[point.point_id] for point in model.points]


def _grid_edges(model: _SweepModel) -> list[tuple[str, str, str, str]]:
    edges: list[tuple[str, str, str, str]] = []
    for point in model.points:
        for axis_index, axis in enumerate(model.axes):
            current = point.coordinates[axis_index]
            if axis.kind == "ordered":
                candidates = (current + 1,) if current + 1 < len(axis.values) else ()
            else:
                candidates = (
                    right
                    for left, right in axis.adjacent_pairs
                    if left == current
                )
            for candidate in candidates:
                neighbor_coordinates = list(point.coordinates)
                neighbor_coordinates[axis_index] = candidate
                neighbor = model.point_by_coordinates[tuple(neighbor_coordinates)]
                edges.append(
                    (axis.name, axis.kind, point.point_id, neighbor.point_id)
                )
    return edges


def _grid_neighbors(model: _SweepModel) -> dict[str, set[str]]:
    neighbors: dict[str, set[str]] = {point.point_id: set() for point in model.points}
    for _axis_name, _axis_kind, left, right in _grid_edges(model):
        neighbors[left].add(right)
        neighbors[right].add(left)
    return neighbors


def _minimal_failing_points(
    model: _SweepModel, failing_ids: set[str]
) -> dict[str, Any]:
    failing = [model.point_by_id[point_id] for point_id in failing_ids]
    ordered_indices = [
        index for index, axis in enumerate(model.axes) if axis.kind == "ordered"
    ]
    categorical_indices = [
        index for index, axis in enumerate(model.axes) if axis.kind == "categorical"
    ]
    if not ordered_indices:
        return {"status": "not_applicable", "point_ids": []}
    minimal: list[str] = []
    for point in failing:
        dominated = False
        for candidate in failing:
            if candidate.point_id == point.point_id:
                continue
            if any(
                candidate.coordinates[index] != point.coordinates[index]
                for index in categorical_indices
            ):
                continue
            no_greater = all(
                candidate.coordinates[index] <= point.coordinates[index]
                for index in ordered_indices
            )
            strictly_less = any(
                candidate.coordinates[index] < point.coordinates[index]
                for index in ordered_indices
            )
            if no_greater and strictly_less:
                dominated = True
                break
        if not dominated:
            minimal.append(point.point_id)
    return {"status": "computed", "point_ids": sorted(minimal)}


def _failure_components(
    failing_ids: set[str], fail_neighbors: Mapping[str, set[str]]
) -> list[dict[str, Any]]:
    pending = set(failing_ids)
    components: list[list[str]] = []
    while pending:
        first = min(pending)
        pending.remove(first)
        queue = deque([first])
        component = [first]
        while queue:
            current = queue.popleft()
            for neighbor in sorted(fail_neighbors.get(current, set())):
                if neighbor in pending:
                    pending.remove(neighbor)
                    queue.append(neighbor)
                    component.append(neighbor)
        components.append(sorted(component))
    components.sort(key=lambda point_ids: tuple(point_ids))
    return [
        {
            "component_id": f"failure-component:v1:{_sha256(point_ids)}",
            "point_ids": point_ids,
        }
        for point_ids in components
    ]


def _analyze_revision(
    model: _SweepModel,
    oracle_by_point: Mapping[str, int],
    edges: Sequence[tuple[str, str, str, str]],
) -> dict[str, Any]:
    failing_ids = {
        point_id for point_id, oracle in oracle_by_point.items() if oracle == 1
    }
    passing_ids = set(model.point_by_id) - failing_ids
    boundary_edges: list[dict[str, str]] = []
    fail_neighbors: dict[str, set[str]] = {
        point_id: set() for point_id in failing_ids
    }
    for axis_name, axis_kind, left, right in edges:
        left_fails = left in failing_ids
        right_fails = right in failing_ids
        if left_fails and right_fails:
            fail_neighbors[left].add(right)
            fail_neighbors[right].add(left)
        elif left_fails != right_fails:
            boundary_edges.append(
                {
                    "axis": axis_name,
                    "axis_kind": axis_kind,
                    "pass_point_id": right if left_fails else left,
                    "fail_point_id": left if left_fails else right,
                }
            )
    boundary_edges.sort(
        key=lambda edge: (
            edge["axis"],
            edge["pass_point_id"],
            edge["fail_point_id"],
        )
    )
    components = _failure_components(failing_ids, fail_neighbors)
    minimal = _minimal_failing_points(model, failing_ids)
    return {
        "pass_point_count": len(passing_ids),
        "fail_point_count": len(failing_ids),
        "pass_point_ids": sorted(passing_ids),
        "fail_point_ids": sorted(failing_ids),
        "boundary_edge_count": len(boundary_edges),
        "boundary_edges": boundary_edges,
        "failure_component_count": len(components),
        "failure_components": components,
        "minimal_failing_points": minimal,
    }


def analyze_boundary_ground_truth(
    sweep_space: Mapping[str, Any], ground_truth: Mapping[str, Any]
) -> dict[str, Any]:
    """Recompute paired bad/fixed boundary topology from a complete finite grid."""

    model = _compile_sweep_space(sweep_space)
    observations = _normalize_ground_truth(ground_truth, model)
    edges = _grid_edges(model)
    bad_oracle = {
        observation["point_id"]: observation["bad_oracle"]
        for observation in observations
    }
    fixed_oracle = {
        observation["point_id"]: observation["fixed_oracle"]
        for observation in observations
    }
    bad = _analyze_revision(model, bad_oracle, edges)
    fixed = _analyze_revision(model, fixed_oracle, edges)
    bad_failures = set(bad["fail_point_ids"])
    fixed_failures = set(fixed["fail_point_ids"])
    return {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_ANALYSIS_SURFACE,
        "sweep_space_sha256": model.sweep_space_sha256,
        "axes": model.normalized_axes,
        "point_count": len(model.points),
        "observations": observations,
        "revisions": {"bad": bad, "fixed": fixed},
        "bad_to_fixed": {
            "disappeared_failure_point_ids": sorted(
                bad_failures - fixed_failures
            ),
            "persistent_failure_point_ids": sorted(
                bad_failures & fixed_failures
            ),
            "introduced_failure_point_ids": sorted(
                fixed_failures - bad_failures
            ),
        },
    }


def _validate_policy_spec(policy_spec: Mapping[str, Any], model: _SweepModel) -> dict[str, Any]:
    if not isinstance(policy_spec, Mapping):
        raise SweepBoundaryError("policy spec must be an object")
    _reject_unknown_fields(
        policy_spec,
        {"kind", "algorithm_version", "seed_sha256", "configuration"},
        "policy spec",
    )
    kind = policy_spec.get("kind")
    if kind not in {
        "random",
        "stratified",
        "ordered_refinement",
        "novelty_boundary_guided",
    }:
        raise SweepBoundaryError("policy spec has unsupported kind")
    if not _exact_integer(policy_spec.get("algorithm_version")) or policy_spec.get(
        "algorithm_version"
    ) != 1:
        raise SweepBoundaryError("policy spec has unsupported algorithm_version")
    seed_sha256 = policy_spec.get("seed_sha256")
    if (
        not isinstance(seed_sha256, str)
        or len(seed_sha256) != 64
        or any(character not in "0123456789abcdef" for character in seed_sha256)
    ):
        raise SweepBoundaryError("policy spec seed_sha256 must be lowercase sha256")
    configuration = policy_spec.get("configuration")
    if not isinstance(configuration, Mapping):
        raise SweepBoundaryError("policy spec configuration must be an object")
    axis_by_name = {axis.name: axis for axis in model.axes}
    if kind in {"random", "novelty_boundary_guided"}:
        _reject_unknown_fields(configuration, set(), f"{kind} configuration")
    elif kind == "stratified":
        _reject_unknown_fields(configuration, {"strata_axes"}, "stratified configuration")
        strata_axes = configuration.get("strata_axes")
        if not isinstance(strata_axes, list) or not strata_axes:
            raise SweepBoundaryError("stratified configuration requires strata_axes")
        if not all(isinstance(axis, str) and axis for axis in strata_axes):
            raise SweepBoundaryError("strata_axes must contain nonempty strings")
        if len(set(strata_axes)) != len(strata_axes):
            raise SweepBoundaryError("strata_axes contains duplicates")
        unknown = sorted(set(strata_axes) - set(axis_by_name))
        if unknown:
            raise SweepBoundaryError(f"strata_axes contains unknown axes: {unknown}")
    else:
        _reject_unknown_fields(
            configuration, {"axis"}, "ordered_refinement configuration"
        )
        axis_name = configuration.get("axis")
        axis = axis_by_name.get(axis_name)
        if axis is None:
            raise SweepBoundaryError("ordered_refinement axis is unknown")
        if axis.kind != "ordered":
            raise SweepBoundaryError("ordered_refinement axis must be ordered")
    return {
        "kind": kind,
        "algorithm_version": 1,
        "seed_sha256": seed_sha256,
        "configuration": dict(configuration),
    }


def _validate_reconstructor_spec(reconstructor_spec: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(reconstructor_spec, Mapping):
        raise SweepBoundaryError("reconstructor spec must be an object")
    _reject_unknown_fields(
        reconstructor_spec,
        {"kind", "algorithm_version"},
        "reconstructor spec",
    )
    if reconstructor_spec.get("kind") != "nearest_observed_graph":
        raise SweepBoundaryError("reconstructor spec has unsupported kind")
    if not _exact_integer(reconstructor_spec.get("algorithm_version")) or (
        reconstructor_spec.get("algorithm_version") != 1
    ):
        raise SweepBoundaryError("reconstructor spec has unsupported algorithm_version")
    return {"kind": "nearest_observed_graph", "algorithm_version": 1}


def _normalize_public_batches(
    completed_public_batches: Sequence[Mapping[str, Any]],
    model: _SweepModel,
) -> tuple[list[str], dict[str, int], dict[str, set[str]]]:
    if not isinstance(completed_public_batches, Sequence) or isinstance(
        completed_public_batches, (str, bytes, bytearray)
    ):
        raise SweepBoundaryError("completed_public_batches must be a sequence")
    completed_order: list[str] = []
    observed_oracle: dict[str, int] = {}
    coverage_by_point: dict[str, set[str]] = {}
    for batch_index, batch in enumerate(completed_public_batches):
        if not isinstance(batch, Mapping):
            raise SweepBoundaryError(f"completed batch {batch_index} must be an object")
        _reject_unknown_fields(
            batch,
            {"selected_point_ids", "bad_observations"},
            f"completed batch {batch_index}",
        )
        selected = batch.get("selected_point_ids")
        observations = batch.get("bad_observations")
        if not isinstance(selected, list) or not isinstance(observations, list):
            raise SweepBoundaryError(
                f"completed batch {batch_index} selected and observations must be lists"
            )
        if len(selected) != len(observations):
            raise SweepBoundaryError(
                f"completed batch {batch_index} observations do not match selections"
            )
        for offset, point_id in enumerate(selected):
            if not isinstance(point_id, str) or point_id not in model.point_by_id:
                raise SweepBoundaryError(
                    f"completed batch {batch_index} has an unknown point id"
                )
            observation = observations[offset]
            if not isinstance(observation, Mapping):
                raise SweepBoundaryError(
                    f"completed batch {batch_index} observation {offset} must be an object"
                )
            _reject_unknown_fields(
                observation,
                {"point_id", "bad_oracle", "coverage_feature_ids"},
                f"completed batch {batch_index} observation {offset}",
            )
            if observation.get("point_id") != point_id:
                raise SweepBoundaryError(
                    f"completed batch {batch_index} observation {offset} point mismatch"
                )
            if point_id in observed_oracle:
                raise SweepBoundaryError(
                    f"completed batch {batch_index} repeats a completed point"
                )
            bad_oracle = observation.get("bad_oracle")
            if not _exact_integer(bad_oracle) or bad_oracle not in (0, 1):
                raise SweepBoundaryError(
                    f"completed batch {batch_index} observation {offset} bad_oracle must be 0 or 1"
                )
            raw_features = observation.get("coverage_feature_ids", [])
            if not isinstance(raw_features, list):
                raise SweepBoundaryError(
                    f"completed batch {batch_index} observation {offset} coverage_feature_ids must be a list"
                )
            if not all(isinstance(feature, str) and feature for feature in raw_features):
                raise SweepBoundaryError(
                    f"completed batch {batch_index} observation {offset} coverage features must be nonempty strings"
                )
            if len(set(raw_features)) != len(raw_features):
                raise SweepBoundaryError(
                    f"completed batch {batch_index} observation {offset} duplicates coverage features"
                )
            completed_order.append(point_id)
            observed_oracle[point_id] = bad_oracle
            coverage_by_point.setdefault(point_id, set()).update(raw_features)
    return completed_order, observed_oracle, coverage_by_point


def _random_order(
    model: _SweepModel, policy: Mapping[str, Any], namespace: str = "random"
) -> list[str]:
    return sorted(
        model.point_by_id,
        key=lambda point_id: _stable_rank(
            namespace,
            policy["kind"],
            policy["algorithm_version"],
            policy["seed_sha256"],
            model.sweep_space_sha256,
            point_id,
        ),
    )


def _select_random(
    model: _SweepModel, policy: Mapping[str, Any], completed: set[str]
) -> list[str]:
    return [
        point_id for point_id in _random_order(model, policy) if point_id not in completed
    ]


def _select_stratified(
    model: _SweepModel, policy: Mapping[str, Any], completed: set[str]
) -> list[str]:
    strata_axes = tuple(policy["configuration"]["strata_axes"])
    strata: dict[tuple[tuple[str, str | int | bool], ...], list[str]] = {}
    for point in model.points:
        key = tuple((axis, point.parameters[axis]) for axis in strata_axes)
        strata.setdefault(key, []).append(point.point_id)
    for point_ids in strata.values():
        point_ids.sort(
            key=lambda point_id: _stable_rank(
                "stratified-point",
                policy["seed_sha256"],
                model.sweep_space_sha256,
                point_id,
            )
        )
    ordered_strata = sorted(
        strata,
        key=lambda key: _stable_rank(
            "stratum", policy["seed_sha256"], model.sweep_space_sha256, key
        ),
    )
    pending = {key: [point_id for point_id in strata[key] if point_id not in completed] for key in ordered_strata}
    order: list[str] = []
    while True:
        progressed = False
        for key in ordered_strata:
            if pending[key]:
                order.append(pending[key].pop(0))
                progressed = True
        if not progressed:
            return order


def _line_key(point: _Point, axis_index: int) -> tuple[int, ...]:
    return point.coordinates[:axis_index] + point.coordinates[axis_index + 1 :]


def _line_point(
    model: _SweepModel, axis_index: int, line: tuple[int, ...], value_index: int
) -> _Point:
    coordinates = list(line)
    coordinates.insert(axis_index, value_index)
    return model.point_by_coordinates[tuple(coordinates)]


def _select_ordered_refinement(
    model: _SweepModel,
    policy: Mapping[str, Any],
    completed: set[str],
    observed_oracle: Mapping[str, int],
) -> list[str]:
    axis_name = policy["configuration"]["axis"]
    axis_index = next(index for index, axis in enumerate(model.axes) if axis.name == axis_name)
    axis = model.axes[axis_index]
    lines = sorted(
        {_line_key(point, axis_index) for point in model.points},
        key=lambda line: _stable_rank(
            "refinement-line", policy["seed_sha256"], model.sweep_space_sha256, line
        ),
    )
    candidates: list[str] = []
    for line in lines:
        line_points = [
            _line_point(model, axis_index, line, value_index)
            for value_index in range(len(axis.values))
        ]
        observed = [
            (index, point.point_id, observed_oracle[point.point_id])
            for index, point in enumerate(line_points)
            if point.point_id in observed_oracle
        ]
        endpoints = (line_points[0].point_id, line_points[-1].point_id)
        for point_id in endpoints:
            if point_id not in completed and point_id not in candidates:
                candidates.append(point_id)
        brackets: list[tuple[int, int]] = []
        for left_index, left_id, left_oracle in observed:
            for right_index, right_id, right_oracle in observed:
                if left_index >= right_index or left_oracle == right_oracle:
                    continue
                interior = [
                    line_points[index].point_id
                    for index in range(left_index + 1, right_index)
                    if line_points[index].point_id not in completed
                ]
                if interior:
                    brackets.append((right_index - left_index, left_index, right_index))
        for _width, left_index, right_index in sorted(brackets, reverse=True):
            midpoint = (left_index + right_index) // 2
            if midpoint == left_index:
                midpoint += 1
            point_id = line_points[midpoint].point_id
            if point_id not in completed and point_id not in candidates:
                candidates.append(point_id)
    return candidates


def _select_novelty_boundary_guided(
    model: _SweepModel,
    policy: Mapping[str, Any],
    completed: set[str],
    observed_oracle: Mapping[str, int],
    coverage_by_point: Mapping[str, set[str]],
) -> list[str]:
    if not observed_oracle:
        return _select_random(model, policy, completed)
    neighbors = _grid_neighbors(model)
    feature_counts: dict[str, int] = {}
    for features in coverage_by_point.values():
        for feature in features:
            feature_counts[feature] = feature_counts.get(feature, 0) + 1

    observed = set(observed_oracle)
    distances = {point_id: 0 for point_id in observed}
    queue = deque(observed)
    while queue:
        current = queue.popleft()
        for neighbor in sorted(neighbors[current]):
            if neighbor not in distances:
                distances[neighbor] = distances[current] + 1
                queue.append(neighbor)

    scored: list[tuple[tuple[Any, ...], str]] = []
    for point in model.points:
        if point.point_id in completed:
            continue
        labels = {observed_oracle[neighbor] for neighbor in neighbors[point.point_id] if neighbor in observed_oracle}
        has_boundary_neighbors = labels == {0, 1}
        touches_observed = bool(labels)
        novel_neighbor_features = 0
        for neighbor in neighbors[point.point_id]:
            for feature in coverage_by_point.get(neighbor, set()):
                if feature_counts.get(feature, 0) == 1:
                    novel_neighbor_features += 1
        score = (
            0 if has_boundary_neighbors else 1,
            0 if touches_observed else 1,
            -novel_neighbor_features,
            distances.get(point.point_id, len(model.points)),
            _stable_rank(
                "novelty-boundary",
                policy["seed_sha256"],
                model.sweep_space_sha256,
                point.point_id,
            ),
        )
        scored.append((score, point.point_id))
    return [point_id for _score, point_id in sorted(scored)]


def select_boundary_points(
    sweep_space: Mapping[str, Any],
    policy_spec: Mapping[str, Any],
    completed_public_batches: Sequence[Mapping[str, Any]],
    requested_count: int,
) -> tuple[str, ...]:
    """Select the next logical boundary-sweep points from public feedback only."""

    model = _compile_sweep_space(sweep_space)
    requested = _positive_integer(requested_count, "requested_count")
    policy = _validate_policy_spec(policy_spec, model)
    completed_order, observed_oracle, coverage_by_point = _normalize_public_batches(
        completed_public_batches, model
    )
    completed = set(completed_order)
    if policy["kind"] == "random":
        candidates = _select_random(model, policy, completed)
    elif policy["kind"] == "stratified":
        candidates = _select_stratified(model, policy, completed)
    elif policy["kind"] == "ordered_refinement":
        candidates = _select_ordered_refinement(
            model, policy, completed, observed_oracle
        )
    else:
        candidates = _select_novelty_boundary_guided(
            model, policy, completed, observed_oracle, coverage_by_point
        )
    return tuple(candidates[:requested])


def _normalize_trial(trial: Mapping[str, Any], model: _SweepModel) -> dict[str, Any]:
    if not isinstance(trial, Mapping):
        raise SweepBoundaryError("policy trial must be an object")
    _reject_unknown_fields(
        trial,
        {
            "schema_version",
            "surface",
            "sweep_space_sha256",
            "policy",
            "reconstructor",
            "requested_count",
            "budget_logical_bad_queries",
            "epochs",
        },
        "policy trial",
    )
    if not _exact_integer(trial.get("schema_version")) or trial.get(
        "schema_version"
    ) != RTL_BOUNDARY_SCHEMA_VERSION:
        raise SweepBoundaryError("unsupported policy-trial schema_version")
    if trial.get("surface") != RTL_BOUNDARY_POLICY_TRIAL_SURFACE:
        raise SweepBoundaryError("unexpected policy-trial surface")
    if trial.get("sweep_space_sha256") != model.sweep_space_sha256:
        raise SweepBoundaryError("policy trial sweep-space identity mismatch")
    policy = _validate_policy_spec(trial.get("policy"), model)
    reconstructor = _validate_reconstructor_spec(trial.get("reconstructor"))
    requested_count = _positive_integer(
        trial.get("requested_count"), "policy trial requested_count"
    )
    budget_logical_bad_queries = _positive_integer(
        trial.get("budget_logical_bad_queries"),
        "policy trial budget_logical_bad_queries",
    )
    epochs = trial.get("epochs")
    if not isinstance(epochs, list):
        raise SweepBoundaryError("policy trial epochs must be a list")
    normalized_epochs: list[dict[str, Any]] = []
    for index, epoch in enumerate(epochs):
        if not isinstance(epoch, Mapping):
            raise SweepBoundaryError(f"policy trial epoch {index} must be an object")
        _reject_unknown_fields(
            epoch,
            {
                "epoch_index",
                "selected_point_ids",
                "bad_observations",
                "total_prediction_after_feedback",
            },
            f"policy trial epoch {index}",
        )
        if epoch.get("epoch_index") != index:
            raise SweepBoundaryError(f"policy trial epoch {index} index mismatch")
        selected = epoch.get("selected_point_ids")
        observations = epoch.get("bad_observations")
        if not isinstance(selected, list) or not selected:
            raise SweepBoundaryError(
                f"policy trial epoch {index} selected_point_ids must be nonempty"
            )
        if not isinstance(observations, list) or len(observations) != len(selected):
            raise SweepBoundaryError(
                f"policy trial epoch {index} observations do not match selections"
            )
        public_batch = {
            "selected_point_ids": selected,
            "bad_observations": observations,
        }
        _normalize_public_batches([public_batch], model)
        prediction = epoch.get("total_prediction_after_feedback")
        if prediction is not None:
            _normalize_total_prediction(prediction, model)
        normalized_epochs.append(
            {
                "epoch_index": index,
                "selected_point_ids": list(selected),
                "bad_observations": [dict(observation) for observation in observations],
                "total_prediction_after_feedback": prediction,
            }
        )
    return {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_POLICY_TRIAL_SURFACE,
        "sweep_space_sha256": model.sweep_space_sha256,
        "policy": policy,
        "reconstructor": reconstructor,
        "requested_count": requested_count,
        "budget_logical_bad_queries": budget_logical_bad_queries,
        "epochs": normalized_epochs,
    }


def _normalize_total_prediction(
    prediction: Any, model: _SweepModel
) -> tuple[set[str], set[str]]:
    if not isinstance(prediction, Mapping):
        raise SweepBoundaryError("total prediction must be an object")
    _reject_unknown_fields(
        prediction,
        {"pass_point_ids", "fail_point_ids"},
        "total prediction",
    )
    pass_ids = prediction.get("pass_point_ids")
    fail_ids = prediction.get("fail_point_ids")
    if not isinstance(pass_ids, list) or not isinstance(fail_ids, list):
        raise SweepBoundaryError("total prediction point sets must be lists")
    if not all(isinstance(point_id, str) for point_id in pass_ids + fail_ids):
        raise SweepBoundaryError("total prediction point ids must be strings")
    if len(set(pass_ids)) != len(pass_ids) or len(set(fail_ids)) != len(fail_ids):
        raise SweepBoundaryError("total prediction point ids must be unique")
    pass_set = set(pass_ids)
    fail_set = set(fail_ids)
    if pass_set & fail_set:
        raise SweepBoundaryError("total prediction pass/fail sets overlap")
    if pass_set | fail_set != set(model.point_by_id):
        raise SweepBoundaryError("total prediction must classify every sweep point")
    return pass_set, fail_set


def _boundary_edge_set(analysis_revision: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (edge["axis"], edge["pass_point_id"], edge["fail_point_id"])
        for edge in analysis_revision["boundary_edges"]
    }


def _prediction_revision(
    model: _SweepModel, fail_ids: set[str]
) -> dict[str, Any]:
    edges = _grid_edges(model)
    oracle = {point_id: int(point_id in fail_ids) for point_id in model.point_by_id}
    return _analyze_revision(model, oracle, edges)


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    if denominator == 0:
        return {"status": "not_applicable", "numerator": numerator, "denominator": 0}
    return {
        "status": "computed",
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator,
    }


def _boundary_precision_recall(
    true_edges: set[tuple[str, str, str]], predicted_edges: set[tuple[str, str, str]]
) -> dict[str, Any]:
    true_positive = len(true_edges & predicted_edges)
    if predicted_edges:
        precision = _ratio(true_positive, len(predicted_edges))
    elif true_edges:
        precision = {
            "status": "not_applicable",
            "reason": "no_predicted_boundary",
            "numerator": 0,
            "denominator": 0,
        }
    else:
        precision = {
            "status": "not_applicable",
            "reason": "no_predicted_or_true_boundary",
            "numerator": 0,
            "denominator": 0,
        }
    if true_edges:
        recall = _ratio(true_positive, len(true_edges))
    elif predicted_edges:
        recall = {
            "status": "not_applicable",
            "reason": "no_true_boundary",
            "numerator": 0,
            "denominator": 0,
        }
    else:
        recall = {
            "status": "not_applicable",
            "reason": "no_predicted_or_true_boundary",
            "numerator": 0,
            "denominator": 0,
        }
    return {"precision": precision, "recall": recall}


def _failure_region_iou(true_fail_ids: set[str], predicted_fail_ids: set[str]) -> dict[str, Any]:
    union = true_fail_ids | predicted_fail_ids
    if not union:
        return {
            "status": "not_applicable",
            "reason": "no_failure_region",
            "numerator": 0,
            "denominator": 0,
        }
    intersection = true_fail_ids & predicted_fail_ids
    return _ratio(len(intersection), len(union))


def _axis_distance_maps(model: _SweepModel) -> list[list[list[int | None]]]:
    distances: list[list[list[int | None]]] = []
    for axis in model.axes:
        count = len(axis.values)
        matrix: list[list[int | None]] = [
            [None for _ in range(count)] for _ in range(count)
        ]
        for index in range(count):
            matrix[index][index] = 0
        if axis.kind == "ordered":
            for left in range(count):
                for right in range(count):
                    matrix[left][right] = abs(left - right)
        else:
            neighbors = {index: set() for index in range(count)}
            for left, right in axis.adjacent_pairs:
                neighbors[left].add(right)
                neighbors[right].add(left)
            for start in range(count):
                queue = deque([start])
                while queue:
                    current = queue.popleft()
                    for neighbor in sorted(neighbors[current]):
                        if matrix[start][neighbor] is None:
                            matrix[start][neighbor] = (matrix[start][current] or 0) + 1
                            queue.append(neighbor)
        distances.append(matrix)
    return distances


def _point_distance(
    left: _Point,
    right: _Point,
    axis_distances: Sequence[Sequence[Sequence[int | None]]],
) -> int | None:
    total = 0
    for axis_index, (left_index, right_index) in enumerate(
        zip(left.coordinates, right.coordinates)
    ):
        distance = axis_distances[axis_index][left_index][right_index]
        if distance is None:
            return None
        total += distance
    return total


def reconstruct_boundary_prediction(
    sweep_space: Mapping[str, Any],
    reconstructor_spec: Mapping[str, Any],
    completed_public_batches: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Classify the full grid from prior bad observations without ground truth."""

    model = _compile_sweep_space(sweep_space)
    _validate_reconstructor_spec(reconstructor_spec)
    _completed, observed_oracle, _coverage = _normalize_public_batches(
        completed_public_batches, model
    )
    axis_distances = _axis_distance_maps(model)
    pass_point_ids: list[str] = []
    fail_point_ids: list[str] = []
    observed_points = [
        (model.point_by_id[point_id], oracle)
        for point_id, oracle in observed_oracle.items()
    ]
    for point in model.points:
        if point.point_id in observed_oracle:
            prediction = observed_oracle[point.point_id]
        else:
            nearest_distance: int | None = None
            nearest_labels: set[int] = set()
            for observed_point, label in observed_points:
                distance = _point_distance(point, observed_point, axis_distances)
                if distance is None:
                    continue
                if nearest_distance is None or distance < nearest_distance:
                    nearest_distance = distance
                    nearest_labels = {label}
                elif distance == nearest_distance:
                    nearest_labels.add(label)
            prediction = 1 if nearest_labels == {1} else 0
        if prediction:
            fail_point_ids.append(point.point_id)
        else:
            pass_point_ids.append(point.point_id)
    return {
        "pass_point_ids": pass_point_ids,
        "fail_point_ids": fail_point_ids,
    }


def _hausdorff_boundary_distance(
    model: _SweepModel,
    true_edges: set[tuple[str, str, str]],
    predicted_edges: set[tuple[str, str, str]],
) -> dict[str, Any]:
    if not true_edges and not predicted_edges:
        return {"status": "not_applicable", "reason": "no_boundary"}
    if not true_edges or not predicted_edges:
        return {"status": "unbounded"}
    axis_distances = _axis_distance_maps(model)

    def edge_distance(
        left: tuple[str, str, str], right: tuple[str, str, str]
    ) -> int | None:
        pass_distance = _point_distance(
            model.point_by_id[left[1]],
            model.point_by_id[right[1]],
            axis_distances,
        )
        fail_distance = _point_distance(
            model.point_by_id[left[2]],
            model.point_by_id[right[2]],
            axis_distances,
        )
        if pass_distance is None or fail_distance is None:
            return None
        return max(pass_distance, fail_distance)

    directed: list[int] = []
    for source, targets in (
        (true_edges, predicted_edges),
        (predicted_edges, true_edges),
    ):
        for source_edge in source:
            distances = [
                distance
                for target_edge in targets
                if (distance := edge_distance(source_edge, target_edge)) is not None
            ]
            if not distances:
                return {"status": "unbounded"}
            directed.append(min(distances))
    return {"status": "computed", "ordinal_distance": max(directed)}


def _minimal_recovery(true_revision: Mapping[str, Any], predicted_revision: Mapping[str, Any]) -> dict[str, Any]:
    true_minimal = true_revision["minimal_failing_points"]
    predicted_minimal = predicted_revision["minimal_failing_points"]
    if true_minimal["status"] != "computed":
        return {"status": "not_applicable", "reason": "no_ordered_axis"}
    true_ids = set(true_minimal["point_ids"])
    predicted_ids = set(predicted_minimal["point_ids"])
    if not true_ids:
        return {
            "status": "not_applicable",
            "reason": "no_true_minimal_failure",
            "numerator": 0,
            "denominator": 0,
        }
    return _ratio(len(true_ids & predicted_ids), len(true_ids))


def _observed_cost(epoch_index: int, epochs: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(epoch["selected_point_ids"]) for epoch in epochs[: epoch_index + 1])


def _first_violation(epochs: Sequence[Mapping[str, Any]], true_fail_ids: set[str]) -> dict[str, Any]:
    if not true_fail_ids:
        return {"status": "not_applicable", "reason": "no_true_failure"}
    for epoch in epochs:
        if any(
            observation["point_id"] in true_fail_ids
            for observation in epoch["bad_observations"]
        ):
            return {
                "status": "reached",
                "epoch_index": epoch["epoch_index"],
                "logical_bad_queries": _observed_cost(epoch["epoch_index"], epochs),
            }
    return {"status": "not_reached"}


def _observed_bracket(
    model: _SweepModel,
    left_id: str,
    right_id: str,
) -> str | None:
    left = model.point_by_id[left_id]
    right = model.point_by_id[right_id]
    differing = [
        index
        for index, (left_coordinate, right_coordinate) in enumerate(
            zip(left.coordinates, right.coordinates)
        )
        if left_coordinate != right_coordinate
    ]
    if len(differing) != 1:
        return None
    axis_index = differing[0]
    axis = model.axes[axis_index]
    if axis.kind == "ordered":
        return axis.name
    pair = tuple(sorted((left.coordinates[axis_index], right.coordinates[axis_index])))
    if pair in axis.adjacent_pairs:
        return axis.name
    return None


def _first_bracket(
    model: _SweepModel,
    epochs: Sequence[Mapping[str, Any]],
    true_boundary_edges: set[tuple[str, str, str]],
) -> dict[str, Any]:
    if not true_boundary_edges:
        return {"status": "not_applicable", "reason": "no_true_boundary"}
    observed_labels: dict[str, int] = {}
    for epoch in epochs:
        for observation in epoch["bad_observations"]:
            observed_labels[observation["point_id"]] = observation["bad_oracle"]
        observed_items = sorted(observed_labels.items())
        for left_id, left_label in observed_items:
            for right_id, right_label in observed_items:
                if left_id >= right_id or left_label == right_label:
                    continue
                axis = _observed_bracket(model, left_id, right_id)
                if axis is not None:
                    return {
                        "status": "reached",
                        "epoch_index": epoch["epoch_index"],
                        "logical_bad_queries": _observed_cost(
                            epoch["epoch_index"], epochs
                        ),
                        "axis": axis,
                    }
    return {"status": "not_reached"}


def _prediction_metrics_by_epoch(
    model: _SweepModel,
    epochs: Sequence[Mapping[str, Any]],
    true_revision: Mapping[str, Any],
) -> dict[str, Any]:
    true_edges = _boundary_edge_set(true_revision)
    true_fail_ids = set(true_revision["fail_point_ids"])
    epoch_metrics: list[dict[str, Any]] = []
    first_exact_boundary: dict[str, Any] | None = None
    for epoch in epochs:
        prediction = epoch.get("total_prediction_after_feedback")
        if prediction is None:
            epoch_metrics.append(
                {
                    "epoch_index": epoch["epoch_index"],
                    "status": "not_computable",
                    "reason": "missing_total_prediction",
                }
            )
            continue
        _pass_ids, fail_ids = _normalize_total_prediction(prediction, model)
        predicted_revision = _prediction_revision(model, fail_ids)
        predicted_edges = _boundary_edge_set(predicted_revision)
        boundary = _boundary_precision_recall(true_edges, predicted_edges)
        metrics = {
            "epoch_index": epoch["epoch_index"],
            "status": "computed",
            "boundary_precision": boundary["precision"],
            "boundary_recall": boundary["recall"],
            "boundary_hausdorff": _hausdorff_boundary_distance(
                model, true_edges, predicted_edges
            ),
            "failure_region_iou": _failure_region_iou(true_fail_ids, fail_ids),
            "minimal_failing_point_recovery": _minimal_recovery(
                true_revision, predicted_revision
            ),
        }
        if true_edges and predicted_edges == true_edges and first_exact_boundary is None:
            first_exact_boundary = {
                "status": "reached",
                "epoch_index": epoch["epoch_index"],
                "logical_bad_queries": _observed_cost(epoch["epoch_index"], epochs),
            }
        epoch_metrics.append(metrics)
    if first_exact_boundary is None:
        if not true_edges:
            first_exact_boundary = {
                "status": "not_applicable",
                "reason": "no_true_boundary",
            }
        else:
            first_exact_boundary = {"status": "not_reached"}
    return {
        "first_exact_boundary": first_exact_boundary,
        "epochs": epoch_metrics,
    }


def analyze_boundary_policy_trial(
    sweep_space: Mapping[str, Any],
    ground_truth: Mapping[str, Any],
    trial: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay a selector trial and recompute boundary-discovery metrics."""

    model = _compile_sweep_space(sweep_space)
    observations = _normalize_ground_truth(ground_truth, model)
    truth_by_point = {
        observation["point_id"]: observation["bad_oracle"]
        for observation in observations
    }
    analysis = analyze_boundary_ground_truth(sweep_space, ground_truth)
    normalized_trial = _normalize_trial(trial, model)
    completed_batches: list[dict[str, Any]] = []
    logical_bad_queries = 0
    for epoch in normalized_trial["epochs"]:
        remaining_budget = normalized_trial["budget_logical_bad_queries"] - logical_bad_queries
        if remaining_budget <= 0:
            raise SweepBoundaryError("policy trial exceeds logical bad-query budget")
        expected = select_boundary_points(
            sweep_space,
            normalized_trial["policy"],
            completed_batches,
            min(normalized_trial["requested_count"], remaining_budget),
        )
        recorded = tuple(epoch["selected_point_ids"])
        if recorded != expected:
            raise SweepBoundaryError(
                f"policy trial epoch {epoch['epoch_index']} selection is not replayable"
            )
        for observation in epoch["bad_observations"]:
            point_id = observation["point_id"]
            if observation["bad_oracle"] != truth_by_point[point_id]:
                raise SweepBoundaryError(
                    f"policy trial epoch {epoch['epoch_index']} observation mismatches ground truth"
                )
        completed_batches.append(
            {
                "selected_point_ids": epoch["selected_point_ids"],
                "bad_observations": epoch["bad_observations"],
            }
        )
        reconstructed_prediction = reconstruct_boundary_prediction(
            sweep_space,
            normalized_trial["reconstructor"],
            completed_batches,
        )
        declared_prediction = epoch.get("total_prediction_after_feedback")
        if declared_prediction is not None:
            declared_pass, declared_fail = _normalize_total_prediction(
                declared_prediction, model
            )
            if declared_pass != set(reconstructed_prediction["pass_point_ids"]) or (
                declared_fail != set(reconstructed_prediction["fail_point_ids"])
            ):
                raise SweepBoundaryError(
                    f"policy trial epoch {epoch['epoch_index']} prediction is not replayable"
                )
        epoch["total_prediction_after_feedback"] = reconstructed_prediction
        logical_bad_queries += len(epoch["selected_point_ids"])
    if logical_bad_queries > normalized_trial["budget_logical_bad_queries"]:
        raise SweepBoundaryError("policy trial exceeds logical bad-query budget")
    remaining_budget = normalized_trial["budget_logical_bad_queries"] - logical_bad_queries
    exhausted = not select_boundary_points(
        sweep_space,
        normalized_trial["policy"],
        completed_batches,
        min(normalized_trial["requested_count"], remaining_budget)
        if remaining_budget > 0
        else normalized_trial["requested_count"],
    )
    if remaining_budget > 0 and not exhausted:
        raise SweepBoundaryError("policy trial stopped before budget or exhaustion")
    if exhausted:
        completion_status = "exhausted"
    else:
        completion_status = "budget_reached"
    bad_revision = analysis["revisions"]["bad"]
    true_boundary_edges = _boundary_edge_set(bad_revision)
    prediction_metrics = _prediction_metrics_by_epoch(
        model,
        normalized_trial["epochs"],
        bad_revision,
    )
    unique_bad_queries = len(
        {
            point_id
            for epoch in normalized_trial["epochs"]
            for point_id in epoch["selected_point_ids"]
        }
    )
    return {
        "schema_version": RTL_BOUNDARY_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_POLICY_ANALYSIS_SURFACE,
        "sweep_space_sha256": model.sweep_space_sha256,
        "ground_truth_sha256": _sha256(ground_truth),
        "trial_sha256": _sha256(trial),
        "policy": normalized_trial["policy"],
        "reconstructor": normalized_trial["reconstructor"],
        "requested_count": normalized_trial["requested_count"],
        "budget_logical_bad_queries": normalized_trial["budget_logical_bad_queries"],
        "epoch_count": len(normalized_trial["epochs"]),
        "logical_bad_queries": logical_bad_queries,
        "unique_bad_queries": unique_bad_queries,
        "duplicate_bad_queries": logical_bad_queries - unique_bad_queries,
        "completion_status": completion_status,
        "first_violation": _first_violation(
            normalized_trial["epochs"], set(bad_revision["fail_point_ids"])
        ),
        "first_bracket": _first_bracket(
            model, normalized_trial["epochs"], true_boundary_edges
        ),
        "prediction_metrics": prediction_metrics,
    }
