"""Validate and project Verilator-native toggle coverage lowering metadata."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from .native import NativeManifestError, validate_native_manifest


NATIVE_COVERAGE_AUTHORITY = "verilator_coverage_lowering"
NATIVE_COVERAGE_KIND = "toggle_transition"
NATIVE_COVERAGE_ID_SCHEME = "sha256_length_prefixed_utf8_v1"


class NativeCoverageError(ValueError):
    """Raised when a native manifest cannot prove its coverage lowering graph."""


def _framed_id(kind: str, fields: list[tuple[str, str]]) -> str:
    framed = kind.encode("utf-8")
    for name, value in fields:
        name_bytes = name.encode("utf-8")
        value_bytes = value.encode("utf-8")
        framed += str(len(name_bytes)).encode("ascii") + b":" + name_bytes
        framed += str(len(value_bytes)).encode("ascii") + b":" + value_bytes
    return kind + ":" + hashlib.sha256(framed).hexdigest()


def _records(value: object, name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise NativeCoverageError(f"{name} must be an array of objects")
    return list(value)


def _text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise NativeCoverageError(f"{name} must be {qualifier}")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise NativeCoverageError(f"{name} must be an integer >= {minimum}")
    return value


def _generated_binding(row: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    binding = row.get("generated_binding")
    if not isinstance(binding, Mapping):
        raise NativeCoverageError(f"{name}.generated_binding must be an object")
    return binding


def _toggle_metadata(
    row: Mapping[str, Any], name: str
) -> tuple[str, int, int, str, str, str]:
    source = row.get("source")
    if not isinstance(source, Mapping):
        raise NativeCoverageError(f"{name}.source must be an object")
    return (
        _text(source.get("file"), f"{name}.source.file"),
        _integer(source.get("line"), f"{name}.source.line", minimum=1),
        _integer(source.get("column"), f"{name}.source.column", minimum=1),
        _text(row.get("hierarchy_suffix"), f"{name}.hierarchy_suffix", allow_empty=True),
        _text(row.get("page"), f"{name}.page", allow_empty=True),
        _text(row.get("comment"), f"{name}.comment", allow_empty=True),
    )


def _storage_id(
    semantic_instance_id: str,
    container: str,
    member: str,
    storage: str,
) -> str:
    return _framed_id(
        "coverage-storage:v1",
        [
            ("semantic_instance_id", semantic_instance_id),
            ("container", container),
            ("member", member),
            ("storage", storage),
        ],
    )


def _physical_word_id(storage_id: str, raw_word_index: int) -> str:
    return _framed_id(
        "coverage-word:v1",
        [
            ("storage_id", storage_id),
            ("raw_word_index", str(raw_word_index)),
        ],
    )


def _alias_group_id(members: list[str]) -> str:
    return _framed_id(
        "coverage-alias-group:v1", [("member", member) for member in members]
    )


def _storage_access_binding(
    storage: Mapping[str, Any],
    instances: Mapping[str, Mapping[str, Any]],
) -> str:
    storage_name = "native coverage storage"
    semantic_instance_id = _text(
        storage.get("semantic_instance_id"),
        f"{storage_name}.semantic_instance_id",
        allow_empty=True,
    )
    binding = _generated_binding(storage, storage_name)
    container = _text(binding.get("container"), f"{storage_name}.container")
    member = _text(binding.get("member"), f"{storage_name}.member")
    storage_kind = _text(binding.get("storage"), f"{storage_name}.storage")
    if storage_kind == "symbol_table_member":
        if semantic_instance_id:
            raise NativeCoverageError(
                "symbol-table coverage storage has a semantic instance"
            )
        return f"{container}.{member}"
    if storage_kind != "instance_member" or not semantic_instance_id:
        raise NativeCoverageError("native coverage storage kind is unsupported")
    instance = instances.get(semantic_instance_id)
    if instance is None:
        raise NativeCoverageError("native coverage storage instance is unresolved")
    module_binding = instance.get("module_binding")
    if not isinstance(module_binding, Mapping) or module_binding.get("container") != container:
        raise NativeCoverageError(
            "native coverage storage container does not match its instance"
        )
    instance_binding = _generated_binding(instance, "native coverage instance")
    if instance_binding.get("storage") != "instance_member":
        raise NativeCoverageError("native coverage instance storage is unsupported")
    state_container = _text(
        instance_binding.get("container"), "native coverage instance container"
    )
    instance_member = _text(
        instance_binding.get("member"), "native coverage instance member"
    )
    return f"{state_container}.{instance_member}.{member}"


def _validate_metrics(
    metrics: Mapping[str, Any],
    *,
    storages: list[Mapping[str, Any]],
    lowerings: list[Mapping[str, Any]],
    observations: list[Mapping[str, Any]],
    bindings: list[Mapping[str, Any]],
    physical_words: list[Mapping[str, Any]],
    update_regions: list[Mapping[str, Any]],
    update_site_count: int,
    aliased_word_count: int,
    maximum_members: int,
) -> None:
    expected = {
        "lowering_declaration_count": len(lowerings),
        "semantic_observation_count": len(observations),
        "semantic_binding_count": len(bindings),
        "storage_count": len(storages),
        "physical_word_count": len(physical_words),
        "aliased_physical_word_count": aliased_word_count,
        "maximum_semantic_observations_per_physical_word": maximum_members,
        "update_site_count": update_site_count,
        "update_region_count": len(update_regions),
        "unsupported_declaration_count": 0,
        "uninstantiated_local_declaration_count": 0,
        "unupdated_physical_word_count": 0,
        "update_only_physical_word_count": 0,
    }
    for name, value in expected.items():
        if metrics.get(name) != value:
            raise NativeCoverageError(f"native coverage metric {name} is inconsistent")
    for name in ("toggle_template_count", "update_template_count"):
        _integer(metrics.get(name), f"native coverage metric {name}", minimum=1)


def project_native_toggle_coverage(
    manifest: Mapping[str, Any], *, expected_model_prefix: str
) -> dict[str, Any]:
    """Return a normalized, fail-closed view of compiler-owned toggle lowering."""

    try:
        validate_native_manifest(manifest)
    except NativeManifestError as error:
        raise NativeCoverageError(str(error)) from error
    native_producer = _text(manifest.get("producer"), "native manifest producer")
    model = manifest["model"]
    assert isinstance(model, Mapping)
    if model.get("prefix") != expected_model_prefix:
        raise NativeCoverageError("native coverage model prefix mismatch")
    coverage = manifest.get("coverage")
    if not isinstance(coverage, Mapping):
        raise NativeCoverageError("native manifest has no coverage object")
    if coverage.get("status") != "provided":
        raise NativeCoverageError("native toggle coverage mapping is not provided")
    if coverage.get("authority") != NATIVE_COVERAGE_AUTHORITY:
        raise NativeCoverageError("native coverage authority is unsupported")
    if coverage.get("kind") != NATIVE_COVERAGE_KIND:
        raise NativeCoverageError("native coverage kind is unsupported")
    if (
        coverage.get("semantic_id_scheme") != NATIVE_COVERAGE_ID_SCHEME
        or coverage.get("physical_id_scheme") != NATIVE_COVERAGE_ID_SCHEME
    ):
        raise NativeCoverageError("native coverage identity scheme is unsupported")
    limitations = manifest.get("limitations")
    if not isinstance(limitations, Mapping) or limitations.get("coverage_mapping") != "provided":
        raise NativeCoverageError("native coverage limitation status is inconsistent")
    counter_semantics = coverage.get("counter_semantics")
    if not isinstance(counter_semantics, Mapping):
        raise NativeCoverageError("native coverage counter semantics are absent")
    if (
        counter_semantics.get("word_bits") != 32
        or counter_semantics.get("cpp_type")
        not in {"uint32_t", "std::atomic<uint32_t>"}
        or counter_semantics.get("hit") != "nonzero_word"
        or counter_semantics.get("alias_aggregation") != "logical_or"
        or counter_semantics.get("transition_order") != ["1->0", "0->1"]
    ):
        raise NativeCoverageError("native coverage counter semantics are unsupported")

    instances = _records(manifest.get("instances"), "native manifest instances")
    instance_by_id = {
        _text(instance.get("instance_id"), "native coverage instance ID"): instance
        for instance in instances
    }
    storages = _records(coverage.get("storages"), "native coverage storages")
    storage_by_id: dict[str, Mapping[str, Any]] = {}
    normalized_storages: list[dict[str, Any]] = []
    for storage in storages:
        storage_id = _text(storage.get("storage_id"), "native coverage storage ID")
        if storage_id in storage_by_id:
            raise NativeCoverageError("native coverage storage IDs are not unique")
        storage_by_id[storage_id] = storage
        word_count = _integer(
            storage.get("word_count"), "native coverage storage word_count", minimum=1
        )
        if storage.get("word_bits") != 32:
            raise NativeCoverageError("native coverage storage word width is unsupported")
        semantic_instance_id = _text(
            storage.get("semantic_instance_id"),
            "native coverage storage semantic instance ID",
            allow_empty=True,
        )
        generated = _generated_binding(storage, "native coverage storage")
        container = _text(
            generated.get("container"), "native coverage storage container"
        )
        member = _text(generated.get("member"), "native coverage storage member")
        storage_kind = _text(
            generated.get("storage"), "native coverage storage kind"
        )
        if storage_id != _storage_id(
            semantic_instance_id, container, member, storage_kind
        ):
            raise NativeCoverageError("native coverage storage identity is invalid")
        normalized_storages.append(
            {
                "storage_id": storage_id,
                "semantic_instance_id": semantic_instance_id,
                "binding": _storage_access_binding(storage, instance_by_id),
                "word_bits": 32,
                "word_count": word_count,
                "generated_binding": dict(generated),
            }
        )

    lowerings = _records(
        coverage.get("lowering_declarations"), "native coverage lowerings"
    )
    lowering_by_id: dict[str, Mapping[str, Any]] = {}
    lowering_locations: dict[str, tuple[str, int, int]] = {}
    lowering_metadata: dict[
        str, tuple[str, str, int, int, str, str, str, int, int, bool]
    ] = {}
    ordinals: set[tuple[str, int]] = set()
    for lowering in lowerings:
        lowering_id = _text(lowering.get("lowering_id"), "native coverage lowering ID")
        if lowering_id in lowering_by_id:
            raise NativeCoverageError("native coverage lowering IDs are not unique")
        lowering_by_id[lowering_id] = lowering
        storage_id = _text(lowering.get("storage_id"), "native coverage lowering storage ID")
        storage = storage_by_id.get(storage_id)
        if storage is None:
            raise NativeCoverageError("native coverage lowering storage is unresolved")
        if lowering.get("semantic_instance_id") != storage.get("semantic_instance_id"):
            raise NativeCoverageError("native coverage lowering instance/storage mismatch")
        range_record = lowering.get("range")
        if not isinstance(range_record, Mapping):
            raise NativeCoverageError("native coverage lowering metadata is malformed")
        begin = _integer(range_record.get("begin"), "native coverage range begin", minimum=-2**31)
        end = _integer(range_record.get("end"), "native coverage range end", minimum=-2**31)
        if not isinstance(range_record.get("ranged"), bool):
            raise NativeCoverageError("native coverage ranged marker must be boolean")
        raw_base = _integer(lowering.get("raw_base_word"), "native coverage raw base")
        width = abs(end - begin) + 1
        if raw_base + 2 * width > storage.get("word_count", 0):
            raise NativeCoverageError("native coverage lowering lies outside storage")
        ordinal = _integer(lowering.get("template_ordinal"), "native coverage template ordinal")
        ordinal_key = (storage_id, ordinal)
        if ordinal_key in ordinals:
            raise NativeCoverageError(
                "native coverage template ordinals are not unique per storage"
            )
        ordinals.add(ordinal_key)
        filename, line, column, hierarchy, page, comment = _toggle_metadata(
            lowering, "native coverage lowering"
        )
        semantic_instance_id = _text(
            lowering.get("semantic_instance_id"),
            "native coverage lowering semantic instance ID",
            allow_empty=True,
        )
        expected_lowering_id = _framed_id(
            "toggle-lowering:v1",
            [
                ("semantic_instance_id", semantic_instance_id),
                ("filename", filename),
                ("line", str(line)),
                ("column", str(column)),
                ("hierarchy_suffix", hierarchy),
                ("page", page),
                ("comment", comment),
                ("begin", str(begin)),
                ("end", str(end)),
                ("ranged", str(range_record["ranged"]).lower()),
                ("template_ordinal", str(ordinal)),
            ],
        )
        if lowering_id != expected_lowering_id:
            raise NativeCoverageError("native coverage lowering identity is invalid")
        lowering_locations[lowering_id] = (storage_id, raw_base, width)
        lowering_metadata[lowering_id] = (
            semantic_instance_id,
            filename,
            line,
            column,
            hierarchy,
            page,
            comment,
            begin,
            end,
            bool(range_record["ranged"]),
        )

    observations = _records(
        coverage.get("semantic_observations"), "native coverage observations"
    )
    observation_ids: list[str] = []
    observation_metadata: dict[
        str, tuple[str, str, int, int, str, str, str, int | None, str]
    ] = {}
    for observation in observations:
        semantic_id = _text(
            observation.get("semantic_id"), "native coverage semantic ID"
        )
        semantic_instance_id = _text(
            observation.get("semantic_instance_id"),
            "native coverage observation semantic instance ID",
            allow_empty=True,
        )
        filename, line, column, hierarchy, page, comment = _toggle_metadata(
            observation, "native coverage observation"
        )
        transition = _text(
            observation.get("transition"), "native coverage transition"
        )
        if transition not in {"1->0", "0->1"}:
            raise NativeCoverageError("native coverage transition is unsupported")
        if "bit_index" in observation:
            bit_index: int | None = _integer(
                observation.get("bit_index"),
                "native coverage observation bit index",
                minimum=-2**31,
            )
            if "bit_index_status" in observation:
                raise NativeCoverageError(
                    "native coverage observation has two bit-index representations"
                )
            id_bit_index = str(bit_index)
        else:
            bit_index = None
            if observation.get("bit_index_status") != "not_applicable":
                raise NativeCoverageError(
                    "native scalar coverage observation has no bit-index status"
                )
            id_bit_index = "not_applicable"
        expected_semantic_id = _framed_id(
            "toggle-observation:v1",
            [
                ("semantic_instance_id", semantic_instance_id),
                ("filename", filename),
                ("line", str(line)),
                ("column", str(column)),
                ("hierarchy_suffix", hierarchy),
                ("page", page),
                ("comment", comment),
                ("bit_index", id_bit_index),
                ("transition", transition),
            ],
        )
        if semantic_id != expected_semantic_id:
            raise NativeCoverageError("native coverage semantic identity is invalid")
        observation_ids.append(semantic_id)
        observation_metadata[semantic_id] = (
            semantic_instance_id,
            filename,
            line,
            column,
            hierarchy,
            page,
            comment,
            bit_index,
            transition,
        )
    if len(observation_ids) != len(set(observation_ids)):
        raise NativeCoverageError("native coverage semantic IDs are not unique")
    observation_id_set = set(observation_ids)

    physical_words = _records(
        coverage.get("physical_words"), "native coverage physical words"
    )
    physical_by_id: dict[str, Mapping[str, Any]] = {}
    location_by_physical_id: dict[str, tuple[str, int]] = {}
    physical_ids_by_location: dict[tuple[str, int], str] = {}
    for word in physical_words:
        physical_id = _text(
            word.get("physical_word_id"), "native coverage physical word ID"
        )
        storage_id = _text(word.get("storage_id"), "native physical word storage ID")
        storage = storage_by_id.get(storage_id)
        raw_index = _integer(word.get("raw_word_index"), "native physical word index")
        if storage is None or raw_index >= storage.get("word_count", 0):
            raise NativeCoverageError("native coverage physical word lies outside storage")
        if physical_id != _physical_word_id(storage_id, raw_index):
            raise NativeCoverageError("native coverage physical word identity is invalid")
        location = (storage_id, raw_index)
        if physical_id in physical_by_id or location in physical_ids_by_location:
            raise NativeCoverageError("native coverage physical words are not unique")
        physical_by_id[physical_id] = word
        location_by_physical_id[physical_id] = location
        physical_ids_by_location[location] = physical_id

    bindings = _records(coverage.get("bindings"), "native coverage bindings")
    members_by_physical: dict[str, set[str]] = defaultdict(set)
    binding_keys: set[tuple[str, str, str]] = set()
    referenced_lowerings: set[str] = set()
    for binding in bindings:
        semantic_id = _text(binding.get("semantic_id"), "native binding semantic ID")
        lowering_id = _text(binding.get("lowering_id"), "native binding lowering ID")
        physical_id = _text(
            binding.get("physical_word_id"), "native binding physical word ID"
        )
        if semantic_id not in observation_id_set or lowering_id not in lowering_by_id:
            raise NativeCoverageError("native coverage binding has an unresolved semantic edge")
        if physical_id not in physical_by_id:
            raise NativeCoverageError("native coverage binding has an unresolved physical edge")
        lowering_storage, lowering_base, lowering_width = lowering_locations[lowering_id]
        physical_storage, physical_index = location_by_physical_id[physical_id]
        if lowering_storage != physical_storage:
            raise NativeCoverageError("native coverage binding crosses storages")
        if not lowering_base <= physical_index < lowering_base + 2 * lowering_width:
            raise NativeCoverageError("native coverage binding lies outside its lowering")
        (
            lowering_instance,
            lowering_filename,
            lowering_line,
            lowering_column,
            lowering_hierarchy,
            lowering_page,
            lowering_comment,
            lowering_begin,
            lowering_end,
            lowering_ranged,
        ) = lowering_metadata[lowering_id]
        (
            observation_instance,
            observation_filename,
            observation_line,
            observation_column,
            observation_hierarchy,
            observation_page,
            observation_comment,
            observation_bit_index,
            observation_transition,
        ) = observation_metadata[semantic_id]
        if (
            lowering_instance,
            lowering_filename,
            lowering_line,
            lowering_column,
            lowering_hierarchy,
            lowering_page,
            lowering_comment,
        ) != (
            observation_instance,
            observation_filename,
            observation_line,
            observation_column,
            observation_hierarchy,
            observation_page,
            observation_comment,
        ):
            raise NativeCoverageError(
                "native coverage binding crosses semantic metadata"
            )
        if lowering_ranged:
            if observation_bit_index is None:
                raise NativeCoverageError(
                    "native ranged coverage binding has no bit index"
                )
            step = 1 if lowering_end >= lowering_begin else -1
            bit_ordinal = (observation_bit_index - lowering_begin) * step
            if bit_ordinal < 0 or bit_ordinal >= lowering_width:
                raise NativeCoverageError(
                    "native coverage observation bit lies outside its lowering"
                )
        else:
            if observation_bit_index is not None or lowering_width != 1:
                raise NativeCoverageError(
                    "native scalar coverage binding has a ranged bit index"
                )
            bit_ordinal = 0
        direction_offset = 0 if observation_transition == "1->0" else 1
        if physical_index != lowering_base + bit_ordinal * 2 + direction_offset:
            raise NativeCoverageError(
                "native coverage physical word does not match its observation"
            )
        key = (semantic_id, lowering_id, physical_id)
        if key in binding_keys:
            raise NativeCoverageError("native coverage bindings are duplicated")
        binding_keys.add(key)
        referenced_lowerings.add(lowering_id)
        members_by_physical[physical_id].add(semantic_id)
    if referenced_lowerings != set(lowering_by_id):
        raise NativeCoverageError("native coverage lowerings are not all bound")
    if set(members_by_physical) != set(physical_by_id):
        raise NativeCoverageError("native coverage physical words are not all bound")
    if set().union(*members_by_physical.values()) != observation_id_set:
        raise NativeCoverageError("native coverage observations are not all bound")

    aliased_word_count = 0
    maximum_members = 0
    for physical_id, word in physical_by_id.items():
        members = sorted(members_by_physical[physical_id])
        declared_members = word.get("member_semantic_ids")
        if (
            declared_members != members
            or word.get("member_count") != len(members)
            or word.get("alias_group_id") != _alias_group_id(members)
            or word.get("hit_aggregation")
            != ("direct" if len(members) == 1 else "logical_or_alias")
        ):
            raise NativeCoverageError("native coverage physical alias set is inconsistent")
        aliased_word_count += len(members) > 1
        maximum_members = max(maximum_members, len(members))

    update_regions = _records(
        coverage.get("update_regions"), "native coverage update regions"
    )
    updated_locations: set[tuple[str, int]] = set()
    update_keys: set[tuple[str, int, int]] = set()
    update_site_count = 0
    for region in update_regions:
        storage_id = _text(region.get("storage_id"), "native update storage ID")
        storage = storage_by_id.get(storage_id)
        raw_base = _integer(region.get("raw_base_word"), "native update raw base")
        width = _integer(region.get("width_bits"), "native update width", minimum=1)
        site_count = _integer(region.get("site_count"), "native update site count", minimum=1)
        if storage is None or raw_base + 2 * width > storage.get("word_count", 0):
            raise NativeCoverageError("native coverage update lies outside storage")
        key = (storage_id, raw_base, width)
        if key in update_keys:
            raise NativeCoverageError("native coverage update regions are duplicated")
        update_keys.add(key)
        update_site_count += site_count
        updated_locations.update(
            (storage_id, raw_base + offset) for offset in range(2 * width)
        )
    if updated_locations != set(physical_ids_by_location):
        raise NativeCoverageError("native coverage update closure is incomplete")

    metrics = coverage.get("metrics")
    if not isinstance(metrics, Mapping):
        raise NativeCoverageError("native coverage metrics must be an object")
    _validate_metrics(
        metrics,
        storages=storages,
        lowerings=lowerings,
        observations=observations,
        bindings=bindings,
        physical_words=physical_words,
        update_regions=update_regions,
        update_site_count=update_site_count,
        aliased_word_count=aliased_word_count,
        maximum_members=maximum_members,
    )
    return {
        "producer": native_producer,
        "authority": NATIVE_COVERAGE_AUTHORITY,
        "transition_order": list(counter_semantics["transition_order"]),
        "counter_stride_words_per_bit": 2,
        "storages": sorted(normalized_storages, key=lambda row: str(row["storage_id"])),
        "lowering_declarations": sorted(
            (dict(row) for row in lowerings),
            key=lambda row: (str(row["storage_id"]), int(row["template_ordinal"])),
        ),
        "update_regions": sorted(
            (dict(row) for row in update_regions),
            key=lambda row: (
                str(row["storage_id"]),
                int(row["raw_base_word"]),
                int(row["width_bits"]),
            ),
        ),
    }
