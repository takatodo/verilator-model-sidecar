from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.coverage import (  # noqa: E402
    CoverageMappingError,
    _expression_descriptor,
    _label_matches_expression,
    build_toggle_coverage_mapping,
    validate_coverage_mapping,
)


PRODUCER = "Verilator 5.050 2026-07-01 rev v5.050"


def _tree_and_meta() -> tuple[dict, dict, dict]:
    dtype_bus = {
        "type": "BASICDTYPE",
        "name": "logic",
        "addr": "(D1)",
        "keyword": "logic",
        "range": "1:0",
    }
    dtype_bit = {
        "type": "BASICDTYPE",
        "name": "logic",
        "addr": "(D2)",
        "keyword": "logic",
    }
    declarations = [
        {
            "type": "COVERTOGGLEDECL",
            "addr": "(C1)",
            "loc": "e,5:7,5:10",
            "page": "v_toggle/toy",
            "binNum": 0,
        },
        {
            "type": "COVERTOGGLEDECL",
            "addr": "(C2)",
            "loc": "e,6:7,6:12",
            "page": "v_toggle/toy",
            "binNum": 0,
        },
    ]
    toggles = [
        {
            "type": "COVERTOGGLE",
            "incp": [{"type": "COVERINC", "declp": "(C1)"}],
            "origp": [
                {
                    "type": "VARREF",
                    "name": "bus",
                    "dtypep": "(D1)",
                    "access": "RD",
                }
            ],
            "changep": [],
        },
        {
            "type": "COVERTOGGLE",
            "incp": [{"type": "COVERINC", "declp": "(C2)"}],
            "origp": [
                {
                    "type": "VARREF",
                    "name": "alias",
                    "dtypep": "(D2)",
                    "access": "RD",
                }
            ],
            "changep": [],
        },
    ]
    tree = {
        "type": "NETLIST",
        "modulesp": [
            {
                "type": "MODULE",
                "name": "toy",
                "origName": "toy",
                "addr": "(M1)",
                "stmtsp": [*declarations, *toggles],
            }
        ],
        "miscsp": [{"type": "TYPETABLE", "typesp": [dtype_bus, dtype_bit]}],
    }
    meta = {
        "files": {
            "e": {
                "filename": "rtl/toy.sv",
                "language": "1800-2023",
            }
        }
    }
    hierarchy = {
        "instances": [
            {
                "instance_id": "instance:v1:toy",
                "canonical_path": "toy",
                "module_definition_id": "module:v1:toy",
                "module": "toy",
                "original_module": "toy",
            }
        ]
    }
    return tree, meta, hierarchy


def _contract() -> dict:
    return {
        "surface": "verilator_toggle_coverage_contract",
        "schema_version": 1,
        "source_model": {"prefix": "Vtoy"},
        "regions": {
            "toggle": {
                "binding": "Vtoy__Syms.TOP.__Vcoverage",
                "kind": "toggle_direction_counters",
                "word_bits": 32,
            }
        },
    }


def _layout() -> dict:
    return {
        "model_prefix": "Vtoy",
        "coverage_region_count": 1,
        "coverage_regions": [
            {
                "name": "toggle",
                "binding": "Vtoy__Syms.TOP.__Vcoverage",
                "kind": "toggle_direction_counters",
                "word_bits": 32,
                "word_count": 4,
                "state_offset": 100,
                "size_bytes": 16,
            }
        ],
    }


def _native_id(kind: str, fields: list[tuple[str, str]]) -> str:
    framed = kind.encode()
    for name, value in fields:
        name_bytes = name.encode()
        value_bytes = value.encode()
        framed += str(len(name_bytes)).encode() + b":" + name_bytes
        framed += str(len(value_bytes)).encode() + b":" + value_bytes
    return kind + ":" + hashlib.sha256(framed).hexdigest()


def _native_manifest(*, alias_comment: str = "alias") -> dict:
    instance_id = "rtl_instance:toy"
    storage_binding = {
        "container": "Vtoy___024root",
        "member": "__Vcoverage",
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

    def lowering(
        *, line: int, comment: str, begin: int, end: int, ranged: bool,
        raw_base_word: int, template_ordinal: int
    ) -> dict:
        fields = [
            ("semantic_instance_id", instance_id),
            ("filename", "rtl/toy.sv"),
            ("line", str(line)),
            ("column", "7"),
            ("hierarchy_suffix", ".toy"),
            ("page", "v_toggle/toy"),
            ("comment", comment),
            ("begin", str(begin)),
            ("end", str(end)),
            ("ranged", str(ranged).lower()),
            ("template_ordinal", str(template_ordinal)),
        ]
        return {
            "lowering_id": _native_id("toggle-lowering:v1", fields),
            "semantic_instance_id": instance_id,
            "storage_id": storage_id,
            "raw_base_word": raw_base_word,
            "template_ordinal": template_ordinal,
            "source": {"file": "rtl/toy.sv", "line": line, "column": 7},
            "hierarchy_suffix": ".toy",
            "page": "v_toggle/toy",
            "comment": comment,
            "range": {"begin": begin, "end": end, "ranged": ranged},
        }

    bus_lowering = lowering(
        line=5, comment="bus", begin=0, end=1, ranged=True,
        raw_base_word=0, template_ordinal=0
    )
    alias_lowering = lowering(
        line=6, comment=alias_comment, begin=0, end=0, ranged=False,
        raw_base_word=2, template_ordinal=1
    )

    def observation(
        lowering_record: dict, *, bit_index: int | None, transition: str
    ) -> dict:
        source = lowering_record["source"]
        fields = [
            ("semantic_instance_id", instance_id),
            ("filename", source["file"]),
            ("line", str(source["line"])),
            ("column", str(source["column"])),
            ("hierarchy_suffix", lowering_record["hierarchy_suffix"]),
            ("page", lowering_record["page"]),
            ("comment", lowering_record["comment"]),
            ("bit_index", "not_applicable" if bit_index is None else str(bit_index)),
            ("transition", transition),
        ]
        row = {
            "semantic_id": _native_id("toggle-observation:v1", fields),
            "semantic_instance_id": instance_id,
            "source": dict(source),
            "hierarchy_suffix": lowering_record["hierarchy_suffix"],
            "page": lowering_record["page"],
            "comment": lowering_record["comment"],
            "transition": transition,
        }
        if bit_index is None:
            row["bit_index_status"] = "not_applicable"
        else:
            row["bit_index"] = bit_index
        return row

    observations = [
        observation(bus_lowering, bit_index=0, transition="1->0"),
        observation(bus_lowering, bit_index=0, transition="0->1"),
        observation(bus_lowering, bit_index=1, transition="1->0"),
        observation(bus_lowering, bit_index=1, transition="0->1"),
        observation(alias_lowering, bit_index=None, transition="1->0"),
        observation(alias_lowering, bit_index=None, transition="0->1"),
    ]
    physical_ids = [
        _native_id(
            "coverage-word:v1",
            [("storage_id", storage_id), ("raw_word_index", str(index))],
        )
        for index in range(4)
    ]
    binding_rows = (
        (observations[0], bus_lowering, 0),
        (observations[1], bus_lowering, 1),
        (observations[2], bus_lowering, 2),
        (observations[3], bus_lowering, 3),
        (observations[4], alias_lowering, 2),
        (observations[5], alias_lowering, 3),
    )
    bindings = [
        {
            "semantic_id": observation_record["semantic_id"],
            "lowering_id": lowering_record["lowering_id"],
            "physical_word_id": physical_ids[word_index],
        }
        for observation_record, lowering_record, word_index in binding_rows
    ]
    members_by_word = (
        [observations[0]["semantic_id"]],
        [observations[1]["semantic_id"]],
        [observations[2]["semantic_id"], observations[4]["semantic_id"]],
        [observations[3]["semantic_id"], observations[5]["semantic_id"]],
    )
    physical_words = []
    for index, raw_members in enumerate(members_by_word):
        members = sorted(raw_members)
        physical_words.append(
            {
                "physical_word_id": physical_ids[index],
                "storage_id": storage_id,
                "raw_word_index": index,
                "alias_group_id": _native_id(
                    "coverage-alias-group:v1",
                    [("member", member) for member in members],
                ),
                "member_count": len(members),
                "hit_aggregation": (
                    "direct" if len(members) == 1 else "logical_or_alias"
                ),
                "member_semantic_ids": members,
            }
        )
    return {
        "schema_version": 1,
        "surface": "verilator_model_manifest_experimental",
        "producer": "Verilator native coverage test",
        "model": {"top": "toy", "prefix": "Vtoy"},
        "field_count": 0,
        "fields": [],
        "instance_count": 1,
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
            }
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
                "toggle_template_count": 2,
                "lowering_declaration_count": 2,
                "semantic_observation_count": 6,
                "semantic_binding_count": 6,
                "storage_count": 1,
                "physical_word_count": 4,
                "aliased_physical_word_count": 2,
                "maximum_semantic_observations_per_physical_word": 2,
                "update_template_count": 2,
                "update_site_count": 2,
                "update_region_count": 2,
                "unsupported_declaration_count": 0,
                "uninstantiated_local_declaration_count": 0,
                "unupdated_physical_word_count": 0,
                "update_only_physical_word_count": 0,
            },
            "storages": [
                {
                    "storage_id": storage_id,
                    "semantic_instance_id": "rtl_instance:toy",
                    "word_bits": 32,
                    "word_count": 4,
                    "generated_binding": storage_binding,
                }
            ],
            "lowering_declarations": [bus_lowering, alias_lowering],
            "semantic_observations": observations,
            "bindings": bindings,
            "physical_words": physical_words,
            "update_regions": [
                {
                    "storage_id": storage_id,
                    "raw_base_word": 0,
                    "width_bits": 2,
                    "site_count": 1,
                },
                {
                    "storage_id": storage_id,
                    "raw_base_word": 2,
                    "width_bits": 1,
                    "site_count": 1,
                },
            ],
        },
        "limitations": {
            "generated_storage_instances": "provided",
            "semantic_instance_topology": "not_provided",
            "coverage_mapping": "provided",
        },
    }


def _oracle(mapping: dict) -> dict:
    region_keys = (
        "name",
        "binding",
        "kind",
        "word_bits",
        "word_count",
        "word_offset",
        "state_offset",
        "size_bytes",
        "hit_semantics",
    )
    return {
        "surface": "verilator_toggle_coverage_oracle",
        "schema_version": 1,
        "model_prefix": "Vtoy",
        "region": {key: mapping["region"][key] for key in region_keys},
        "metrics": dict(mapping["metrics"]),
        "fingerprints": dict(mapping["fingerprints"]),
    }


class CoverageMappingTest(unittest.TestCase):
    def test_accepts_lexical_scope_prefix_without_weakening_leaf_match(self) -> None:
        variable = {"kind": "variable", "base_name": "state_q"}
        selection = {"kind": "selection", "base_name": "storage"}
        self.assertTrue(_label_matches_expression("gen_async.state_q", variable))
        self.assertTrue(
            _label_matches_expression("gen_normal_fifo.storage[3]", selection)
        )
        self.assertFalse(_label_matches_expression("gen_async.other_q", variable))
        self.assertFalse(
            _label_matches_expression("gen_normal_fifo.storage_q[3]", selection)
        )

    def test_describes_unpacked_array_selection(self) -> None:
        dtypes = {
            "(D1)": {
                "type": "BASICDTYPE",
                "addr": "(D1)",
                "keyword": "logic",
                "range": "31:0",
            }
        }
        descriptor, width = _expression_descriptor(
            {
                "type": "ARRAYSEL",
                "dtypep": "(D1)",
                "fromp": [{"type": "VARREF", "name": "words"}],
                "bitp": [{"type": "CONST", "name": "32'h2"}],
            },
            dtypes,
        )
        self.assertEqual(width, 32)
        self.assertEqual(
            descriptor,
            {
                "kind": "array_selection",
                "base_name": "words",
                "index": "32'h2",
                "width_bits": 32,
            },
        )

    def _build(
        self,
        *,
        native_manifest: dict | None = None,
        oracle: dict | None = None,
    ) -> dict:
        tree, meta, hierarchy = _tree_and_meta()
        return build_toggle_coverage_mapping(
            tree=tree,
            meta=meta,
            semantic_hierarchy=hierarchy,
            source_root=ROOT,
            model_prefix="Vtoy",
            producer=PRODUCER,
            native_manifest=native_manifest or _native_manifest(),
            coverage_contract=_contract(),
            layout_observation=_layout(),
            oracle=oracle,
        )

    def test_maps_ast_semantics_to_aliased_words_deterministically(self) -> None:
        first = self._build()
        second = self._build()

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "resolved")
        self.assertEqual(first["metrics"]["ast_declaration_count"], 2)
        self.assertEqual(first["metrics"]["semantic_observation_count"], 6)
        self.assertEqual(first["metrics"]["raw_word_count"], 4)
        self.assertEqual(first["metrics"]["aliased_raw_word_count"], 2)
        self.assertEqual(
            first["metrics"]["maximum_canonical_identities_per_raw_word"], 2
        )
        self.assertEqual(
            [word["member_count"] for word in first["physical_words"]],
            [1, 1, 2, 2],
        )
        self.assertEqual(
            first["lowering"]["helper"]["word_offset_order"],
            ["1->0", "0->1"],
        )
        self.assertEqual(first["lowering"]["generated_cpp_parse"], "not_used")
        self.assertEqual(first["lowering"]["generated_source_count"], 0)
        validate_coverage_mapping(first)

    def test_oracle_drift_is_a_valid_mismatch(self) -> None:
        resolved = self._build()
        oracle = _oracle(resolved)
        verified = self._build(oracle=oracle)
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["issues"], [])

        drifted = copy.deepcopy(oracle)
        drifted["metrics"]["semantic_observation_count"] += 1
        mismatch = self._build(oracle=drifted)
        self.assertEqual(mismatch["status"], "mismatch")
        self.assertEqual(
            mismatch["issues"],
            ["oracle_metric_semantic_observation_count_mismatch"],
        )

    def test_rejects_generated_label_that_does_not_match_ast(self) -> None:
        changed = _native_manifest(alias_comment="wrong")
        with self.assertRaisesRegex(CoverageMappingError, "does not match AST"):
            self._build(native_manifest=changed)

    def test_rejects_tampered_native_identity(self) -> None:
        changed = _native_manifest()
        changed["coverage"]["semantic_observations"][0]["semantic_id"] = "changed"
        with self.assertRaisesRegex(CoverageMappingError, "identity is invalid"):
            self._build(native_manifest=changed)

    def test_rejects_partial_native_coverage_contract(self) -> None:
        changed = _native_manifest()
        changed["coverage"]["status"] = "partial"
        changed["limitations"]["coverage_mapping"] = "partial"
        with self.assertRaisesRegex(CoverageMappingError, "is not provided"):
            self._build(native_manifest=changed)

    def test_rejects_native_binding_outside_its_lowering(self) -> None:
        changed = _native_manifest()
        changed["coverage"]["bindings"][4]["physical_word_id"] = changed[
            "coverage"
        ]["physical_words"][0]["physical_word_id"]
        with self.assertRaisesRegex(CoverageMappingError, "outside its lowering"):
            self._build(native_manifest=changed)

    def test_validator_rejects_a_tampered_physical_mapping(self) -> None:
        mapping = self._build()
        tampered = copy.deepcopy(mapping)
        tampered["physical_words"][0]["state_offset"] += 4
        with self.assertRaisesRegex(CoverageMappingError, "physical word"):
            validate_coverage_mapping(tampered)


if __name__ == "__main__":
    unittest.main()
