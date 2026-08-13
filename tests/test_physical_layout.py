from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.physical import (  # noqa: E402
    PhysicalProbeError,
    probe_physical_layout,
    validate_layout_observation,
)
from verilator_model_sidecar.semantic import resolve_physical_bindings  # noqa: E402


PRODUCER = "Verilator 5.050 2026-07-01 rev v5.050"
NATIVE_PRODUCER = "Verilator native test"


def _native_id(kind: str, fields: list[tuple[str, str]]) -> str:
    framed = kind.encode()
    for name, value in fields:
        name_bytes = name.encode()
        value_bytes = value.encode()
        framed += str(len(name_bytes)).encode() + b":" + name_bytes
        framed += str(len(value_bytes)).encode() + b":" + value_bytes
    return kind + ":" + hashlib.sha256(framed).hexdigest()


def _adapter() -> dict:
    return {
        "adapter_id": "verilator",
        "source_model": {"prefix": "Vtoy"},
        "target": "toy",
        "signals": {
            "clock": {
                "binding": "Vtoy___024root.clk",
                "direction": "drive",
                "role": "clock",
                "width_bits": 1,
            },
            "done": {
                "binding": "Vtoy__Syms.STATUS.done",
                "direction": "observe",
                "role": "completion",
                "width_bits": 1,
            },
        },
    }


def _coverage_contract() -> dict:
    return {
        "surface": "verilator_toggle_coverage_contract",
        "schema_version": 1,
        "source_model": {"prefix": "Vtoy"},
        "regions": {
            "toggle": {
                "binding": "Vtoy__Syms.TOP.cov",
                "kind": "toggle_direction_counters",
                "word_bits": 32,
            }
        },
    }


def _native_manifest() -> dict:
    instance_id = "rtl_instance:toy"
    storage_binding = {
        "container": "Vtoy___024root",
        "member": "cov",
        "storage": "instance_member",
    }
    storage_id = _native_id(
        "coverage-storage:v1",
        [
            ("semantic_instance_id", instance_id),
            ("container", storage_binding["container"]),
            ("member", storage_binding["member"]),
            ("storage", storage_binding["storage"]),
        ],
    )
    lowering_fields = [
        ("semantic_instance_id", instance_id),
        ("filename", "toy.sv"),
        ("line", "1"),
        ("column", "1"),
        ("hierarchy_suffix", ".toy"),
        ("page", "v_toggle/toy"),
        ("comment", "clk"),
        ("begin", "0"),
        ("end", "0"),
        ("ranged", "false"),
        ("template_ordinal", "0"),
    ]
    lowering_id = _native_id("toggle-lowering:v1", lowering_fields)

    def observation(transition: str) -> dict:
        fields = [
            ("semantic_instance_id", instance_id),
            ("filename", "toy.sv"),
            ("line", "1"),
            ("column", "1"),
            ("hierarchy_suffix", ".toy"),
            ("page", "v_toggle/toy"),
            ("comment", "clk"),
            ("bit_index", "not_applicable"),
            ("transition", transition),
        ]
        return {
            "semantic_id": _native_id("toggle-observation:v1", fields),
            "semantic_instance_id": instance_id,
            "source": {"file": "toy.sv", "line": 1, "column": 1},
            "hierarchy_suffix": ".toy",
            "page": "v_toggle/toy",
            "comment": "clk",
            "bit_index_status": "not_applicable",
            "transition": transition,
        }

    observations = [observation("1->0"), observation("0->1")]
    physical_ids = [
        _native_id(
            "coverage-word:v1",
            [("storage_id", storage_id), ("raw_word_index", str(index))],
        )
        for index in range(2)
    ]
    return {
        "schema_version": 1,
        "surface": "verilator_model_manifest_experimental",
        "producer": NATIVE_PRODUCER,
        "model": {"top": "toy", "prefix": "Vtoy"},
        "field_count": 2,
        "fields": [
            {
                "field_id": "rtl:toy.clk",
                "origin": "rtl",
                "semantic_path": "toy.clk",
                "rtl_name": "clk",
                "width_bits": 1,
                "direction": "INPUT",
                "source": {"file": "toy.sv", "first_line": 1},
                "generated_binding": {
                    "container": "Vtoy___024root",
                    "member": "clk",
                    "storage": "instance_member",
                },
            },
            {
                "field_id": "rtl:status.done",
                "origin": "rtl",
                "semantic_path": "status.done",
                "rtl_name": "done",
                "width_bits": 1,
                "direction": "NONE",
                "source": {"file": "toy.sv", "first_line": 2},
                "generated_binding": {
                    "container": "Vtoy_status",
                    "member": "done",
                    "storage": "instance_member",
                },
            },
        ],
        "instance_count": 2,
        "instances": [
            {
                "instance_id": "rtl_instance:toy",
                "semantic_path": "toy",
                "parent_instance_id": "",
                "is_top": True,
                "module_binding": {"container": "Vtoy___024root"},
                "generated_binding": {
                    "container": "Vtoy__Syms",
                    "member": "TOP",
                    "storage": "instance_member",
                },
            },
            {
                "instance_id": "rtl_instance:toy.status",
                "semantic_path": "toy.status",
                "parent_instance_id": "rtl_instance:toy",
                "is_top": False,
                "module_binding": {"container": "Vtoy_status"},
                "generated_binding": {
                    "container": "Vtoy__Syms",
                    "member": "STATUS",
                    "storage": "instance_member",
                },
            },
        ],
        "coverage": {
            "status": "provided",
            "authority": "verilator_coverage_lowering",
            "kind": "toggle_transition",
            "semantic_id_scheme": "sha256_length_prefixed_utf8_v1",
            "physical_id_scheme": "sha256_length_prefixed_utf8_v1",
            "counter_semantics": {
                "word_bits": 32,
                "cpp_type": "uint32_t",
                "hit": "nonzero_word",
                "alias_aggregation": "logical_or",
                "transition_order": ["1->0", "0->1"],
            },
            "metrics": {
                "toggle_template_count": 1,
                "lowering_declaration_count": 1,
                "semantic_observation_count": 2,
                "semantic_binding_count": 2,
                "storage_count": 1,
                "physical_word_count": 2,
                "aliased_physical_word_count": 0,
                "maximum_semantic_observations_per_physical_word": 1,
                "update_template_count": 1,
                "update_site_count": 1,
                "update_region_count": 1,
                "unsupported_declaration_count": 0,
                "uninstantiated_local_declaration_count": 0,
                "unupdated_physical_word_count": 0,
                "update_only_physical_word_count": 0,
            },
            "storages": [
                {
                    "storage_id": storage_id,
                    "semantic_instance_id": instance_id,
                    "word_bits": 32,
                    "word_count": 2,
                    "generated_binding": storage_binding,
                }
            ],
            "lowering_declarations": [
                {
                    "lowering_id": lowering_id,
                    "semantic_instance_id": instance_id,
                    "storage_id": storage_id,
                    "raw_base_word": 0,
                    "template_ordinal": 0,
                    "source": {"file": "toy.sv", "line": 1, "column": 1},
                    "hierarchy_suffix": ".toy",
                    "page": "v_toggle/toy",
                    "comment": "clk",
                    "range": {"begin": 0, "end": 0, "ranged": False},
                }
            ],
            "semantic_observations": observations,
            "bindings": [
                {
                    "semantic_id": observations[0]["semantic_id"],
                    "lowering_id": lowering_id,
                    "physical_word_id": physical_ids[0],
                },
                {
                    "semantic_id": observations[1]["semantic_id"],
                    "lowering_id": lowering_id,
                    "physical_word_id": physical_ids[1],
                },
            ],
            "physical_words": [
                {
                    "physical_word_id": physical_ids[0],
                    "storage_id": storage_id,
                    "raw_word_index": 0,
                    "alias_group_id": _native_id(
                        "coverage-alias-group:v1",
                        [("member", observations[0]["semantic_id"])],
                    ),
                    "member_count": 1,
                    "hit_aggregation": "direct",
                    "member_semantic_ids": [observations[0]["semantic_id"]],
                },
                {
                    "physical_word_id": physical_ids[1],
                    "storage_id": storage_id,
                    "raw_word_index": 1,
                    "alias_group_id": _native_id(
                        "coverage-alias-group:v1",
                        [("member", observations[1]["semantic_id"])],
                    ),
                    "member_count": 1,
                    "hit_aggregation": "direct",
                    "member_semantic_ids": [observations[1]["semantic_id"]],
                },
            ],
            "update_regions": [
                {
                    "storage_id": storage_id,
                    "raw_base_word": 0,
                    "width_bits": 1,
                    "site_count": 1,
                }
            ],
        },
        "limitations": {
            "generated_storage_instances": "provided",
            "semantic_instance_topology": "not_provided",
            "coverage_mapping": "provided",
        },
    }


def _write_fake_model(root: Path) -> tuple[Path, Path]:
    include_dir = root / "include"
    obj_dir = root / "obj_dir"
    include_dir.mkdir()
    obj_dir.mkdir()
    (include_dir / "verilated.h").write_text("#pragma once\n", encoding="utf-8")
    (obj_dir / "Vtoy___024root.h").write_text(
        "#pragma once\n#include <cstdint>\n"
        "struct Vtoy___024root { uint8_t clk; uint8_t rst; uint32_t cov[2]; };\n",
        encoding="utf-8",
    )
    (obj_dir / "Vtoy_status.h").write_text(
        "#pragma once\n#include <cstdint>\n"
        "struct Vtoy_status { uint8_t done; uint8_t passed; };\n",
        encoding="utf-8",
    )
    (obj_dir / "Vtoy__Syms.h").write_text(
        "#pragma once\n#include <cstdint>\n"
        '#include "Vtoy___024root.h"\n#include "Vtoy_status.h"\n'
        "struct Vtoy__Syms {\n"
        "  uint64_t host;\n"
        "  Vtoy___024root TOP;\n"
        "  Vtoy_status STATUS;\n"
        "};\n",
        encoding="utf-8",
    )
    return obj_dir, include_dir


def _adapter_verification() -> dict:
    signals = []
    for name, contract in sorted(_adapter()["signals"].items()):
        signals.append(
            {
                "name": name,
                "status": "matched",
                "binding": contract["binding"],
                "canonical_name": f"toy.{name}",
                "semantic_entity": {
                    "semantic_id": f"sem:v1:{name}",
                    "width_bits": contract["width_bits"],
                },
            }
        )
    return {
        "status": "matched",
        "model_prefix": "Vtoy",
        "signals": signals,
    }


class PhysicalLayoutTest(unittest.TestCase):
    def test_native_manifest_measures_layout_without_parsing_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obj_dir, include_dir = _write_fake_model(Path(temporary))
            with mock.patch(
                "verilator_model_sidecar.physical._member_types",
                side_effect=AssertionError("legacy header parser used"),
            ):
                observation = probe_physical_layout(
                    obj_dir=obj_dir,
                    adapter=_adapter(),
                    producer=NATIVE_PRODUCER,
                    native_manifest=_native_manifest(),
                    coverage_contract=_coverage_contract(),
                    verilator_include=include_dir,
                )

        validate_layout_observation(observation)
        self.assertEqual(
            observation["binding_authority"],
            "verilator_native_model_manifest",
        )
        self.assertEqual(
            observation["state_image"], {"bytes": 24, "root_offset_bytes": 8}
        )
        bindings = {row["name"]: row for row in observation["bindings"]}
        self.assertEqual(bindings["clock"]["state_offset"], 8)
        self.assertEqual(bindings["done"]["state_offset"], 20)
        self.assertEqual(
            observation["coverage_regions"],
            [
                {
                    "name": "toggle",
                    "binding": "Vtoy__Syms.TOP.cov",
                    "kind": "toggle_direction_counters",
                    "word_bits": 32,
                    "word_count": 2,
                    "state_offset": 12,
                    "size_bytes": 8,
                }
            ],
        )

    def test_measures_known_cpp_layout_without_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obj_dir, include_dir = _write_fake_model(Path(temporary))
            first = probe_physical_layout(
                obj_dir=obj_dir,
                adapter=_adapter(),
                producer=PRODUCER,
                verilator_include=include_dir,
            )
            second = probe_physical_layout(
                obj_dir=obj_dir,
                adapter=_adapter(),
                producer=PRODUCER,
                verilator_include=include_dir,
            )

        self.assertEqual(first, second)
        validate_layout_observation(first)
        self.assertEqual(
            first["binding_authority"], "generated_cpp_header_inference"
        )
        self.assertEqual(first["state_image"], {"bytes": 24, "root_offset_bytes": 8})
        bindings = {row["name"]: row for row in first["bindings"]}
        self.assertEqual(bindings["clock"]["state_offset"], 8)
        self.assertEqual(bindings["clock"]["size_bytes"], 1)
        self.assertEqual(bindings["done"]["state_offset"], 20)
        self.assertEqual(bindings["done"]["size_bytes"], 1)
        serialized = json.dumps(first)
        self.assertNotIn("/tmp/", serialized)
        self.assertNotIn("/home/", serialized)
        tampered = copy.deepcopy(first)
        tampered["bindings"][0]["state_offset"] += 1
        with self.assertRaisesRegex(PhysicalProbeError, "fingerprint mismatch"):
            validate_layout_observation(tampered)

    def test_measures_coverage_array_as_an_explicit_region(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obj_dir, include_dir = _write_fake_model(Path(temporary))
            observation = probe_physical_layout(
                obj_dir=obj_dir,
                adapter=_adapter(),
                coverage_contract=_coverage_contract(),
                producer=PRODUCER,
                verilator_include=include_dir,
            )

        validate_layout_observation(observation)
        self.assertEqual(observation["coverage_region_count"], 1)
        self.assertEqual(
            observation["coverage_regions"],
            [
                {
                    "name": "toggle",
                    "binding": "Vtoy__Syms.TOP.cov",
                    "kind": "toggle_direction_counters",
                    "word_bits": 32,
                    "word_count": 2,
                    "state_offset": 12,
                    "size_bytes": 8,
                }
            ],
        )

    def test_joins_semantic_ids_and_fails_closed_on_oracle_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obj_dir, include_dir = _write_fake_model(Path(temporary))
            observation = probe_physical_layout(
                obj_dir=obj_dir,
                adapter=_adapter(),
                producer=PRODUCER,
                verilator_include=include_dir,
            )
        headers = observation["headers"]
        oracle = {
            "surface": "verilator_physical_binding_oracle",
            "schema_version": 1,
            "model_prefix": "Vtoy",
            "headers": headers,
            "state_image": observation["state_image"],
            "bindings": {
                row["name"]: {
                    "state_offset": row["state_offset"],
                    "size_bytes": row["size_bytes"],
                }
                for row in observation["bindings"]
            },
        }
        verified = resolve_physical_bindings(
            adapter_verification=_adapter_verification(),
            layout_observation=observation,
            actual_headers=headers,
            producer=PRODUCER,
            oracle=oracle,
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["verified_count"], 2)
        self.assertEqual(verified["mismatch_count"], 0)

        drifted = copy.deepcopy(oracle)
        drifted["bindings"]["done"]["state_offset"] += 1
        mismatch = resolve_physical_bindings(
            adapter_verification=_adapter_verification(),
            layout_observation=observation,
            actual_headers=headers,
            producer=PRODUCER,
            oracle=drifted,
        )
        self.assertEqual(mismatch["status"], "mismatch")
        self.assertEqual(mismatch["mismatch_count"], 1)
        done = {row["name"]: row for row in mismatch["bindings"]}["done"]
        self.assertEqual(done["issues"], ["oracle_state_offset_mismatch"])

    def test_rejects_unsupported_generated_binding(self) -> None:
        adapter = _adapter()
        adapter["signals"]["clock"]["binding"] = "Vtoy___024root.bad.field"
        with tempfile.TemporaryDirectory() as temporary:
            obj_dir, include_dir = _write_fake_model(Path(temporary))
            with self.assertRaisesRegex(PhysicalProbeError, "unsupported root"):
                probe_physical_layout(
                    obj_dir=obj_dir,
                    adapter=adapter,
                    producer=PRODUCER,
                    verilator_include=include_dir,
                )


if __name__ == "__main__":
    unittest.main()
