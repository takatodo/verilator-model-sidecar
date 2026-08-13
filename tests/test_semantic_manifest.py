from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.semantic import (  # noqa: E402
    SidecarError,
    analyze_manifest,
    capture_manifest,
    extract_semantic_projection,
    validate_manifest,
    verify_adapter_semantics,
)
from verilator_model_sidecar.physical import probe_physical_layout  # noqa: E402


def _synthetic_tree() -> tuple[dict, dict]:
    dtype_logic = {
        "type": "BASICDTYPE",
        "name": "logic",
        "addr": "(D1)",
        "keyword": "logic",
        "range": "7:0",
    }
    dtype_bit = {
        "type": "BASICDTYPE",
        "name": "logic",
        "addr": "(D2)",
        "keyword": "logic",
    }
    tree = {
        "type": "NETLIST",
        "modulesp": [
            {
                "type": "MODULE",
                "name": "tiny",
                "stmtsp": [
                    {
                        "type": "VAR",
                        "name": "clk_i",
                        "verilogName": "clk_i",
                        "origName": "clk_i",
                        "addr": "(V1)",
                        "loc": "e,2:15,2:20",
                        "dtypep": "(D2)",
                        "dtypeName": "logic",
                        "direction": "INPUT",
                        "varType": "PORT",
                    },
                    {
                        "type": "VAR",
                        "name": "state_q",
                        "verilogName": "state_q",
                        "origName": "state_q",
                        "addr": "(V2)",
                        "loc": "e,3:15,3:22",
                        "dtypep": "(D1)",
                        "dtypeName": "logic",
                        "direction": "NONE",
                        "varType": "VAR",
                    },
                    {
                        "type": "ALWAYS",
                        "keyword": "always_ff",
                        "loc": "e,4:3,4:12",
                        "stmtsp": [
                            {
                                "type": "ASSIGNDLY",
                                "rhsp": [
                                    {
                                        "type": "VARREF",
                                        "varp": "(V1)",
                                        "access": "RD",
                                    }
                                ],
                                "lhsp": [
                                    {
                                        "type": "VARREF",
                                        "varp": "(V2)",
                                        "access": "WR",
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ],
        "miscsp": [{"type": "TYPETABLE", "typesp": [dtype_logic, dtype_bit]}],
    }
    meta = {
        "files": {
            "e": {
                "filename": "tests/fixtures/tiny/tiny.sv",
                "realpath": "/ignored/local/path/tiny.sv",
                "language": "1800-2023",
            }
        },
        "pointers": {"(V1)": "0x1234"},
    }
    return tree, meta


def _synthetic_hierarchy_tree() -> tuple[dict, dict, dict]:
    tree, meta = _synthetic_tree()
    top = tree["modulesp"][0]
    for index in range(2):
        top["stmtsp"].append(
            {
                "type": "GENBLOCK",
                "name": f"gen_status[{index}]",
                "addr": f"(G{index})",
                "loc": "e,8:3,8:11",
                "itemsp": [
                    {
                        "type": "CELL",
                        "name": "u_status",
                        "verilogName": "u_status",
                        "origName": "u_status",
                        "addr": f"(C{index})",
                        "loc": "e,8:3,8:11",
                        "modp": "(M2)",
                        "pinsp": [],
                    }
                ],
            }
        )
    tree["modulesp"].append(
        {
            "type": "MODULE",
            "name": "status_if",
            "origName": "status_if",
            "verilogName": "status_if",
            "addr": "(M2)",
            "loc": "e,10:1,10:10",
            "stmtsp": [
                {
                    "type": "VAR",
                    "name": "done",
                    "verilogName": "done",
                    "origName": "done",
                    "addr": "(V3)",
                    "loc": "e,11:7,11:11",
                    "dtypep": "(D2)",
                    "dtypeName": "logic",
                    "direction": "NONE",
                    "varType": "VAR",
                }
            ],
        }
    )
    adapter = {
        "adapter_id": "verilator",
        "target": "synthetic",
        "source_model": {"prefix": "Vtiny"},
        "signals": {
            "clock": {
                "binding": "Vtiny___024root.tiny__DOT__clk_i",
                "width_bits": 1,
                "direction": "drive",
                "role": "clock",
            },
            "done": {
                "binding": "Vtiny__Syms.TOP__tiny__DOT__gen_status__BRA__0__KET____DOT__u_status.done",
                "width_bits": 1,
                "direction": "observe",
                "role": "completion",
            },
        },
    }
    return tree, meta, adapter


class SemanticManifestTest(unittest.TestCase):
    def test_extracts_deterministic_semantic_projection(self) -> None:
        tree, meta = _synthetic_tree()

        first = extract_semantic_projection(tree, meta, top="tiny", source_root=ROOT)
        second = extract_semantic_projection(tree, meta, top="tiny", source_root=ROOT)

        self.assertEqual(first, second)
        self.assertEqual(first["entity_count"], 2)
        entities = {entity["name"]: entity for entity in first["entities"]}
        self.assertEqual(entities["clk_i"]["width_bits"], 1)
        self.assertEqual(entities["clk_i"]["lifecycle"], "external_input")
        self.assertEqual(entities["state_q"]["width_bits"], 8)
        self.assertEqual(entities["state_q"]["lifecycle"], "persistent_mutable")
        self.assertEqual(first["process_count"], 1)
        self.assertEqual(len(first["processes"][0]["reads"]), 1)
        self.assertEqual(len(first["processes"][0]["writes"]), 1)
        self.assertNotIn("/ignored/", json.dumps(first))

    def test_resolves_adapter_signals_through_elaborated_hierarchy(self) -> None:
        tree, meta, adapter = _synthetic_hierarchy_tree()

        projection = extract_semantic_projection(
            tree, meta, top="tiny", source_root=ROOT
        )
        verification = verify_adapter_semantics(
            tree, meta, adapter, top="tiny", source_root=ROOT
        )

        hierarchy = projection["hierarchy"]
        self.assertEqual(hierarchy["status"], "resolved")
        self.assertEqual(hierarchy["instance_count"], 3)
        self.assertEqual(
            [entry["canonical_path"] for entry in hierarchy["instances"]],
            [
                "tiny",
                "tiny.gen_status[0].u_status",
                "tiny.gen_status[1].u_status",
            ],
        )
        self.assertEqual(verification["status"], "matched")
        self.assertEqual(verification["matched_count"], 2)
        resolved = {entry["name"]: entry for entry in verification["signals"]}
        self.assertEqual(
            resolved["done"]["semantic_entity"]["canonical_name"],
            "tiny.gen_status[0].u_status.done",
        )
        self.assertEqual(
            resolved["done"]["direction_authority"],
            "adapter_contract_not_derived_from_ast",
        )
        mismatched = json.loads(json.dumps(adapter))
        mismatched["signals"]["done"]["width_bits"] = 2
        mismatch_report = verify_adapter_semantics(
            tree, meta, mismatched, top="tiny", source_root=ROOT
        )
        self.assertEqual(mismatch_report["status"], "mismatch")
        self.assertEqual(mismatch_report["matched_count"], 1)
        self.assertEqual(mismatch_report["unmatched_count"], 1)

    def test_manifest_validation_fails_closed_on_unimplemented_sections(self) -> None:
        manifest = {
            "schema_version": 1,
            "surface": "verilator_model_sidecar_manifest",
            "semantic_projection": {
                "entities": [],
                "hierarchy": {
                    "instance_count": 0,
                    "unresolved_count": 0,
                    "instances": [],
                    "unresolved": [],
                },
            },
            "physical_bindings": {"status": "passed", "bindings": []},
            "checkpoint_projection": {"status": "not_analyzed", "fields": []},
            "coverage_mapping": {"status": "not_analyzed", "mappings": []},
            "eval_effects": {"status": "not_analyzed", "regions": []},
        }

        with self.assertRaises(SidecarError):
            validate_manifest(manifest)

    @unittest.skipUnless(shutil.which("verilator"), "Verilator is not installed")
    def test_verilator_5050_capture_is_reproducible(self) -> None:
        version = subprocess.run(
            ["verilator", "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        if not version.startswith("Verilator 5.050 "):
            self.skipTest("integration contract is pinned to Verilator 5.050")

        source = Path("tests/fixtures/tiny/tiny.sv")
        with tempfile.TemporaryDirectory() as first_work, tempfile.TemporaryDirectory() as second_work:
            first = capture_manifest(
                source_root=ROOT,
                top="tiny",
                sources=[source],
                work_dir=Path(first_work),
            )
            second = capture_manifest(
                source_root=ROOT,
                top="tiny",
                sources=[source],
                work_dir=Path(second_work),
            )
            tiny_adapter = {
                "adapter_id": "verilator",
                "target": "tiny",
                "source_model": {"prefix": "Vtiny"},
                "signals": {
                    "clock": {
                        "binding": "Vtiny___024root.clk_i",
                        "width_bits": 1,
                        "direction": "drive",
                        "role": "clock",
                    },
                    "state": {
                        "binding": "Vtiny___024root.tiny__DOT__state_q",
                        "width_bits": 8,
                        "direction": "observe",
                        "role": "state",
                    },
                },
            }
            tiny_coverage = {
                "surface": "verilator_toggle_coverage_contract",
                "schema_version": 1,
                "source_model": {"prefix": "Vtiny"},
                "regions": {
                    "toggle": {
                        "binding": "Vtiny__Syms.TOP.__Vcoverage",
                        "kind": "toggle_direction_counters",
                        "word_bits": 32,
                    }
                },
            }
            layout = probe_physical_layout(
                obj_dir=Path(first_work) / "obj_dir",
                adapter=tiny_adapter,
                coverage_contract=tiny_coverage,
                producer=version.strip(),
            )
            physical_oracle = {
                "surface": "verilator_physical_binding_oracle",
                "schema_version": 1,
                "model_prefix": "Vtiny",
                "headers": layout["headers"],
                "state_image": layout["state_image"],
                "bindings": {
                    row["name"]: {
                        "state_offset": row["state_offset"],
                        "size_bytes": row["size_bytes"],
                    }
                    for row in layout["bindings"]
                },
            }
            with patch(
                "verilator_model_sidecar.semantic.subprocess.run",
                side_effect=AssertionError("analyze must not execute subprocesses"),
            ):
                analyzed = analyze_manifest(
                    source_root=ROOT,
                    top="tiny",
                    tree_path=Path(first_work) / "Vtiny.tree.json",
                    meta_path=Path(first_work) / "Vtiny.tree.meta.json",
                    obj_dir=Path(first_work) / "obj_dir",
                    producer=version.strip(),
                    adapter=tiny_adapter,
                    layout_observation=layout,
                    physical_oracle=physical_oracle,
                    coverage_contract=tiny_coverage,
                )

        self.assertEqual(first, second)
        self.assertEqual(
            analyzed["semantic_projection"], first["semantic_projection"]
        )
        self.assertEqual(analyzed["provenance"]["artifact_mode"], "external")
        self.assertEqual(analyzed["status"], "coverage_mapping_resolved")
        self.assertEqual(analyzed["physical_bindings"]["verified_count"], 2)
        self.assertEqual(analyzed["physical_bindings"]["mismatch_count"], 0)
        self.assertEqual(analyzed["coverage_mapping"]["status"], "resolved")
        self.assertEqual(
            analyzed["coverage_mapping"]["metrics"]["raw_word_count"], 52
        )
        validate_manifest(first)
        entities = {
            entity["name"]: entity
            for entity in first["semantic_projection"]["entities"]
        }
        self.assertEqual(set(entities), {"Width", "clk_i", "data_i", "data_o", "rst_ni", "state_q"})
        self.assertEqual(entities["state_q"]["lifecycle"], "persistent_mutable")
        self.assertEqual(entities["data_i"]["width_bits"], 8)
        self.assertEqual(first["physical_bindings"]["status"], "not_analyzed")
        self.assertNotIn("/home/", json.dumps(first))
        self.assertNotIn("/tmp/", json.dumps(first))


if __name__ == "__main__":
    unittest.main()
