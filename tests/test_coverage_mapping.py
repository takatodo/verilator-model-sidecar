from __future__ import annotations

import copy
import sys
import tempfile
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


GENERATED = r'''
void Vtoy___024root::__vlCoverToggleInsert(int begin, int end, bool ranged,
                                           uint32_t* countp) {
    for (int i = begin; i <= end; ++i) {
        for (int j = 0; j < 2; j++) {
            std::string commentWithIndex;
            commentWithIndex += j ? ":0->1" : ":1->0";
            ++countp;
        }
    }
}
void Vtoy___024root___configure_coverage(Vtoy___024root* vlSelf, bool first) {
    vlSelf->__vlCoverToggleInsert(0, 1, 1, vlSelf->__Vcoverage + 0, first, true, "rtl/toy.sv", 5, 7, ".toy", "v_toggle/toy", "bus");
    vlSelf->__vlCoverToggleInsert(0, 0, 0, vlSelf->__Vcoverage + 2, first, true, "rtl/toy.sv", 6, 7, ".toy", "v_toggle/toy", "alias");
}
void Vtoy___024root___eval(Vtoy___024root* vlSelf) {
    VL_COV_TOGGLE_CHG_ST_I(2, vlSelf->__Vcoverage + 0, bus, old_bus);
    VL_COV_TOGGLE_CHG_ST_I(1, vlSelf->__Vcoverage + 2, alias, old_alias);
}
'''


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
        root: Path,
        *,
        generated: str = GENERATED,
        oracle: dict | None = None,
    ) -> dict:
        obj_dir = root / "obj_dir"
        obj_dir.mkdir()
        (obj_dir / "Vtoy___024root__Slow.cpp").write_text(
            generated, encoding="utf-8"
        )
        tree, meta, hierarchy = _tree_and_meta()
        return build_toggle_coverage_mapping(
            tree=tree,
            meta=meta,
            semantic_hierarchy=hierarchy,
            source_root=ROOT,
            obj_dir=obj_dir,
            model_prefix="Vtoy",
            producer=PRODUCER,
            coverage_contract=_contract(),
            layout_observation=_layout(),
            oracle=oracle,
        )

    def test_maps_ast_semantics_to_aliased_words_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as first_root, tempfile.TemporaryDirectory() as second_root:
            first = self._build(Path(first_root))
            second = self._build(Path(second_root))

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
        validate_coverage_mapping(first)

    def test_oracle_drift_is_a_valid_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as first_root:
            resolved = self._build(Path(first_root))
        oracle = _oracle(resolved)
        with tempfile.TemporaryDirectory() as verified_root:
            verified = self._build(Path(verified_root), oracle=oracle)
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["issues"], [])

        drifted = copy.deepcopy(oracle)
        drifted["metrics"]["semantic_observation_count"] += 1
        with tempfile.TemporaryDirectory() as mismatch_root:
            mismatch = self._build(Path(mismatch_root), oracle=drifted)
        self.assertEqual(mismatch["status"], "mismatch")
        self.assertEqual(
            mismatch["issues"],
            ["oracle_metric_semantic_observation_count_mismatch"],
        )

    def test_rejects_generated_label_that_does_not_match_ast(self) -> None:
        changed = GENERATED.replace('"alias");', '"wrong");')
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CoverageMappingError, "does not match AST"):
                self._build(Path(temporary), generated=changed)

    def test_validator_rejects_a_tampered_physical_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mapping = self._build(Path(temporary))
        tampered = copy.deepcopy(mapping)
        tampered["physical_words"][0]["state_offset"] += 4
        with self.assertRaisesRegex(CoverageMappingError, "physical word"):
            validate_coverage_mapping(tampered)


if __name__ == "__main__":
    unittest.main()
