from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.cli import main as cli_main  # noqa: E402
from verilator_model_sidecar.native import (  # noqa: E402
    NativeManifestError,
    project_native_checkpoint,
    validate_native_checkpoint_manifest,
    validate_native_manifest,
    verify_native_adapter,
    write_native_checkpoint,
    write_native_verification,
)


def _native_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "surface": "verilator_model_manifest_experimental",
        "producer": "Verilator test",
        "model": {"top": "tiny", "prefix": "Vtiny"},
        "field_count": 3,
        "fields": [
            {
                "field_id": "rtl:tiny.clk_i",
                "origin": "rtl",
                "semantic_path": "tiny.clk_i",
                "rtl_name": "clk_i",
                "width_bits": 1,
                "direction": "INPUT",
                "source": {"file": "tiny.sv", "first_line": 1},
                "generated_binding": {
                    "container": "Vtiny___024root",
                    "member": "clk_i",
                    "storage": "instance_member",
                },
                "checkpoint_membership": {
                    "status": "included",
                    "authority": "verilator_savable_field_selection",
                    "reason": "serialized_field",
                },
            },
            {
                "field_id": "rtl:status_if.done",
                "origin": "rtl",
                "semantic_path": "status_if.done",
                "rtl_name": "done",
                "width_bits": 1,
                "direction": "NONE",
                "source": {"file": "tiny.sv", "first_line": 4},
                "generated_binding": {
                    "container": "Vtiny_status_if",
                    "member": "done",
                    "storage": "instance_member",
                },
                "checkpoint_membership": {
                    "status": "included",
                    "authority": "verilator_savable_field_selection",
                    "reason": "serialized_field",
                },
            },
            {
                "field_id": "generated:Vtiny___024root.CONST_TABLE",
                "origin": "compiler_generated",
                "semantic_path": "",
                "rtl_name": "",
                "width_bits": 64,
                "direction": "NONE",
                "source": {"file": "", "first_line": 0},
                "generated_binding": {
                    "container": "Vtiny___024root",
                    "member": "CONST_TABLE",
                    "storage": "static_member",
                },
                "checkpoint_membership": {
                    "status": "excluded",
                    "authority": "verilator_savable_field_selection",
                    "reason": "static_const",
                },
            },
        ],
        "instance_count": 2,
        "instances": [
            {
                "instance_id": "rtl_instance:tiny",
                "semantic_path": "tiny",
                "parent_instance_id": "",
                "is_top": True,
                "module_binding": {"container": "Vtiny___024root"},
                "generated_binding": {
                    "container": "Vtiny__Syms",
                    "member": "TOP",
                    "storage": "instance_member",
                },
            },
            {
                "instance_id": "rtl_instance:tiny.status",
                "semantic_path": "tiny.status",
                "parent_instance_id": "rtl_instance:tiny",
                "is_top": False,
                "module_binding": {"container": "Vtiny_status_if"},
                "generated_binding": {
                    "container": "Vtiny__Syms",
                    "member": "TOP__tiny__DOT__status",
                    "storage": "instance_member",
                },
            },
        ],
        "checkpoint_projection": {
            "status": "field_membership_only",
            "authority": "verilator_savable_field_selection",
            "included_definition_field_count": 2,
            "excluded_definition_field_count": 1,
            "runtime_state": "not_provided",
            "packing": "not_provided",
        },
        "limitations": {
            "generated_storage_instances": "provided",
            "semantic_instance_topology": "not_provided",
            "checkpoint_field_membership": "provided",
            "pointer_free_checkpoint": "not_provided",
        },
    }


def _adapter() -> dict[str, object]:
    return {
        "schema_version": 1,
        "surface": "verilator_adapter_semantic_contract",
        "adapter_id": "verilator",
        "target": "tiny",
        "source_model": {"prefix": "Vtiny"},
        "signals": {
            "clock": {
                "binding": "Vtiny___024root.clk_i",
                "direction": "drive",
                "role": "clock",
                "width_bits": 1,
            },
            "done": {
                "binding": "Vtiny__Syms.TOP__tiny__DOT__status.done",
                "direction": "observe",
                "role": "completion",
                "width_bits": 1,
            },
        },
    }


class NativeManifestTest(unittest.TestCase):
    def test_projects_checkpoint_membership_onto_stored_instances(self) -> None:
        report = project_native_checkpoint(_native_manifest())

        self.assertEqual(report["status"], "projected")
        self.assertEqual(report["definition_field_count"], 3)
        self.assertEqual(report["included_definition_field_count"], 2)
        self.assertEqual(report["excluded_definition_field_count"], 1)
        self.assertEqual(report["stored_field_occurrence_count"], 2)
        self.assertEqual(report["unsupported_included_definition_field_count"], 0)
        occurrences = {
            row["canonical_name"]: row
            for row in report["stored_field_occurrences"]
        }
        self.assertEqual(set(occurrences), {"tiny.clk_i", "tiny.status.done"})
        self.assertEqual(
            occurrences["tiny.status.done"]["generated_storage"],
            {
                "state_container": "Vtiny__Syms",
                "instance_member": "TOP__tiny__DOT__status",
                "field_container": "Vtiny_status_if",
                "field_member": "done",
            },
        )
        self.assertEqual(report["runtime_state"], "not_provided")
        self.assertEqual(report["packing"], "not_provided")

    def test_checkpoint_projection_fails_closed(self) -> None:
        missing = copy.deepcopy(_native_manifest())
        del missing["fields"][0]["checkpoint_membership"]
        with self.assertRaisesRegex(NativeManifestError, "checkpoint membership"):
            validate_native_checkpoint_manifest(missing)

        unsupported = copy.deepcopy(_native_manifest())
        unsupported["fields"][2]["checkpoint_membership"] = {
            "status": "included",
            "authority": "verilator_savable_field_selection",
            "reason": "serialized_field",
        }
        unsupported["checkpoint_projection"]["included_definition_field_count"] = 3
        unsupported["checkpoint_projection"]["excluded_definition_field_count"] = 0
        report = project_native_checkpoint(unsupported)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["unsupported_included_definition_field_count"], 1)
        self.assertEqual(
            report["unsupported_included_definition_fields"],
            [
                {
                    "field_id": "generated:Vtiny___024root.CONST_TABLE",
                    "storage": "static_member",
                }
            ],
        )

    def test_resolves_direct_and_instance_fields_without_cpp(self) -> None:
        report = verify_native_adapter(_native_manifest(), _adapter())

        self.assertEqual(report["status"], "matched")
        self.assertEqual(report["matched_count"], 2)
        signals = {row["name"]: row for row in report["signals"]}
        self.assertEqual(signals["clock"]["binding_kind"], "direct_field")
        self.assertEqual(signals["clock"]["canonical_name"], "tiny.clk_i")
        self.assertEqual(
            signals["clock"]["native_entity"]["instance_id"],
            "rtl_instance:tiny",
        )
        self.assertEqual(signals["done"]["binding_kind"], "instance_field")
        self.assertEqual(signals["done"]["canonical_name"], "tiny.status.done")
        self.assertEqual(
            signals["done"]["native_entity"]["instance_id"],
            "rtl_instance:tiny.status",
        )
        self.assertEqual(
            signals["done"]["native_entity"]["generated_storage"],
            {
                "state_container": "Vtiny__Syms",
                "instance_member": "TOP__tiny__DOT__status",
                "field_container": "Vtiny_status_if",
                "field_member": "done",
            },
        )

    def test_fails_closed_when_topology_or_width_is_wrong(self) -> None:
        manifest = copy.deepcopy(_native_manifest())
        manifest["limitations"]["generated_storage_instances"] = "not_provided"
        with self.assertRaisesRegex(NativeManifestError, "storage instances"):
            validate_native_manifest(manifest)

        adapter = copy.deepcopy(_adapter())
        adapter["signals"]["done"]["width_bits"] = 2
        report = verify_native_adapter(_native_manifest(), adapter)
        self.assertEqual(report["status"], "mismatch")
        self.assertEqual(report["matched_count"], 1)

    def test_output_is_deterministic(self) -> None:
        report = verify_native_adapter(_native_manifest(), _adapter())
        checkpoint = project_native_checkpoint(_native_manifest())
        with tempfile.TemporaryDirectory() as work:
            first = Path(work) / "first.json"
            second = Path(work) / "second.json"
            write_native_verification(first, report)
            write_native_verification(second, report)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(json.loads(first.read_text()), report)
            first_checkpoint = Path(work) / "first-checkpoint.json"
            second_checkpoint = Path(work) / "second-checkpoint.json"
            write_native_checkpoint(first_checkpoint, checkpoint)
            write_native_checkpoint(second_checkpoint, checkpoint)
            self.assertEqual(
                first_checkpoint.read_bytes(), second_checkpoint.read_bytes()
            )
            self.assertEqual(json.loads(first_checkpoint.read_text()), checkpoint)

    def test_cli_writes_report_and_fails_on_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            work_path = Path(work)
            manifest_path = work_path / "manifest.json"
            adapter_path = work_path / "adapter.json"
            report_path = work_path / "report.json"
            manifest_path.write_text(json.dumps(_native_manifest()), encoding="utf-8")
            adapter_path.write_text(json.dumps(_adapter()), encoding="utf-8")

            arguments = [
                "verify-native",
                "--manifest",
                str(manifest_path),
                "--adapter",
                str(adapter_path),
                "--output",
                str(report_path),
            ]
            self.assertEqual(cli_main(arguments), 0)
            self.assertEqual(json.loads(report_path.read_text())["status"], "matched")

            adapter = _adapter()
            adapter["signals"]["done"]["width_bits"] = 2
            adapter_path.write_text(json.dumps(adapter), encoding="utf-8")
            self.assertEqual(cli_main(arguments), 1)
            self.assertEqual(json.loads(report_path.read_text())["status"], "mismatch")

            checkpoint_path = work_path / "checkpoint.json"
            checkpoint_arguments = [
                "project-native-checkpoint",
                "--manifest",
                str(manifest_path),
                "--output",
                str(checkpoint_path),
            ]
            self.assertEqual(cli_main(checkpoint_arguments), 0)
            self.assertEqual(
                json.loads(checkpoint_path.read_text())["status"], "projected"
            )

            unsupported = _native_manifest()
            unsupported["fields"][2]["checkpoint_membership"] = {
                "status": "included",
                "authority": "verilator_savable_field_selection",
                "reason": "serialized_field",
            }
            unsupported["checkpoint_projection"][
                "included_definition_field_count"
            ] = 3
            unsupported["checkpoint_projection"][
                "excluded_definition_field_count"
            ] = 0
            manifest_path.write_text(json.dumps(unsupported), encoding="utf-8")
            self.assertEqual(cli_main(checkpoint_arguments), 1)
            self.assertEqual(
                json.loads(checkpoint_path.read_text())["status"], "incomplete"
            )


if __name__ == "__main__":
    unittest.main()
