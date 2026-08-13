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
    validate_native_manifest,
    verify_native_adapter,
    write_native_verification,
)


def _native_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "surface": "verilator_model_manifest_experimental",
        "producer": "Verilator test",
        "model": {"top": "tiny", "prefix": "Vtiny"},
        "field_count": 2,
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
        "limitations": {
            "generated_storage_instances": "provided",
            "semantic_instance_topology": "not_provided",
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
        with tempfile.TemporaryDirectory() as work:
            first = Path(work) / "first.json"
            second = Path(work) / "second.json"
            write_native_verification(first, report)
            write_native_verification(second, report)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(json.loads(first.read_text()), report)

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


if __name__ == "__main__":
    unittest.main()
