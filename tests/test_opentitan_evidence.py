from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.cli import main as cli_main  # noqa: E402
from verilator_model_sidecar.opentitan_evidence import (  # noqa: E402
    OPENTITAN_ADJUDICATION_RUN_SPEC_REPORT_SURFACE,
    OPENTITAN_ADJUDICATION_RUN_SPEC_SURFACE,
    OPENTITAN_ADJUDICATION_SUMMARY_VALIDATION_REPORT_SURFACE,
    OPENTITAN_ADJUDICATION_VALIDATION_REPORT_SURFACE,
    OPENTITAN_EVIDENCE_BUNDLE_SURFACE,
    OPENTITAN_SEMANTIC_MANIFEST_SURFACE,
    OPENTITAN_TARGET_CONTRACT_REPORT_SURFACE,
    OPENTITAN_TARGET_CONTRACT_SURFACE,
    OpenTitanEvidenceError,
    adjudicate_external_evidence,
    read_strict_json_object,
    summarize_adjudications,
    validate_adjudication_run_spec,
    validate_adjudication_document,
    validate_adjudication_summary_document,
    validate_target_contract_document,
)


BAD_REVISION = "a" * 40
FIXED_REVISION = "b" * 40


def _semantic_manifest(revision_label: str, revision_sha: str) -> dict:
    return {
        "schema_version": 1,
        "surface": OPENTITAN_SEMANTIC_MANIFEST_SURFACE,
        "target_id": "tlul10818",
        "revision_label": revision_label,
        "revision_sha": revision_sha,
        "checkpoint_identity": "checkpoint:v1:uart-profile",
        "oracle_identity": "oracle:v1:tlul-stall",
        "observables": [
            {
                "name": "valid_o",
                "semantic_id": "rtl:tlul.valid_o",
                "width_bits": 1,
            },
            {
                "name": "data_o",
                "semantic_id": "rtl:tlul.data_o",
                "width_bits": 32,
            },
            {
                "name": "oracle_violation",
                "semantic_id": "oracle:tlul.protocol_violation",
                "width_bits": 1,
            },
        ],
    }


BAD_MANIFEST_PAYLOAD = json.dumps(
    _semantic_manifest("bad", BAD_REVISION), sort_keys=True
) + "\n"
FIXED_MANIFEST_PAYLOAD = json.dumps(
    _semantic_manifest("fixed", FIXED_REVISION), sort_keys=True
) + "\n"
BAD_MANIFEST = hashlib.sha256(BAD_MANIFEST_PAYLOAD.encode("utf-8")).hexdigest()
FIXED_MANIFEST = hashlib.sha256(FIXED_MANIFEST_PAYLOAD.encode("utf-8")).hexdigest()


def _canonical_sha(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_artifact(root: Path, relative: str, payload: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract() -> dict:
    return {
        "schema_version": 1,
        "surface": "opentitan_regression_target_contract",
        "targets": [
            {
                "target_id": "tlul10818",
                "ip": "tlul",
                "issue": "10818",
                "revisions": {
                    "bad": BAD_REVISION,
                    "fixed": FIXED_REVISION,
                },
                "checkpoint_identity": "checkpoint:v1:uart-profile",
                "oracle_identity": "oracle:v1:tlul-stall",
                "campaign_action_domain": ["stall_a", "stall_d"],
                "reproduction_action_domain": ["stall_a"],
                "semantic_observables": ["valid_o", "data_o"],
                "oracle_field": "oracle_violation",
                "semantic_manifest_sha256": {
                    "bad": BAD_MANIFEST,
                    "fixed": FIXED_MANIFEST,
                },
            }
        ],
    }


def _evidence(root: Path) -> dict:
    domain = ["stall_a", "stall_d"]
    bad_manifest_hash = _write_artifact(root, "manifests/bad.json", BAD_MANIFEST_PAYLOAD)
    fixed_manifest_hash = _write_artifact(root, "manifests/fixed.json", FIXED_MANIFEST_PAYLOAD)
    source_hash = _write_artifact(root, "raw/equivalence.json", '{"status":"pass"}\n')
    campaign_hash = _write_artifact(root, "raw/campaign.json", '{"policies":["stratified"]}\n')
    graph_hash = _write_artifact(root, "graphs/coverage.svg", "<svg></svg>\n")
    report_hash = _write_artifact(root, "reports/runner.md", "# runner\n")
    graph_payload = {
        "kind": "coverage_curve",
        "source_artifact_role": "campaign_report",
        "source_artifact_sha256": campaign_hash,
        "policies": ["stratified"],
    }
    report_payload = {
        "kind": "runner_summary",
        "source_artifact_roles": ["equivalence_report", "campaign_report"],
        "source_artifact_sha256_by_role": {
            "equivalence_report": source_hash,
            "campaign_report": campaign_hash,
        },
    }
    bad_observations = [
        {
            "sequence": ["stall_a"],
            "status": "pass",
            "cpu": {
                "valid_o": 1,
                "data_o": 7,
                "oracle_violation": 1,
            },
            "gpu": {
                "valid_o": 1,
                "data_o": 7,
                "oracle_violation": 1,
            },
        },
        {
            "sequence": ["stall_d"],
            "status": "pass",
            "cpu": {
                "valid_o": 0,
                "data_o": 3,
                "oracle_violation": 0,
            },
            "gpu": {
                "valid_o": 0,
                "data_o": 3,
                "oracle_violation": 0,
            },
        },
    ]
    fixed_observations = copy.deepcopy(bad_observations)
    fixed_observations[0]["cpu"]["oracle_violation"] = 0
    fixed_observations[0]["gpu"]["oracle_violation"] = 0
    return {
        "schema_version": 1,
        "surface": "opentitan_external_regression_evidence_bundle",
        "target_id": "tlul10818",
        "target": {
            "ip": "tlul",
            "issue": "10818",
        },
        "runner": {
            "identity": "external-runner",
            "status": "pass",
            "completed_at": "2026-08-14T00:00:00Z",
        },
        "revisions": {
            "bad": BAD_REVISION,
            "fixed": FIXED_REVISION,
        },
        "checkpoint_identity": "checkpoint:v1:uart-profile",
        "oracle_identity": "oracle:v1:tlul-stall",
        "action_domain": domain,
        "action_domain_sha256": _canonical_sha(domain),
        "semantic_manifest_sha256": {
            "bad": BAD_MANIFEST,
            "fixed": FIXED_MANIFEST,
        },
        "revision_results": {
            "bad": {
                "executed_action_sequences": [["stall_a"], ["stall_d"]],
                "oracle_failure_action_sequences": [["stall_a"]],
                "observations": bad_observations,
            },
            "fixed": {
                "executed_action_sequences": [["stall_a"], ["stall_d"]],
                "oracle_failure_action_sequences": [],
                "observations": fixed_observations,
            },
        },
        "seed_corpora": {
            "coverage_gain": [
                {
                    "schema_version": 1,
                    "corpus_kind": "coverage_gain",
                    "source_revision": "bad",
                    "sequence": ["stall_a"],
                    "coverage_delta_bits": "01",
                }
            ],
            "oracle_violation": [
                {
                    "schema_version": 1,
                    "corpus_kind": "oracle_violation",
                    "source_revision": "bad",
                    "sequence": ["stall_a"],
                    "minimal": True,
                }
            ],
        },
        "campaign": {
            "policies": [
                {
                    "name": "stratified",
                    "orders": [domain],
                    "metrics": {
                        "mean": 2,
                        "p50": 2,
                        "p95": 2,
                        "max": 2,
                        "long_tail_rate": 0,
                    },
                }
            ]
        },
        "source_artifacts": [
            {
                "role": "semantic_manifest_bad",
                "path": "manifests/bad.json",
                "sha256": bad_manifest_hash,
            },
            {
                "role": "semantic_manifest_fixed",
                "path": "manifests/fixed.json",
                "sha256": fixed_manifest_hash,
            },
            {
                "role": "equivalence_report",
                "path": "raw/equivalence.json",
                "sha256": source_hash,
            },
            {
                "role": "campaign_report",
                "path": "raw/campaign.json",
                "sha256": campaign_hash,
            }
        ],
        "graph_artifacts": [
            {
                "role": "coverage_curve",
                "path": "graphs/coverage.svg",
                "sha256": graph_hash,
                "payload": graph_payload,
                "payload_sha256": _canonical_sha(graph_payload),
            }
        ],
        "report_artifacts": [
            {
                "role": "runner_summary",
                "path": "reports/runner.md",
                "sha256": report_hash,
                "payload": report_payload,
                "payload_sha256": _canonical_sha(report_payload),
            }
        ],
    }


def _codes(adjudication: dict) -> set[str]:
    return {
        issue["code"]
        for issue in adjudication["issues"]
    }


def _bind_persisted_input_hashes(adjudication: dict) -> dict:
    adjudication["input_sha256"] = {
        "target_contract": "c" * 64,
        "evidence": "d" * 64,
    }
    return adjudication


class OpenTitanEvidenceTest(unittest.TestCase):
    def test_public_schema_files_define_expected_surfaces(self) -> None:
        schema_expectations = {
            "opentitan_regression_semantic_manifest.schema.json": {
                "surface": OPENTITAN_SEMANTIC_MANIFEST_SURFACE,
                "required": {
                    "schema_version",
                    "surface",
                    "target_id",
                    "revision_label",
                    "revision_sha",
                    "checkpoint_identity",
                    "oracle_identity",
                    "observables",
                },
            },
            "opentitan_regression_target_contract.schema.json": {
                "surface": OPENTITAN_TARGET_CONTRACT_SURFACE,
                "required": {
                    "schema_version",
                    "surface",
                    "targets",
                },
            },
            "opentitan_external_regression_evidence_bundle.schema.json": {
                "surface": OPENTITAN_EVIDENCE_BUNDLE_SURFACE,
                "required": {
                    "schema_version",
                    "surface",
                    "target_id",
                    "target",
                    "runner",
                    "revisions",
                    "checkpoint_identity",
                    "oracle_identity",
                    "action_domain",
                    "action_domain_sha256",
                    "semantic_manifest_sha256",
                    "revision_results",
                    "seed_corpora",
                    "campaign",
                    "source_artifacts",
                    "graph_artifacts",
                    "report_artifacts",
                },
            },
            "opentitan_regression_adjudication_run_spec.schema.json": {
                "surface": OPENTITAN_ADJUDICATION_RUN_SPEC_SURFACE,
                "required": {
                    "schema_version",
                    "surface",
                    "target_contract",
                    "evidence_root",
                    "evidence",
                },
            },
            "opentitan_regression_adjudication_run_spec_report.schema.json": {
                "surface": OPENTITAN_ADJUDICATION_RUN_SPEC_REPORT_SURFACE,
                "required": {
                    "schema_version",
                    "surface",
                    "status",
                    "checks",
                    "issue_count",
                    "issues",
                },
            },
            "opentitan_regression_target_contract_report.schema.json": {
                "surface": OPENTITAN_TARGET_CONTRACT_REPORT_SURFACE,
                "required": {
                    "schema_version",
                    "surface",
                    "status",
                    "checks",
                    "target_count",
                    "issue_count",
                    "issues",
                    "targets",
                },
            },
            "opentitan_regression_adjudication.schema.json": {
                "surface": "opentitan_regression_adjudication",
                "required": {
                    "schema_version",
                    "surface",
                    "target_id",
                    "status",
                    "verified_identity",
                    "checks",
                    "issue_count",
                    "issues",
                    "verified_artifacts",
                    "input_sha256",
                },
            },
            "opentitan_regression_adjudication_summary.schema.json": {
                "surface": "opentitan_regression_adjudication_summary",
                "required": {
                    "schema_version",
                    "surface",
                    "status",
                    "input_sha256",
                    "evidence_count",
                    "pass_count",
                    "fail_count",
                    "check_counts",
                    "results",
                },
            },
            "opentitan_regression_adjudication_validation_report.schema.json": {
                "surface": OPENTITAN_ADJUDICATION_VALIDATION_REPORT_SURFACE,
                "required": {
                    "schema_version",
                    "surface",
                    "status",
                    "issue_count",
                    "issues",
                },
            },
            "opentitan_regression_adjudication_summary_validation_report.schema.json": {
                "surface": OPENTITAN_ADJUDICATION_SUMMARY_VALIDATION_REPORT_SURFACE,
                "required": {
                    "schema_version",
                    "surface",
                    "status",
                    "issue_count",
                    "issues",
                },
            },
        }
        self.assertEqual(
            set(schema_expectations),
            {
                path.name
                for path in (ROOT / "contracts").glob("opentitan_*schema.json")
            },
        )
        for filename, expectation in schema_expectations.items():
            with self.subTest(filename=filename):
                schema_path = ROOT / "contracts" / filename
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(schema["properties"]["surface"]["const"], expectation["surface"])
                self.assertTrue(expectation["required"].issubset(set(schema["required"])))
        summary_schema = json.loads(
            (ROOT / "contracts" / "opentitan_regression_adjudication_summary.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("verified_identity", summary_schema["$defs"]["result"]["required"])

    def test_public_schemas_encode_fail_closed_output_boundaries(self) -> None:
        adjudication_schema = json.loads(
            (ROOT / "contracts" / "opentitan_regression_adjudication.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("input_sha256", adjudication_schema["required"])
        self.assertFalse(adjudication_schema["$defs"]["input_sha256"]["additionalProperties"])
        self.assertEqual(
            adjudication_schema["$defs"]["artifact_rows"]["items"]["properties"][
                "source_artifact_sha256_by_role"
            ]["minProperties"],
            1,
        )
        self.assertEqual(
            set(
                adjudication_schema["allOf"][0]["then"]["properties"]["input_sha256"][
                    "required"
                ]
            ),
            {"target_contract", "evidence"},
        )

        summary_schema = json.loads(
            (
                ROOT
                / "contracts"
                / "opentitan_regression_adjudication_summary.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(summary_schema["properties"]["evidence_count"]["minimum"], 1)
        self.assertEqual(summary_schema["properties"]["results"]["minItems"], 1)
        self.assertFalse(summary_schema["$defs"]["input_sha256"]["additionalProperties"])
        self.assertEqual(
            set(
                summary_schema["$defs"]["result"]["allOf"][0]["then"]["properties"][
                    "input_sha256"
                ]["required"]
            ),
            {"target_contract", "evidence"},
        )

        manifest_schema = json.loads(
            (
                ROOT
                / "contracts"
                / "opentitan_regression_semantic_manifest.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(manifest_schema["$defs"]["observable"]["required"]),
            {"name", "semantic_id", "width_bits"},
        )

    def test_target_contract_schema_requires_core_target_fields(self) -> None:
        schema = json.loads(
            (ROOT / "contracts" / "opentitan_regression_target_contract.schema.json").read_text(
                encoding="utf-8"
            )
        )
        target_required = set(schema["$defs"]["target"]["required"])
        self.assertTrue(
            {
                "target_id",
                "ip",
                "issue",
                "revisions",
                "checkpoint_identity",
                "oracle_identity",
                "campaign_action_domain",
                "reproduction_action_domain",
                "semantic_observables",
                "oracle_field",
                "semantic_manifest_sha256",
            }.issubset(target_required)
        )

    def test_evidence_schema_requires_payload_source_provenance(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "contracts"
                / "opentitan_external_regression_evidence_bundle.schema.json"
            ).read_text(encoding="utf-8")
        )
        payload = schema["$defs"]["artifact_payload"]
        self.assertEqual(payload["type"], "object")
        self.assertIn(
            {"required": ["source_artifact_role", "source_artifact_sha256"]},
            payload["anyOf"],
        )
        self.assertIn(
            {"required": ["source_artifact_roles", "source_artifact_sha256_by_role"]},
            payload["anyOf"],
        )
        self.assertEqual(
            payload["dependentRequired"],
            {
                "source_artifact_role": ["source_artifact_sha256"],
                "source_artifact_sha256": ["source_artifact_role"],
                "source_artifact_roles": ["source_artifact_sha256_by_role"],
                "source_artifact_sha256_by_role": ["source_artifact_roles"],
            },
        )

    def test_valid_bundle_passes_and_cli_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = _contract()
            evidence = _evidence(root)
            adjudication = adjudicate_external_evidence(
                target_contract=contract,
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "pass")
            self.assertEqual(adjudication["issue_count"], 0)

            contract_path = root / "contract.json"
            evidence_path = root / "evidence.json"
            output_path = root / "adjudication.json"
            report_path = root / "adjudication.md"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            self.assertEqual(
                cli_main(
                    [
                        "adjudicate-opentitan-evidence",
                        "--target-contract",
                        str(contract_path),
                        "--evidence",
                        str(evidence_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                0,
            )
            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(output["status"], "pass")
            self.assertEqual(validate_adjudication_document(output)["status"], "pass")
            self.assertEqual(output["verified_identity"]["revisions"]["bad"], BAD_REVISION)
            self.assertEqual(output["verified_identity"]["revisions"]["fixed"], FIXED_REVISION)
            self.assertEqual(
                output["verified_identity"]["checkpoint_identity"],
                "checkpoint:v1:uart-profile",
            )
            self.assertEqual(
                output["verified_identity"]["oracle_identity"],
                "oracle:v1:tlul-stall",
            )
            self.assertTrue(all(check["status"] == "pass" for check in output["checks"]))
            self.assertEqual(output["input_sha256"]["target_contract"], _file_sha(contract_path))
            self.assertEqual(output["input_sha256"]["evidence"], _file_sha(evidence_path))
            graph_row = output["verified_artifacts"]["graph_artifacts"][0]
            self.assertEqual(graph_row["source_artifact_roles"], ["campaign_report"])
            self.assertEqual(
                graph_row["source_artifact_sha256_by_role"],
                {"campaign_report": evidence["source_artifacts"][3]["sha256"]},
            )
            report_row = output["verified_artifacts"]["report_artifacts"][0]
            self.assertEqual(
                report_row["source_artifact_roles"],
                ["equivalence_report", "campaign_report"],
            )
            self.assertEqual(
                report_row["source_artifact_sha256_by_role"],
                {
                    "equivalence_report": evidence["source_artifacts"][2]["sha256"],
                    "campaign_report": evidence["source_artifacts"][3]["sha256"],
                },
            )
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Status: `pass`", report)
            self.assertIn("target_contract", report)
            self.assertIn("coverage_curve", report)
            self.assertIn("runner_summary", report)
            self.assertIn("source `campaign_report`", report)
            self.assertIn(f"Bad revision: `{BAD_REVISION}`", report)
            self.assertIn("Checkpoint: `checkpoint:v1:uart-profile`", report)

    def test_cli_validates_stored_adjudication_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=_evidence(root),
                evidence_root=root,
            )
            _bind_persisted_input_hashes(adjudication)
            adjudication_path = root / "adjudication.json"
            output_path = root / "adjudication-validation.json"
            report_path = root / "adjudication-validation.md"
            adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "validate-opentitan-adjudication",
                        "--adjudication",
                        str(adjudication_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                0,
            )
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["input_sha256"]["adjudication"], _file_sha(adjudication_path))
            self.assertIn("Status: `pass`", report_path.read_text(encoding="utf-8"))

    def test_cli_validates_stored_summary_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=_evidence(root),
                evidence_root=root,
            )
            _bind_persisted_input_hashes(adjudication)
            summary = summarize_adjudications(
                [{"evidence_path": "evidence.json", "adjudication": adjudication}],
                target_contract_sha256="c" * 64,
            )
            summary_path = root / "summary.json"
            output_path = root / "summary-validation.json"
            report_path = root / "summary-validation.md"
            summary_path.write_text(json.dumps(summary), encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "validate-opentitan-adjudication-summary",
                        "--summary",
                        str(summary_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                0,
            )
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["input_sha256"]["summary"], _file_sha(summary_path))
            self.assertIn("Status: `pass`", report_path.read_text(encoding="utf-8"))

    def test_cli_output_validation_parse_error_replaces_stale_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adjudication_path = root / "adjudication.json"
            output_path = root / "adjudication-validation.json"
            report_path = root / "adjudication-validation.md"
            adjudication_path.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
            output_path.write_text('{"status":"pass"}\n', encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "validate-opentitan-adjudication",
                        "--adjudication",
                        str(adjudication_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "fail")
            self.assertIn("adjudication_input_error", _codes(report))

    def test_cli_summary_output_validation_parse_error_replaces_stale_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary_path = root / "summary.json"
            output_path = root / "summary-validation.json"
            report_path = root / "summary-validation.md"
            summary_path.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
            output_path.write_text('{"status":"pass"}\n', encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "validate-opentitan-adjudication-summary",
                        "--summary",
                        str(summary_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "fail")
            self.assertIn("adjudication_input_error", _codes(report))

    def test_campaign_orders_may_be_action_domain_permutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["campaign"]["policies"].append(
                {
                    "name": "random",
                    "orders": [["stall_d", "stall_a"]],
                    "metrics": {
                        "mean": 2,
                        "p50": 2,
                        "p95": 2,
                        "max": 2,
                        "long_tail_rate": 0,
                    },
                }
            )
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "pass")

    def test_cli_validates_target_contract_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract_path = root / "contract.json"
            output_path = root / "target-report.json"
            report_path = root / "target-report.md"
            contract_path.write_text(json.dumps(_contract()), encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "validate-opentitan-target-contract",
                        "--target-contract",
                        str(contract_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                0,
            )
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["target_count"], 1)
            self.assertEqual(report["input_sha256"]["target_contract"], _file_sha(contract_path))
            self.assertIn("tlul10818", report_path.read_text(encoding="utf-8"))

    def test_target_contract_duplicate_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = _contract()
            contract["targets"].append(copy.deepcopy(contract["targets"][0]))
            contract_path = root / "contract.json"
            output_path = root / "target-report.json"
            report_path = root / "target-report.md"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "validate-opentitan-target-contract",
                        "--target-contract",
                        str(contract_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "fail")
            self.assertIn("target_id_duplicate", _codes(report))

    def test_target_contract_parse_error_replaces_stale_pass_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract_path = root / "contract.json"
            output_path = root / "target-report.json"
            report_path = root / "target-report.md"
            contract_path.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
            output_path.write_text('{"status":"pass"}\n', encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "validate-opentitan-target-contract",
                        "--target-contract",
                        str(contract_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "fail")
            self.assertIn("adjudication_input_error", _codes(report))

    def test_cli_summarizes_multiple_evidence_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = _contract()
            valid = _evidence(root)
            invalid = copy.deepcopy(valid)
            invalid["graph_artifacts"][0]["sha256"] = "0" * 64
            contract_path = root / "contract.json"
            valid_path = root / "valid-evidence.json"
            invalid_path = root / "invalid-evidence.json"
            output_path = root / "summary.json"
            report_path = root / "summary.md"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            valid_path.write_text(json.dumps(valid), encoding="utf-8")
            invalid_path.write_text(json.dumps(invalid), encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "adjudicate-opentitan-evidence-set",
                        "--target-contract",
                        str(contract_path),
                        "--evidence",
                        str(valid_path),
                        "--evidence",
                        str(invalid_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "fail")
            self.assertEqual(validate_adjudication_summary_document(summary)["status"], "pass")
            self.assertEqual(summary["evidence_count"], 2)
            self.assertEqual(summary["pass_count"], 1)
            self.assertEqual(summary["fail_count"], 1)
            self.assertEqual(summary["check_counts"]["hash_provenance"]["fail"], 1)
            self.assertEqual(
                summary["results"][0]["verified_identity"]["revisions"]["bad"],
                BAD_REVISION,
            )
            self.assertIsNone(summary["results"][1]["verified_identity"])
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("valid-evidence.json", report)
            self.assertIn("invalid-evidence.json", report)
            self.assertIn(f"revisions bad=`{BAD_REVISION}`", report)

    def test_empty_summary_fails_closed(self) -> None:
        summary = summarize_adjudications([])
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(validate_adjudication_summary_document(summary)["status"], "pass")
        self.assertEqual(summary["evidence_count"], 1)
        self.assertEqual(summary["fail_count"], 1)
        self.assertEqual(summary["results"][0]["issue_codes"], ["adjudication_input_error"])

    def test_output_self_validation_detects_tampered_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=_evidence(root),
                evidence_root=root,
            )
            adjudication["issue_count"] = 1
            report = validate_adjudication_document(adjudication)
            self.assertEqual(report["status"], "fail")
            self.assertIn(
                "adjudication_output_issue_count_mismatch",
                {issue["code"] for issue in report["issues"]},
            )

    def test_passing_adjudication_requires_bound_input_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=_evidence(root),
                evidence_root=root,
            )
            _bind_persisted_input_hashes(base)
            self.assertEqual(validate_adjudication_document(base)["status"], "pass")
            mutations = (
                (
                    "missing_field",
                    lambda value: value.pop("input_sha256"),
                    "adjudication_output_input_sha256_invalid",
                ),
                (
                    "empty",
                    lambda value: value.__setitem__("input_sha256", {}),
                    "adjudication_output_input_sha256_missing",
                ),
                (
                    "bogus_only",
                    lambda value: value.__setitem__("input_sha256", {"bogus": "e" * 64}),
                    "adjudication_output_input_sha256_unknown",
                ),
                (
                    "missing_contract",
                    lambda value: value["input_sha256"].pop("target_contract"),
                    "adjudication_output_input_sha256_missing",
                ),
                (
                    "missing_evidence",
                    lambda value: value["input_sha256"].pop("evidence"),
                    "adjudication_output_input_sha256_missing",
                ),
            )
            for name, mutate, expected_code in mutations:
                with self.subTest(name=name):
                    adjudication = copy.deepcopy(base)
                    mutate(adjudication)
                    report = validate_adjudication_document(adjudication)
                    self.assertEqual(report["status"], "fail")
                    self.assertIn(expected_code, _codes(report))

    def test_summary_self_validation_detects_tampered_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=_evidence(root),
                evidence_root=root,
            )
            summary = summarize_adjudications(
                [{"evidence_path": "evidence.json", "adjudication": adjudication}]
            )
            mutations = (
                ("wrong_count", "pass_count", 0, "summary_output_pass_count_mismatch"),
                ("bool_evidence", "evidence_count", True, "summary_output_evidence_count_invalid"),
                ("bool_pass", "pass_count", True, "summary_output_pass_count_invalid"),
                ("bool_fail", "fail_count", False, "summary_output_fail_count_invalid"),
            )
            for name, field, value, expected_code in mutations:
                with self.subTest(name=name):
                    mutated = copy.deepcopy(summary)
                    mutated[field] = value
                    report = validate_adjudication_summary_document(mutated)
                    self.assertEqual(report["status"], "fail")
                    self.assertIn(
                        expected_code,
                        {issue["code"] for issue in report["issues"]},
                    )

    def test_passing_summary_requires_nonempty_results_and_bound_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=_evidence(root),
                evidence_root=root,
            )
            _bind_persisted_input_hashes(adjudication)
            base = summarize_adjudications(
                [{"evidence_path": "evidence.json", "adjudication": adjudication}],
                target_contract_sha256="c" * 64,
            )
            self.assertEqual(
                validate_adjudication_summary_document(base)["status"], "pass"
            )
            mutations = (
                (
                    "empty_pass",
                    lambda value: value.update(
                        {
                            "results": [],
                            "evidence_count": 0,
                            "pass_count": 0,
                            "fail_count": 0,
                            "check_counts": {
                                name: {"pass": 0, "fail": 0}
                                for name in value["check_counts"]
                            },
                        }
                    ),
                    "summary_output_results_empty",
                ),
                (
                    "missing_evidence_path",
                    lambda value: value["results"][0].pop("evidence_path"),
                    "summary_output_result_evidence_path_missing",
                ),
                (
                    "missing_target_id",
                    lambda value: value["results"][0].pop("target_id"),
                    "summary_output_result_target_id_missing",
                ),
                (
                    "missing_result_hashes",
                    lambda value: value["results"][0].pop("input_sha256"),
                    "summary_output_result_input_sha256_missing",
                ),
                (
                    "empty_result_hashes",
                    lambda value: value["results"][0].__setitem__("input_sha256", {}),
                    "summary_output_result_input_sha256_missing",
                ),
                (
                    "empty_summary_hashes",
                    lambda value: value.__setitem__("input_sha256", {}),
                    "summary_output_input_sha256_missing",
                ),
                (
                    "cross_hash_mismatch",
                    lambda value: value["results"][0]["input_sha256"].__setitem__(
                        "target_contract", "f" * 64
                    ),
                    "summary_output_input_sha256_mismatch",
                ),
            )
            for name, mutate, expected_code in mutations:
                with self.subTest(name=name):
                    summary = copy.deepcopy(base)
                    mutate(summary)
                    report = validate_adjudication_summary_document(summary)
                    self.assertEqual(report["status"], "fail")
                    self.assertIn(expected_code, _codes(report))

    def test_output_self_validation_checks_embedded_contract_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=_evidence(root),
                evidence_root=root,
            )
            _bind_persisted_input_hashes(base)
            self.assertEqual(validate_adjudication_document(base)["status"], "pass")
            mutations = (
                (
                    "count",
                    lambda value: value["target_contract"].__setitem__("target_count", 2),
                    "adjudication_output_target_contract_target_count_mismatch",
                ),
                (
                    "failing_row",
                    lambda value: value["target_contract"]["targets"][0].update(
                        {"status": "fail", "issue_count": 1}
                    ),
                    "adjudication_output_target_contract_target_failure_unreported",
                ),
                (
                    "empty_targets",
                    lambda value: value["target_contract"].update(
                        {"target_count": 0, "targets": []}
                    ),
                    "adjudication_output_target_contract_targets_empty",
                ),
                (
                    "wrong_target",
                    lambda value: value["target_contract"]["targets"][0].__setitem__(
                        "target_id", "different"
                    ),
                    "adjudication_output_target_contract_target_link_mismatch",
                ),
                (
                    "missing_target_id",
                    lambda value: value["target_contract"]["targets"][0].pop("target_id"),
                    "adjudication_output_target_contract_target_id_missing",
                ),
                (
                    "bool_index",
                    lambda value: value["target_contract"]["targets"][0].__setitem__(
                        "index", False
                    ),
                    "adjudication_output_target_contract_target_index_mismatch",
                ),
            )
            for name, mutate, expected_code in mutations:
                with self.subTest(name=name):
                    adjudication = copy.deepcopy(base)
                    mutate(adjudication)
                    report = validate_adjudication_document(adjudication)
                    self.assertEqual(report["status"], "fail")
                    self.assertIn(expected_code, _codes(report))

    def test_output_self_validation_checks_identity_and_artifact_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=_evidence(root),
                evidence_root=root,
            )
            _bind_persisted_input_hashes(base)
            self.assertEqual(validate_adjudication_document(base)["status"], "pass")
            mutations = (
                (
                    "empty_sources",
                    lambda value: value["verified_artifacts"]["source_artifacts"].clear(),
                    "adjudication_output_source_artifacts_empty",
                ),
                (
                    "source_hash",
                    lambda value: value["verified_artifacts"]["source_artifacts"][3].__setitem__(
                        "sha256", "0" * 64
                    ),
                    "adjudication_output_graph_artifacts_source_mismatch",
                ),
                (
                    "graph_source_hash",
                    lambda value: value["verified_artifacts"]["graph_artifacts"][0][
                        "source_artifact_sha256_by_role"
                    ].__setitem__("campaign_report", "0" * 64),
                    "adjudication_output_graph_artifacts_source_mismatch",
                ),
                (
                    "graph_source_map_missing",
                    lambda value: value["verified_artifacts"]["graph_artifacts"][0].pop(
                        "source_artifact_sha256_by_role"
                    ),
                    "adjudication_output_graph_artifacts_source_hashes_invalid",
                ),
                (
                    "graph_source_map_empty",
                    lambda value: value["verified_artifacts"]["graph_artifacts"][0].__setitem__(
                        "source_artifact_sha256_by_role", {}
                    ),
                    "adjudication_output_graph_artifacts_source_hashes_invalid",
                ),
                (
                    "report_source_map_empty",
                    lambda value: value["verified_artifacts"]["report_artifacts"][0].__setitem__(
                        "source_artifact_sha256_by_role", {}
                    ),
                    "adjudication_output_report_artifacts_source_hashes_invalid",
                ),
                (
                    "report_source_map_partial",
                    lambda value: value["verified_artifacts"]["report_artifacts"][0][
                        "source_artifact_sha256_by_role"
                    ].pop("campaign_report"),
                    "adjudication_output_report_artifacts_source_provenance_incomplete",
                ),
                (
                    "source_row_provenance",
                    lambda value: value["verified_artifacts"]["source_artifacts"][0].__setitem__(
                        "source_artifact_sha256_by_role", {"semantic_manifest_bad": BAD_MANIFEST}
                    ),
                    "adjudication_output_source_artifacts_source_hashes_unexpected",
                ),
                (
                    "source_role_duplicate",
                    lambda value: value["verified_artifacts"]["source_artifacts"].append(
                        copy.deepcopy(value["verified_artifacts"]["source_artifacts"][0])
                    ),
                    "adjudication_output_source_artifacts_role_duplicate",
                ),
                (
                    "manifest_identity",
                    lambda value: value["verified_identity"]["semantic_manifest_sha256"].__setitem__(
                        "bad", "0" * 64
                    ),
                    "adjudication_output_semantic_manifest_provenance_mismatch",
                ),
                (
                    "oracle_observable_overlap",
                    lambda value: value["verified_identity"].__setitem__(
                        "oracle_field", "valid_o"
                    ),
                    "adjudication_output_verified_identity_oracle_field_not_distinct",
                ),
                (
                    "unknown_output_field",
                    lambda value: value.__setitem__("unexpected", True),
                    "adjudication_output_unknown_field",
                ),
                (
                    "null_input_hash",
                    lambda value: value.__setitem__("input_sha256", {"evidence": None}),
                    "adjudication_output_input_sha256_invalid",
                ),
            )
            for name, mutate, expected_code in mutations:
                with self.subTest(name=name):
                    adjudication = copy.deepcopy(base)
                    mutate(adjudication)
                    report = validate_adjudication_document(adjudication)
                    self.assertEqual(report["status"], "fail")
                    self.assertIn(
                        expected_code,
                        {issue["code"] for issue in report["issues"]},
                    )

    def test_summary_self_validation_checks_embedded_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=_evidence(root),
                evidence_root=root,
            )
            base = summarize_adjudications(
                [{"evidence_path": "evidence.json", "adjudication": adjudication}]
            )
            base["run_spec"] = validate_adjudication_run_spec(
                {
                    "schema_version": 1,
                    "surface": OPENTITAN_ADJUDICATION_RUN_SPEC_SURFACE,
                    "target_contract": "contract.json",
                    "evidence_root": ".",
                    "evidence": ["evidence.json"],
                }
            )
            base["target_contract"] = validate_target_contract_document(_contract())
            mutations = (
                (
                    "run_spec",
                    lambda summary: summary["run_spec"].__setitem__("issue_count", 1),
                    "summary_output_run_spec_issue_count_mismatch",
                ),
                (
                    "target_contract",
                    lambda summary: summary["target_contract"].__setitem__("target_count", 2),
                    "summary_output_target_contract_target_count_mismatch",
                ),
                (
                    "result_identity",
                    lambda summary: summary["results"][0]["verified_identity"].__setitem__(
                        "campaign_action_domain_sha256", "0" * 64
                    ),
                    "summary_output_result_0_verified_identity_domain_hash_mismatch",
                ),
            )
            for name, mutate, expected_code in mutations:
                with self.subTest(name=name):
                    summary = copy.deepcopy(base)
                    mutate(summary)
                    report = validate_adjudication_summary_document(summary)
                    self.assertEqual(report["status"], "fail")
                    self.assertIn(
                        expected_code,
                        {issue["code"] for issue in report["issues"]},
                    )

    def test_cli_summary_parse_error_replaces_stale_pass_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract_path = root / "contract.json"
            evidence_path = root / "bad-evidence.json"
            output_path = root / "summary.json"
            report_path = root / "summary.md"
            contract_path.write_text(json.dumps(_contract()), encoding="utf-8")
            evidence_path.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
            output_path.write_text('{"status":"pass"}\n', encoding="utf-8")

            self.assertEqual(
                cli_main(
                    [
                        "adjudicate-opentitan-evidence-set",
                        "--target-contract",
                        str(contract_path),
                        "--evidence",
                        str(evidence_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "fail")
            self.assertEqual(summary["check_counts"]["input_format"]["fail"], 1)
            self.assertIn("bad-evidence.json", report_path.read_text(encoding="utf-8"))

    def test_cli_adjudicates_from_run_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract_path = root / "contract.json"
            evidence_path = root / "evidence.json"
            run_spec_path = root / "run-spec.json"
            output_path = root / "run-summary.json"
            report_path = root / "run-summary.md"
            contract_path.write_text(json.dumps(_contract()), encoding="utf-8")
            evidence_path.write_text(json.dumps(_evidence(root)), encoding="utf-8")
            run_spec_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "surface": "opentitan_regression_adjudication_run_spec",
                        "target_contract": "contract.json",
                        "evidence_root": ".",
                        "evidence": ["evidence.json"],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                cli_main(
                    [
                        "adjudicate-opentitan-run-spec",
                        "--run-spec",
                        str(run_spec_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                0,
            )
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(summary["evidence_count"], 1)
            self.assertEqual(summary["input_sha256"]["run_spec"], _file_sha(run_spec_path))
            self.assertEqual(summary["run_spec"]["status"], "pass")
            self.assertEqual(summary["target_contract"]["status"], "pass")
            self.assertIn("evidence.json", report_path.read_text(encoding="utf-8"))

    def test_run_spec_fails_on_invalid_unused_target_contract_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = _contract()
            broken_target = copy.deepcopy(contract["targets"][0])
            broken_target["target_id"] = "unused"
            broken_target["semantic_manifest_sha256"]["bad"] = "not-a-sha"
            contract["targets"].append(broken_target)
            contract_path = root / "contract.json"
            evidence_path = root / "evidence.json"
            run_spec_path = root / "run-spec.json"
            output_path = root / "run-summary.json"
            report_path = root / "run-summary.md"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            evidence_path.write_text(json.dumps(_evidence(root)), encoding="utf-8")
            run_spec_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "surface": "opentitan_regression_adjudication_run_spec",
                        "target_contract": "contract.json",
                        "evidence_root": ".",
                        "evidence": ["evidence.json"],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                cli_main(
                    [
                        "adjudicate-opentitan-run-spec",
                        "--run-spec",
                        str(run_spec_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "fail")
            self.assertEqual(summary["target_contract"]["status"], "fail")
            self.assertIn("target_semantic_manifest_sha_invalid", _codes(summary["target_contract"]))

    def test_run_spec_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_spec_path = root / "run-spec.json"
            output_path = root / "run-summary.json"
            report_path = root / "run-summary.md"
            run_spec_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "surface": "opentitan_regression_adjudication_run_spec",
                        "target_contract": "../contract.json",
                        "evidence_root": ".",
                        "evidence": ["evidence.json"],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                cli_main(
                    [
                        "adjudicate-opentitan-run-spec",
                        "--run-spec",
                        str(run_spec_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "fail")
            self.assertEqual(summary["run_spec"]["status"], "fail")
            self.assertIn(
                "run_spec_target_contract_invalid",
                summary["run_spec"]["checks"][1]["issue_codes"],
            )

    def test_run_spec_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            outside_contract = Path(outside) / "contract.json"
            outside_contract.write_text(json.dumps(_contract()), encoding="utf-8")
            try:
                os.symlink(outside_contract, root / "contract-link.json")
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            run_spec_path = root / "run-spec.json"
            output_path = root / "run-summary.json"
            report_path = root / "run-summary.md"
            run_spec_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "surface": "opentitan_regression_adjudication_run_spec",
                        "target_contract": "contract-link.json",
                        "evidence_root": ".",
                        "evidence": ["evidence.json"],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                cli_main(
                    [
                        "adjudicate-opentitan-run-spec",
                        "--run-spec",
                        str(run_spec_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "fail")
            self.assertIn("run spec path escapes", report_path.read_text(encoding="utf-8"))

    def test_run_spec_rejects_evidence_symlink_escape_and_writes_fail_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            outside_evidence = Path(outside) / "evidence.json"
            outside_evidence.write_text(json.dumps(_evidence(root)), encoding="utf-8")
            try:
                os.symlink(outside_evidence, root / "evidence-link.json")
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            contract_path = root / "contract.json"
            run_spec_path = root / "run-spec.json"
            output_path = root / "run-summary.json"
            report_path = root / "run-summary.md"
            contract_path.write_text(json.dumps(_contract()), encoding="utf-8")
            output_path.write_text('{"status":"pass"}\n', encoding="utf-8")
            run_spec_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "surface": "opentitan_regression_adjudication_run_spec",
                        "target_contract": "contract.json",
                        "evidence_root": ".",
                        "evidence": ["evidence-link.json"],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                cli_main(
                    [
                        "adjudicate-opentitan-run-spec",
                        "--run-spec",
                        str(run_spec_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "fail")
            self.assertIn("run spec path escapes", report_path.read_text(encoding="utf-8"))

    def test_duplicate_json_key_is_rejected_by_strict_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "duplicate.json"
            path.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
            with self.assertRaisesRegex(OpenTitanEvidenceError, "duplicate JSON key"):
                read_strict_json_object(path)

            nonfinite = root / "nonfinite.json"
            nonfinite.write_text('{"value":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(OpenTitanEvidenceError, "non-finite JSON number"):
                read_strict_json_object(nonfinite)

            overflow_float = root / "overflow-float.json"
            overflow_float.write_text(
                '{"value":1e' + str(sys.float_info.max_10_exp + 1) + "}",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(OpenTitanEvidenceError, "non-finite JSON number"):
                read_strict_json_object(overflow_float)

            get_digit_limit = getattr(sys, "get_int_max_str_digits", None)
            if get_digit_limit is not None and get_digit_limit() > 0:
                oversized_integer = root / "oversized-integer.json"
                oversized_integer.write_text(
                    '{"value":' + "1" * (get_digit_limit() + 1) + "}",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(OpenTitanEvidenceError, "invalid JSON"):
                    read_strict_json_object(oversized_integer)

            invalid_utf8 = root / "invalid-utf8.json"
            invalid_utf8.write_bytes(b"\xff")
            with self.assertRaisesRegex(OpenTitanEvidenceError, "invalid JSON"):
                read_strict_json_object(invalid_utf8)

            lone_surrogate = root / "lone-surrogate.json"
            lone_surrogate.write_text('{"value":"\\ud800"}', encoding="utf-8")
            with self.assertRaisesRegex(OpenTitanEvidenceError, "lone Unicode surrogate"):
                read_strict_json_object(lone_surrogate)

    def test_bool_schema_versions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = _contract()
            evidence = _evidence(root)
            contract["schema_version"] = True
            evidence["seed_corpora"]["coverage_gain"][0]["schema_version"] = True
            evidence["seed_corpora"]["oracle_violation"][0]["schema_version"] = True
            adjudication = adjudicate_external_evidence(
                target_contract=contract,
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            codes = _codes(adjudication)
            self.assertIn("contract_schema_version_mismatch", codes)
            self.assertIn("coverage_gain_schema_version_invalid", codes)
            self.assertIn("oracle_violation_schema_version_invalid", codes)

    def test_target_and_run_spec_schema_shape_fail_closed(self) -> None:
        contract_cases = (
            (
                "contract_root_unknown",
                lambda contract: contract.__setitem__("unexpected", True),
                "contract_unknown_field",
            ),
            (
                "target_unknown",
                lambda contract: contract["targets"][0].__setitem__("unexpected", True),
                "target_unknown_field",
            ),
            (
                "same_revision",
                lambda contract: contract["targets"][0]["revisions"].__setitem__(
                    "fixed", BAD_REVISION
                ),
                "target_revisions_not_distinct",
            ),
        )
        for name, mutate, expected_code in contract_cases:
            with self.subTest(name=name):
                contract = _contract()
                mutate(contract)
                report = validate_target_contract_document(contract)
                self.assertEqual(report["status"], "fail")
                self.assertIn(expected_code, _codes(report))

        valid_run_spec = {
            "schema_version": 1,
            "surface": OPENTITAN_ADJUDICATION_RUN_SPEC_SURFACE,
            "target_contract": "contract.json",
            "evidence_root": ".",
            "evidence": ["evidence.json"],
        }
        run_spec_cases = (
            (
                "run_spec_unknown",
                lambda run_spec: run_spec.__setitem__("unexpected", True),
                "run_spec_unknown_field",
            ),
            (
                "missing_evidence_root",
                lambda run_spec: run_spec.pop("evidence_root"),
                "run_spec_evidence_root_invalid",
            ),
            (
                "nul_evidence_path",
                lambda run_spec: run_spec.__setitem__("evidence", ["bad\x00path.json"]),
                "run_spec_evidence_path_invalid",
            ),
            (
                "newline_evidence_path",
                lambda run_spec: run_spec.__setitem__(
                    "evidence", ["bad\npath.json"]
                ),
                "run_spec_evidence_path_invalid",
            ),
        )
        for name, mutate, expected_code in run_spec_cases:
            with self.subTest(name=name):
                run_spec = copy.deepcopy(valid_run_spec)
                mutate(run_spec)
                report = validate_adjudication_run_spec(run_spec)
                self.assertEqual(report["status"], "fail")
                self.assertIn(expected_code, _codes(report))

    def test_evidence_schema_unknown_fields_fail_closed(self) -> None:
        mutations = (
            (
                "root",
                lambda evidence: evidence.__setitem__("unexpected", True),
                "evidence_unknown_field",
            ),
            (
                "target",
                lambda evidence: evidence["target"].__setitem__("unexpected", True),
                "evidence_target_unknown_field",
            ),
            (
                "runner",
                lambda evidence: evidence["runner"].__setitem__("unexpected", True),
                "runner_unknown_field",
            ),
            (
                "revision_result",
                lambda evidence: evidence["revision_results"]["bad"].__setitem__(
                    "unexpected", True
                ),
                "revision_result_unknown_field",
            ),
            (
                "observation",
                lambda evidence: evidence["revision_results"]["bad"]["observations"][0].__setitem__(
                    "unexpected", True
                ),
                "observation_unknown_field",
            ),
            (
                "coverage_seed",
                lambda evidence: evidence["seed_corpora"]["coverage_gain"][0].__setitem__(
                    "unexpected", True
                ),
                "coverage_gain_unknown_field",
            ),
            (
                "campaign_metrics",
                lambda evidence: evidence["campaign"]["policies"][0]["metrics"].__setitem__(
                    "unexpected", True
                ),
                "campaign_metrics_unknown_field",
            ),
            (
                "source_artifact",
                lambda evidence: evidence["source_artifacts"][0].__setitem__(
                    "unexpected", True
                ),
                "source_artifacts_unknown_field",
            ),
            (
                "graph_artifact",
                lambda evidence: evidence["graph_artifacts"][0].__setitem__(
                    "unexpected", True
                ),
                "graph_artifacts_unknown_field",
            ),
        )
        for name, mutate, expected_code in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                evidence = _evidence(root)
                mutate(evidence)
                adjudication = adjudicate_external_evidence(
                    target_contract=_contract(),
                    evidence=evidence,
                    evidence_root=root,
                )
                self.assertEqual(adjudication["status"], "fail")
                self.assertIn(expected_code, _codes(adjudication))

    def test_malformed_revision_results_and_unobserved_failure_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            malformed = _evidence(root)
            malformed["revision_results"] = []
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=malformed,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("revision_results_invalid", _codes(adjudication))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing_observation = _evidence(root)
            missing_observation["revision_results"]["bad"]["observations"] = [
                missing_observation["revision_results"]["bad"]["observations"][1]
            ]
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=missing_observation,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("failure_sequence_not_observed", _codes(adjudication))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing_nonfailure_observation = _evidence(root)
            missing_nonfailure_observation["revision_results"]["bad"]["observations"] = [
                missing_nonfailure_observation["revision_results"]["bad"]["observations"][0]
            ]
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=missing_nonfailure_observation,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("executed_sequence_not_observed", _codes(adjudication))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid_sequence = _evidence(root)
            invalid_sequence["revision_results"]["bad"][
                "oracle_failure_action_sequences"
            ] = [[1]]
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=invalid_sequence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("failure_sequence_invalid", _codes(adjudication))

    def test_invalid_target_domain_fails_closed_without_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = _contract()
            contract["targets"][0]["campaign_action_domain"] = [["not-hashable"]]
            adjudication = adjudicate_external_evidence(
                target_contract=contract,
                evidence=_evidence(root),
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            codes = _codes(adjudication)
            self.assertIn("target_campaign_domain_invalid", codes)
            self.assertIn("action_domain_mismatch", codes)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = _contract()
            contract["targets"][0]["semantic_observables"] = 1
            adjudication = adjudicate_external_evidence(
                target_contract=contract,
                evidence=_evidence(root),
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn(
                "target_semantic_observables_invalid", _codes(adjudication)
            )

    def test_semantic_cpu_gpu_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["revision_results"]["bad"]["observations"][0]["gpu"]["data_o"] = 8
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("semantic_cpu_gpu_mismatch", _codes(adjudication))

    def test_semantic_types_and_bad_oracle_declarations_fail_closed(self) -> None:
        mutations = (
            (
                "bool_vs_int_semantic",
                lambda evidence: evidence["revision_results"]["bad"]["observations"][0][
                    "cpu"
                ].__setitem__("valid_o", True),
                "semantic_cpu_gpu_mismatch",
            ),
            (
                "bool_oracle",
                lambda evidence: (
                    evidence["revision_results"]["bad"]["observations"][0]["cpu"].__setitem__(
                        "oracle_violation", True
                    ),
                    evidence["revision_results"]["bad"]["observations"][0]["gpu"].__setitem__(
                        "oracle_violation", True
                    ),
                ),
                "oracle_value_invalid",
            ),
            (
                "undeclared_bad_failure",
                lambda evidence: (
                    evidence["revision_results"]["bad"]["observations"][1]["cpu"].__setitem__(
                        "oracle_violation", 1
                    ),
                    evidence["revision_results"]["bad"]["observations"][1]["gpu"].__setitem__(
                        "oracle_violation", 1
                    ),
                ),
                "bad_oracle_assertion_not_declared_failure",
            ),
        )
        for name, mutate, expected_code in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                evidence = _evidence(root)
                mutate(evidence)
                adjudication = adjudicate_external_evidence(
                    target_contract=_contract(),
                    evidence=evidence,
                    evidence_root=root,
                )
                self.assertEqual(adjudication["status"], "fail")
                self.assertIn(expected_code, _codes(adjudication))

    def test_oracle_seed_is_verified_one_minimal(self) -> None:
        def add_long_sequence(evidence: dict, *, retain_short_failure: bool) -> None:
            sequence = ["stall_a", "stall_a"]
            bad_observation = copy.deepcopy(
                evidence["revision_results"]["bad"]["observations"][0]
            )
            bad_observation["sequence"] = sequence
            fixed_observation = copy.deepcopy(bad_observation)
            fixed_observation["cpu"]["oracle_violation"] = 0
            fixed_observation["gpu"]["oracle_violation"] = 0
            for label, observation in (
                ("bad", bad_observation),
                ("fixed", fixed_observation),
            ):
                evidence["revision_results"][label]["executed_action_sequences"].append(
                    sequence
                )
                evidence["revision_results"][label]["observations"].append(observation)
            if retain_short_failure:
                evidence["revision_results"]["bad"]["oracle_failure_action_sequences"].append(
                    sequence
                )
            else:
                evidence["revision_results"]["bad"]["oracle_failure_action_sequences"] = [
                    sequence
                ]
                evidence["revision_results"]["bad"]["observations"][0]["cpu"][
                    "oracle_violation"
                ] = 0
                evidence["revision_results"]["bad"]["observations"][0]["gpu"][
                    "oracle_violation"
                ] = 0
            evidence["seed_corpora"]["oracle_violation"][0]["sequence"] = sequence

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            add_long_sequence(evidence, retain_short_failure=True)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(), evidence=evidence, evidence_root=root
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("oracle_violation_not_minimal", _codes(adjudication))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            add_long_sequence(evidence, retain_short_failure=False)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(), evidence=evidence, evidence_root=root
            )
            self.assertEqual(adjudication["status"], "pass")

    def test_fixed_revision_oracle_failure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["revision_results"]["fixed"]["oracle_failure_action_sequences"] = [["stall_a"]]
            evidence["revision_results"]["fixed"]["observations"][0]["cpu"]["oracle_violation"] = 1
            evidence["revision_results"]["fixed"]["observations"][0]["gpu"]["oracle_violation"] = 1
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("fixed_revision_failure", _codes(adjudication))
            self.assertIn("fixed_observation_oracle_asserted", _codes(adjudication))

    def test_hash_and_corpus_errors_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["graph_artifacts"][0]["sha256"] = "0" * 64
            evidence["seed_corpora"]["coverage_gain"][0]["coverage_delta_bits"] = "00"
            evidence["seed_corpora"]["oracle_violation"][0]["source_revision"] = "fixed"
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            codes = _codes(adjudication)
            self.assertIn("graph_artifacts_hash_mismatch", codes)
            self.assertIn("coverage_gain_delta_empty", codes)
            self.assertIn("oracle_violation_revision_invalid", codes)

    def test_artifact_boundary_and_required_source_roles_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["graph_artifacts"][0]["path"] = "bad\x00path.svg"
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("graph_artifacts_path_invalid", _codes(adjudication))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["graph_artifacts"][0]["path"] = "../outside.svg"
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("graph_artifacts_path_invalid", _codes(adjudication))

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "evidence-root"
            root.mkdir()
            evidence = _evidence(root)
            outside = base / "outside.svg"
            outside.write_text("<svg></svg>\n", encoding="utf-8")
            try:
                os.symlink(outside, root / "escape.svg")
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            evidence["graph_artifacts"][0]["path"] = "escape.svg"
            evidence["graph_artifacts"][0]["sha256"] = _file_sha(outside)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("graph_artifacts_path_invalid", _codes(adjudication))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            (root / "manifests" / "bad.json").write_text(
                '{"semantic":"tampered"}\n', encoding="utf-8"
            )
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("source_artifacts_hash_mismatch", _codes(adjudication))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            del evidence["source_artifacts"][2]
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("source_artifact_role_missing", _codes(adjudication))

    def test_semantic_manifest_content_is_bound_to_target(self) -> None:
        def repin_bad_manifest(
            root: Path, contract: dict, evidence: dict, payload: bytes
        ) -> None:
            path = root / "manifests" / "bad.json"
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            contract["targets"][0]["semantic_manifest_sha256"]["bad"] = digest
            evidence["semantic_manifest_sha256"]["bad"] = digest
            evidence["source_artifacts"][0]["sha256"] = digest

        cases = (
            (
                "not_json",
                lambda: b"\x00not-json",
                "semantic_manifest_bad_parse_error",
            ),
            (
                "revision",
                lambda: (
                    json.dumps(
                        {
                            **_semantic_manifest("bad", BAD_REVISION),
                            "revision_sha": "f" * 40,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
                "semantic_manifest_bad_revision_sha_mismatch",
            ),
            (
                "observable",
                lambda: (
                    json.dumps(
                        {
                            **_semantic_manifest("bad", BAD_REVISION),
                            "observables": [
                                {
                                    **_semantic_manifest("bad", BAD_REVISION)["observables"][0],
                                    "name": "different",
                                },
                                *_semantic_manifest("bad", BAD_REVISION)["observables"][1:],
                            ],
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8"),
                "semantic_manifest_bad_observable_names_mismatch",
            ),
        )
        for name, make_payload, expected_code in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                contract = _contract()
                evidence = _evidence(root)
                repin_bad_manifest(root, contract, evidence, make_payload())
                adjudication = adjudicate_external_evidence(
                    target_contract=contract,
                    evidence=evidence,
                    evidence_root=root,
                )
                self.assertEqual(adjudication["status"], "fail")
                self.assertIn(expected_code, _codes(adjudication))

    def test_campaign_metrics_must_be_bounded_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            metrics = evidence["campaign"]["policies"][0]["metrics"]
            metrics["p50"] = 2
            metrics["p95"] = 1
            metrics["max"] = 3
            metrics["long_tail_rate"] = 2
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            codes = _codes(adjudication)
            self.assertIn("campaign_metric_order_invalid", codes)
            self.assertIn("campaign_metric_out_of_range", codes)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["campaign"]["policies"][0]["metrics"]["mean"] = 10 ** (
                sys.float_info.max_10_exp + 1
            )
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(), evidence=evidence, evidence_root=root
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("campaign_metric_out_of_range", _codes(adjudication))

    def test_graph_payload_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["graph_artifacts"][0]["payload"]["policies"] = ["changed"]
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("graph_artifacts_payload_hash_mismatch", _codes(adjudication))

    def test_failed_adjudication_remains_structurally_self_validating(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["graph_artifacts"][0]["payload_sha256"] = "invalid"
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(), evidence=evidence, evidence_root=root
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("graph_artifacts_payload_sha_invalid", _codes(adjudication))
            _bind_persisted_input_hashes(adjudication)
            self.assertEqual(
                validate_adjudication_document(adjudication)["status"], "pass"
            )
            self.assertNotIn(
                "payload_sha256",
                adjudication["verified_artifacts"]["graph_artifacts"][0],
            )

    def test_failed_producer_diagnostics_remain_self_validating(self) -> None:
        cases = (
            (
                "normalized_parent_path",
                lambda contract, evidence: evidence["graph_artifacts"][0].__setitem__(
                    "path", "graphs/../graphs/coverage.svg"
                ),
                "graph_artifacts_path_invalid",
            ),
            (
                "duplicate_artifact_role",
                lambda contract, evidence: evidence["source_artifacts"].append(
                    copy.deepcopy(evidence["source_artifacts"][2])
                ),
                "source_artifacts_role_duplicate",
            ),
            (
                "source_payload_field",
                lambda contract, evidence: evidence["source_artifacts"][0].__setitem__(
                    "payload_sha256", "0" * 64
                ),
                "source_artifacts_unknown_field",
            ),
            (
                "empty_target_id",
                lambda contract, evidence: evidence.__setitem__("target_id", ""),
                "target_id_invalid",
            ),
            (
                "duplicate_contract_target",
                lambda contract, evidence: contract["targets"].append(
                    copy.deepcopy(contract["targets"][0])
                ),
                "target_id_duplicate",
            ),
        )
        for name, mutate, expected_code in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                contract = _contract()
                evidence = _evidence(root)
                mutate(contract, evidence)
                adjudication = adjudicate_external_evidence(
                    target_contract=contract,
                    evidence=evidence,
                    evidence_root=root,
                )
                self.assertEqual(adjudication["status"], "fail")
                self.assertIn(expected_code, _codes(adjudication))
                _bind_persisted_input_hashes(adjudication)
                self.assertEqual(
                    validate_adjudication_document(adjudication)["status"], "pass"
                )
                summary = summarize_adjudications(
                    [{"evidence_path": "evidence.json", "adjudication": adjudication}],
                    target_contract_sha256="c" * 64,
                )
                self.assertEqual(summary["status"], "fail")
                self.assertEqual(
                    validate_adjudication_summary_document(summary)["status"],
                    "pass",
                )

    def test_graph_payload_without_source_provenance_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            payload = evidence["graph_artifacts"][0]["payload"]
            del payload["source_artifact_role"]
            del payload["source_artifact_sha256"]
            evidence["graph_artifacts"][0]["payload_sha256"] = _canonical_sha(payload)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("graph_artifacts_payload_provenance_missing", _codes(adjudication))

    def test_graph_payload_source_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            payload = evidence["graph_artifacts"][0]["payload"]
            payload["source_artifact_sha256"] = "0" * 64
            evidence["graph_artifacts"][0]["payload_sha256"] = _canonical_sha(payload)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("graph_artifacts_payload_source_sha_mismatch", _codes(adjudication))

    def test_report_payload_unknown_source_role_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            payload = evidence["report_artifacts"][0]["payload"]
            payload["source_artifact_roles"] = ["not_a_source_artifact"]
            payload["source_artifact_sha256_by_role"] = {"not_a_source_artifact": "0" * 64}
            evidence["report_artifacts"][0]["payload_sha256"] = _canonical_sha(payload)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("report_artifacts_payload_source_role_unknown", _codes(adjudication))

    def test_report_payload_source_hash_map_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            payload = evidence["report_artifacts"][0]["payload"]
            payload["source_artifact_sha256_by_role"]["campaign_report"] = "0" * 64
            evidence["report_artifacts"][0]["payload_sha256"] = _canonical_sha(payload)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("report_artifacts_payload_source_sha_mismatch", _codes(adjudication))

    def test_report_payload_source_roles_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            payload = evidence["report_artifacts"][0]["payload"]
            payload["source_artifact_roles"] = [
                "equivalence_report",
                "equivalence_report",
            ]
            payload["source_artifact_sha256_by_role"] = {
                "equivalence_report": evidence["source_artifacts"][2]["sha256"]
            }
            evidence["report_artifacts"][0]["payload_sha256"] = _canonical_sha(payload)
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(), evidence=evidence, evidence_root=root
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn(
                "report_artifacts_payload_source_roles_invalid",
                _codes(adjudication),
            )

    def test_payload_provenance_rejects_invalid_unselected_fields(self) -> None:
        cases = (
            (
                "graph_artifacts",
                "source_artifact_sha256_by_role",
                [],
                "graph_artifacts_payload_source_sha_map_invalid",
            ),
            (
                "report_artifacts",
                "source_artifact_sha256",
                [],
                "report_artifacts_payload_source_sha_invalid",
            ),
        )
        for artifact_field, payload_field, value, expected_code in cases:
            with self.subTest(payload_field=payload_field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                evidence = _evidence(root)
                payload = evidence[artifact_field][0]["payload"]
                payload[payload_field] = value
                evidence[artifact_field][0]["payload_sha256"] = _canonical_sha(payload)
                adjudication = adjudicate_external_evidence(
                    target_contract=_contract(), evidence=evidence, evidence_root=root
                )
                self.assertEqual(adjudication["status"], "fail")
                self.assertIn(expected_code, _codes(adjudication))

        selector_cases = (
            (
                "report_artifacts",
                "source_artifact_role",
                "source_artifact_sha256",
                "0" * 64,
                "report_artifacts_payload_source_role_invalid",
            ),
            (
                "graph_artifacts",
                "source_artifact_roles",
                "source_artifact_sha256_by_role",
                {"campaign_report": "0" * 64},
                "graph_artifacts_payload_source_roles_invalid",
            ),
        )
        for artifact_field, selector, digest_field, digest, expected_code in selector_cases:
            with self.subTest(selector=selector), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                evidence = _evidence(root)
                payload = evidence[artifact_field][0]["payload"]
                payload[selector] = None
                payload[digest_field] = digest
                evidence[artifact_field][0]["payload_sha256"] = _canonical_sha(payload)
                adjudication = adjudicate_external_evidence(
                    target_contract=_contract(), evidence=evidence, evidence_root=root
                )
                self.assertEqual(adjudication["status"], "fail")
                self.assertIn(expected_code, _codes(adjudication))

    def test_action_sequence_identity_has_no_delimiter_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = _contract()
            domain = ["a", "b", "a\x00b"]
            contract["targets"][0]["campaign_action_domain"] = domain
            contract["targets"][0]["reproduction_action_domain"] = ["a\x00b"]
            evidence = _evidence(root)
            evidence["action_domain"] = domain
            evidence["action_domain_sha256"] = _canonical_sha(domain)
            bad_observation = copy.deepcopy(
                evidence["revision_results"]["bad"]["observations"][0]
            )
            bad_observation["sequence"] = ["a", "b"]
            fixed_observation = copy.deepcopy(bad_observation)
            fixed_observation["cpu"]["oracle_violation"] = 0
            fixed_observation["gpu"]["oracle_violation"] = 0
            evidence["revision_results"] = {
                "bad": {
                    "executed_action_sequences": [["a", "b"]],
                    "oracle_failure_action_sequences": [["a", "b"]],
                    "observations": [bad_observation],
                },
                "fixed": {
                    "executed_action_sequences": [["a", "b"]],
                    "oracle_failure_action_sequences": [],
                    "observations": [fixed_observation],
                },
            }
            evidence["seed_corpora"]["coverage_gain"][0]["sequence"] = ["a", "b"]
            evidence["seed_corpora"]["oracle_violation"][0]["sequence"] = ["a\x00b"]
            evidence["campaign"]["policies"][0]["orders"] = [domain]
            evidence["campaign"]["policies"][0]["metrics"].update(
                {"mean": 3, "p50": 3, "p95": 3, "max": 3}
            )
            adjudication = adjudicate_external_evidence(
                target_contract=contract, evidence=evidence, evidence_root=root
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("oracle_violation_not_bad_failure", _codes(adjudication))
            self.assertIn(
                "oracle_violation_fixed_sequence_not_observed",
                _codes(adjudication),
            )

    def test_oracle_violation_must_use_reproduction_domain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["revision_results"]["bad"]["oracle_failure_action_sequences"] = [["stall_d"]]
            evidence["seed_corpora"]["oracle_violation"][0]["sequence"] = ["stall_d"]
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("oracle_violation_outside_reproduction_domain", _codes(adjudication))

    def test_oracle_violation_must_be_observed_on_fixed_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = _evidence(root)
            evidence["revision_results"]["fixed"]["executed_action_sequences"] = [["stall_d"]]
            evidence["revision_results"]["fixed"]["observations"] = [
                evidence["revision_results"]["fixed"]["observations"][1]
            ]
            adjudication = adjudicate_external_evidence(
                target_contract=_contract(),
                evidence=evidence,
                evidence_root=root,
            )
            self.assertEqual(adjudication["status"], "fail")
            self.assertIn("oracle_violation_fixed_sequence_not_observed", _codes(adjudication))

    def test_cli_parse_error_replaces_stale_pass_output_with_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract_path = root / "contract.json"
            evidence_path = root / "evidence.json"
            output_path = root / "adjudication.json"
            report_path = root / "adjudication.md"
            contract_path.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
            evidence_path.write_text(json.dumps(_evidence(root)), encoding="utf-8")
            output_path.write_text('{"status":"pass"}\n', encoding="utf-8")
            self.assertEqual(
                cli_main(
                    [
                        "adjudicate-opentitan-evidence",
                        "--target-contract",
                        str(contract_path),
                        "--evidence",
                        str(evidence_path),
                        "--output",
                        str(output_path),
                        "--report",
                        str(report_path),
                    ]
                ),
                1,
            )
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["status"], "fail")
            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(output["checks"][0]["name"], "input_format")
            self.assertEqual(output["checks"][0]["status"], "fail")
            self.assertIsNone(output["verified_identity"])
            self.assertEqual(validate_adjudication_document(output)["status"], "pass")
            self.assertIn("adjudication_input_error", report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
