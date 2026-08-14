"""Static adjudication for externally generated OpenTitan RTL evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence


OPENTITAN_TARGET_CONTRACT_SURFACE = "opentitan_regression_target_contract"
OPENTITAN_EVIDENCE_BUNDLE_SURFACE = "opentitan_external_regression_evidence_bundle"
OPENTITAN_ADJUDICATION_SURFACE = "opentitan_regression_adjudication"
OPENTITAN_ADJUDICATION_SUMMARY_SURFACE = "opentitan_regression_adjudication_summary"
OPENTITAN_ADJUDICATION_VALIDATION_REPORT_SURFACE = "opentitan_regression_adjudication_validation_report"
OPENTITAN_ADJUDICATION_SUMMARY_VALIDATION_REPORT_SURFACE = "opentitan_regression_adjudication_summary_validation_report"
OPENTITAN_ADJUDICATION_RUN_SPEC_SURFACE = "opentitan_regression_adjudication_run_spec"
OPENTITAN_ADJUDICATION_RUN_SPEC_REPORT_SURFACE = "opentitan_regression_adjudication_run_spec_report"
OPENTITAN_TARGET_CONTRACT_REPORT_SURFACE = "opentitan_regression_target_contract_report"
OPENTITAN_SEMANTIC_MANIFEST_SURFACE = "opentitan_regression_semantic_manifest"
OPENTITAN_EVIDENCE_SCHEMA_VERSION = 1

_REVISION_LABELS = ("bad", "fixed")
_HEX40 = frozenset("0123456789abcdef")
_HEX64 = _HEX40
_CHECK_NAMES = (
    "input_format",
    "run_spec",
    "target_contract",
    "bundle_identity",
    "semantic_cpu_gpu_equality",
    "bad_fixed_oracle_delta",
    "seed_corpora",
    "campaign",
    "hash_provenance",
)


class OpenTitanEvidenceError(RuntimeError):
    """Raised when an evidence file cannot be parsed as a strict JSON object."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file."""

    return _sha256_file(path)


def _strict_object_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OpenTitanEvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(token: str) -> None:
    raise OpenTitanEvidenceError(f"non-finite JSON number is not permitted: {token}")


def _strict_json_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise OpenTitanEvidenceError(
            f"non-finite JSON number is not permitted: {token}"
        )
    return value


def _lone_surrogate_path(value: Any, path: str = "$") -> str | None:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            return path
        return None
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_path = _lone_surrogate_path(key, f"{path}.<key>")
            if key_path is not None:
                return key_path
            child_path = _lone_surrogate_path(child, f"{path}.{key}")
            if child_path is not None:
                return child_path
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = _lone_surrogate_path(child, f"{path}[{index}]")
            if child_path is not None:
                return child_path
    return None


def read_strict_json_object(path: Path) -> dict[str, Any]:
    """Read one JSON object while rejecting duplicate keys and NaN/Infinity."""

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_strict_json_float,
        )
    except (ValueError, UnicodeError) as error:
        raise OpenTitanEvidenceError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise OpenTitanEvidenceError(f"{path}: JSON root must be an object")
    invalid_unicode_path = _lone_surrogate_path(value)
    if invalid_unicode_path is not None:
        raise OpenTitanEvidenceError(
            f"{path}: lone Unicode surrogate is not permitted at {invalid_unicode_path}"
        )
    return value


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically replace a JSON output file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(encoded)
    os.replace(temporary, path)


def write_text_atomic(path: Path, text: str) -> None:
    """Atomically replace a text output file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    os.replace(temporary, path)


def adjudication_input_error(detail: str) -> dict[str, Any]:
    """Return a fail-closed adjudication for an unreadable evidence input."""

    issue = {
        "code": "adjudication_input_error",
        "detail": detail,
    }
    return {
        "schema_version": OPENTITAN_EVIDENCE_SCHEMA_VERSION,
        "surface": OPENTITAN_ADJUDICATION_SURFACE,
        "target_id": None,
        "status": "fail",
        "verified_identity": None,
        "checks": _build_checks([issue]),
        "issue_count": 1,
        "issues": [issue],
        "verified_artifacts": {
            "source_artifacts": [],
            "graph_artifacts": [],
            "report_artifacts": [],
        },
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def _is_finite_number(value: Any) -> bool:
    if _is_int(value):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _json_values_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _json_values_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    if isinstance(left, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    return left == right


def _has_schema_version(value: Any) -> bool:
    return _is_int(value) and value == OPENTITAN_EVIDENCE_SCHEMA_VERSION


def _hex(value: Any, width: int) -> bool:
    if not isinstance(value, str) or len(value) != width:
        return False
    allowed = _HEX40 if width == 40 else _HEX64
    return all(character in allowed for character in value)


def _nonempty_unique_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, str) and item for item in value)
        and len(set(value)) == len(value)
    )


def _string_list(value: Any) -> list[str]:
    if not _nonempty_unique_strings(value):
        return []
    return list(value)


def _sequence_key(sequence: Sequence[str]) -> tuple[str, ...]:
    return tuple(sequence)


def _valid_sequence(value: Any, domain: set[str]) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, str) and item in domain for item in value)
    )


def _issue(issues: list[dict[str, str]], code: str, detail: str) -> None:
    issues.append({"code": code, "detail": detail})


def _reject_unknown_keys(
    value: Any,
    allowed: set[str],
    issues: list[dict[str, str]],
    code: str,
    label: str,
) -> None:
    if not isinstance(value, Mapping):
        return
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        _issue(issues, code, f"{label} has unknown fields: {unknown}")


def _check_name_for_issue(code: str) -> str:
    if code == "adjudication_input_error":
        return "input_format"
    if code.startswith("run_spec_"):
        return "run_spec"
    if code in {
        "evidence_surface_mismatch",
        "evidence_schema_version_mismatch",
        "target_identity_mismatch",
        "identity_mismatch",
        "revision_mismatch",
        "semantic_manifest_mismatch",
        "action_domain_mismatch",
        "action_domain_hash_mismatch",
        "runner_status_invalid",
        "runner_identity_invalid",
        "runner_completed_at_invalid",
    } or code.startswith(("evidence_", "runner_")):
        return "bundle_identity"
    if code in {
        "source_artifact_role_missing",
        "semantic_manifest_artifact_hash_mismatch",
    } or code.startswith(("semantic_manifest_bad_", "semantic_manifest_fixed_")):
        return "hash_provenance"
    if code.startswith("contract_") or code.startswith("target_"):
        return "target_contract"
    if code.startswith(("observation_", "semantic_", "oracle_", "revision_result_")):
        return "semantic_cpu_gpu_equality"
    if code.startswith("failure_") or code.startswith("bad_") or code.startswith("fixed_"):
        return "bad_fixed_oracle_delta"
    if code.startswith("seed_") or code.startswith("coverage_") or code.startswith("oracle_violation_"):
        return "seed_corpora"
    if code.startswith("campaign_"):
        return "campaign"
    if (
        code.startswith("source_artifacts_")
        or code.startswith("graph_artifacts_")
        or code.startswith("report_artifacts_")
    ):
        return "hash_provenance"
    return "uncategorized"


def _build_checks(issues: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    issues_by_check = {name: [] for name in _CHECK_NAMES}
    for issue in issues:
        code = issue.get("code", "unknown")
        check_name = _check_name_for_issue(code)
        issues_by_check.setdefault(check_name, []).append(code)
    return [
        {
            "name": name,
            "status": "fail" if issues_by_check[name] else "pass",
            "issue_codes": sorted(set(issues_by_check[name])),
        }
        for name in issues_by_check
    ]


def _target_by_id(contract: Mapping[str, Any], target_id: Any) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    targets = contract.get("targets")
    if not isinstance(targets, list):
        return None, issues
    matches = [
        target
        for target in targets
        if isinstance(target, dict) and target.get("target_id") == target_id
    ]
    if len(matches) != 1:
        _issue(
            issues,
            "target_contract_match_count",
            f"expected exactly one contract target for {target_id!r}, found {len(matches)}",
        )
        return None, issues
    return matches[0], issues


def _is_safe_relative_path(value: Any, *, allow_dot: bool = False) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    path = Path(value)
    if path.is_absolute():
        return False
    if value == ".":
        return allow_dot
    return ".." not in path.parts


def validate_adjudication_run_spec(run_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a static OpenTitan evidence adjudication run specification."""

    issues: list[dict[str, str]] = []
    _reject_unknown_keys(
        run_spec,
        {"schema_version", "surface", "target_contract", "evidence_root", "evidence"},
        issues,
        "run_spec_unknown_field",
        "run spec",
    )
    if run_spec.get("surface") != OPENTITAN_ADJUDICATION_RUN_SPEC_SURFACE:
        _issue(issues, "run_spec_surface_mismatch", "run spec surface is not recognized")
    if not _has_schema_version(run_spec.get("schema_version")):
        _issue(issues, "run_spec_schema_version_mismatch", "run spec schema_version must be integer 1")
    if not _is_safe_relative_path(run_spec.get("target_contract")):
        _issue(issues, "run_spec_target_contract_invalid", "target_contract must be a nonempty relative path without parent traversal")
    if not _is_safe_relative_path(run_spec.get("evidence_root"), allow_dot=True):
        _issue(issues, "run_spec_evidence_root_invalid", "evidence_root must be a relative path without parent traversal")
    evidence = run_spec.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        _issue(issues, "run_spec_evidence_missing", "evidence must be a nonempty list")
    else:
        seen: set[str] = set()
        for index, path in enumerate(evidence):
            if not _is_safe_relative_path(path):
                _issue(issues, "run_spec_evidence_path_invalid", f"evidence path {index} must be a relative path without parent traversal")
                continue
            if path in seen:
                _issue(issues, "run_spec_evidence_path_duplicate", f"evidence path {path!r} is duplicated")
            seen.add(path)
    return {
        "schema_version": OPENTITAN_EVIDENCE_SCHEMA_VERSION,
        "surface": OPENTITAN_ADJUDICATION_RUN_SPEC_REPORT_SURFACE,
        "status": "fail" if issues else "pass",
        "checks": _build_checks(issues),
        "issue_count": len(issues),
        "issues": issues,
    }


def validate_target_contract_document(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Validate every target in an OpenTitan regression target contract."""

    issues: list[dict[str, str]] = []
    _reject_unknown_keys(
        contract,
        {"schema_version", "surface", "targets"},
        issues,
        "contract_unknown_field",
        "target contract",
    )
    if contract.get("surface") != OPENTITAN_TARGET_CONTRACT_SURFACE:
        _issue(issues, "contract_surface_mismatch", "target contract surface is not recognized")
    if not _has_schema_version(contract.get("schema_version")):
        _issue(issues, "contract_schema_version_mismatch", "target contract schema_version must be integer 1")
    targets = contract.get("targets")
    target_reports: list[dict[str, Any]] = []
    if not isinstance(targets, list) or not targets:
        _issue(issues, "contract_targets_missing", "target contract must contain a nonempty targets list")
    else:
        seen_ids: set[str] = set()
        for index, target in enumerate(targets):
            before = len(issues)
            target_id = None
            if not isinstance(target, dict):
                _issue(issues, "target_entry_invalid", f"target entry {index} must be an object")
            else:
                target_id = target.get("target_id")
                if isinstance(target_id, str) and target_id:
                    if target_id in seen_ids:
                        _issue(issues, "target_id_duplicate", f"target_id {target_id!r} is duplicated")
                    seen_ids.add(target_id)
                _validate_target_contract(target, issues)
            target_reports.append(
                {
                    "index": index,
                    "target_id": target_id
                    if isinstance(target_id, str) and target_id
                    else None,
                    "status": "fail" if len(issues) != before else "pass",
                    "issue_count": len(issues) - before,
                }
            )
    return {
        "schema_version": OPENTITAN_EVIDENCE_SCHEMA_VERSION,
        "surface": OPENTITAN_TARGET_CONTRACT_REPORT_SURFACE,
        "status": "fail" if issues else "pass",
        "checks": _build_checks(issues),
        "target_count": len(target_reports),
        "issue_count": len(issues),
        "issues": issues,
        "targets": target_reports,
    }


def _validate_target_contract(target: Mapping[str, Any], issues: list[dict[str, str]]) -> None:
    _reject_unknown_keys(
        target,
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
        },
        issues,
        "target_unknown_field",
        "target contract entry",
    )
    for name in ("target_id", "ip", "issue", "checkpoint_identity", "oracle_identity", "oracle_field"):
        if not _is_string(target.get(name)) or not target.get(name):
            _issue(issues, "target_contract_string", f"target contract field {name} must be a nonempty string")
    campaign_domain = target.get("campaign_action_domain")
    reproduction_domain = target.get("reproduction_action_domain")
    semantic_observables = target.get("semantic_observables")
    if not _nonempty_unique_strings(campaign_domain):
        _issue(issues, "target_campaign_domain_invalid", "campaign_action_domain must be nonempty unique strings")
    if not _nonempty_unique_strings(reproduction_domain):
        _issue(issues, "target_reproduction_domain_invalid", "reproduction_action_domain must be nonempty unique strings")
    if _nonempty_unique_strings(campaign_domain) and _nonempty_unique_strings(reproduction_domain):
        missing = sorted(set(reproduction_domain) - set(campaign_domain))
        if missing:
            _issue(issues, "target_reproduction_domain_not_subset", f"reproduction actions not in campaign domain: {missing}")
    if not _nonempty_unique_strings(semantic_observables):
        _issue(issues, "target_semantic_observables_invalid", "semantic_observables must be nonempty unique strings")
    elif target.get("oracle_field") in semantic_observables:
        _issue(
            issues,
            "target_oracle_field_not_distinct",
            "oracle_field must be distinct from semantic_observables",
        )
    revisions = target.get("revisions")
    manifests = target.get("semantic_manifest_sha256")
    if not isinstance(revisions, dict) or set(revisions) != set(_REVISION_LABELS):
        _issue(issues, "target_revisions_invalid", "revisions must contain exactly bad and fixed")
    else:
        for label in _REVISION_LABELS:
            if not _hex(revisions.get(label), 40):
                _issue(issues, "target_revision_sha_invalid", f"{label} revision must be lowercase 40-hex")
        if all(_hex(revisions.get(label), 40) for label in _REVISION_LABELS) and revisions["bad"] == revisions["fixed"]:
            _issue(issues, "target_revisions_not_distinct", "bad and fixed revisions must be distinct")
    if not isinstance(manifests, dict) or set(manifests) != set(_REVISION_LABELS):
        _issue(issues, "target_semantic_manifest_invalid", "semantic_manifest_sha256 must contain exactly bad and fixed")
    else:
        for label in _REVISION_LABELS:
            if not _hex(manifests.get(label), 64):
                _issue(issues, "target_semantic_manifest_sha_invalid", f"{label} semantic manifest must be lowercase 64-hex")


def _validate_identity(evidence: Mapping[str, Any], target: Mapping[str, Any], issues: list[dict[str, str]]) -> None:
    if evidence.get("surface") != OPENTITAN_EVIDENCE_BUNDLE_SURFACE:
        _issue(issues, "evidence_surface_mismatch", "evidence surface is not recognized")
    if not _has_schema_version(evidence.get("schema_version")):
        _issue(issues, "evidence_schema_version_mismatch", "evidence schema_version must be integer 1")
    evidence_target = evidence.get("target")
    _reject_unknown_keys(
        evidence_target,
        {"ip", "issue"},
        issues,
        "evidence_target_unknown_field",
        "evidence target",
    )
    for name in ("ip", "issue"):
        observed = evidence_target.get(name) if isinstance(evidence_target, dict) else None
        if observed != target.get(name):
            _issue(issues, "target_identity_mismatch", f"evidence target.{name} does not match contract")
    for name in ("checkpoint_identity", "oracle_identity"):
        if evidence.get(name) != target.get(name):
            _issue(issues, "identity_mismatch", f"evidence {name} does not match target contract")
    if evidence.get("revisions") != target.get("revisions"):
        _issue(issues, "revision_mismatch", "evidence revisions do not match target contract")
    if evidence.get("semantic_manifest_sha256") != target.get("semantic_manifest_sha256"):
        _issue(issues, "semantic_manifest_mismatch", "semantic manifest hashes do not match target contract")
    domain = evidence.get("action_domain")
    if domain != target.get("campaign_action_domain"):
        _issue(issues, "action_domain_mismatch", "evidence action_domain must equal contract campaign_action_domain in order")
    elif evidence.get("action_domain_sha256") != _sha256_bytes(_canonical_bytes(domain)):
        _issue(issues, "action_domain_hash_mismatch", "action_domain_sha256 does not match canonical action_domain")
    runner = evidence.get("runner")
    _reject_unknown_keys(
        runner,
        {"identity", "status", "completed_at"},
        issues,
        "runner_unknown_field",
        "runner",
    )
    if not isinstance(runner, dict) or runner.get("status") != "pass":
        _issue(issues, "runner_status_invalid", "runner.status must be pass")
    elif not _is_string(runner.get("identity")) or not runner.get("identity"):
        _issue(issues, "runner_identity_invalid", "runner.identity must be a nonempty string")
    elif not _is_string(runner.get("completed_at")) or not runner.get("completed_at"):
        _issue(issues, "runner_completed_at_invalid", "runner.completed_at must be a nonempty string")


def _coverage_delta_nonzero(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and all(character in _HEX64 for character in value) and int(value, 16) != 0


def _collect_revision_results(
    evidence: Mapping[str, Any],
    target: Mapping[str, Any],
    issues: list[dict[str, str]],
) -> tuple[
    dict[str, set[tuple[str, ...]]],
    dict[str, dict[tuple[str, ...], Mapping[str, Any]]],
]:
    domain = set(_string_list(target.get("campaign_action_domain")))
    semantic_keys = _string_list(target.get("semantic_observables"))
    oracle_field = target.get("oracle_field")
    results = evidence.get("revision_results")
    executed_by_revision: dict[str, set[tuple[str, ...]]] = {
        "bad": set(),
        "fixed": set(),
    }
    observations_by_revision: dict[
        str, dict[tuple[str, ...], Mapping[str, Any]]
    ] = {"bad": {}, "fixed": {}}
    if not isinstance(results, dict) or set(results) != set(_REVISION_LABELS):
        _issue(issues, "revision_results_invalid", "revision_results must contain exactly bad and fixed")
        return executed_by_revision, observations_by_revision

    for label in _REVISION_LABELS:
        result = results.get(label)
        if not isinstance(result, dict):
            _issue(issues, "revision_result_invalid", f"{label} revision result must be an object")
            continue
        _reject_unknown_keys(
            result,
            {"executed_action_sequences", "oracle_failure_action_sequences", "observations"},
            issues,
            "revision_result_unknown_field",
            f"{label} revision result",
        )
        executed = result.get("executed_action_sequences")
        failures = result.get("oracle_failure_action_sequences")
        observations = result.get("observations")
        if not isinstance(executed, list) or not isinstance(failures, list) or not isinstance(observations, list):
            _issue(issues, "revision_result_lists_invalid", f"{label} revision must provide executed, failure, and observation lists")
            continue
        for sequence in executed:
            if not _valid_sequence(sequence, domain):
                _issue(issues, "executed_sequence_invalid", f"{label} executed sequence is not a nonempty domain sequence")
                continue
            key = _sequence_key(sequence)
            if key in executed_by_revision[label]:
                _issue(issues, "executed_sequence_duplicate", f"{label} executed sequence is duplicated")
            executed_by_revision[label].add(key)
        failure_keys: set[tuple[str, ...]] = set()
        for sequence in failures:
            if not _valid_sequence(sequence, domain):
                _issue(issues, "failure_sequence_invalid", f"{label} failure sequence is not a nonempty domain sequence")
                continue
            key = _sequence_key(sequence)
            failure_keys.add(key)
            if key not in executed_by_revision[label]:
                _issue(issues, "failure_sequence_not_executed", f"{label} failure sequence was not executed")
        if label == "bad" and not failure_keys:
            _issue(issues, "bad_revision_no_failure", "bad revision must have at least one oracle failure")
        if label == "fixed" and failure_keys:
            _issue(issues, "fixed_revision_failure", "fixed revision must not have oracle failures")

        for observation in observations:
            if not isinstance(observation, dict):
                _issue(issues, "observation_invalid", f"{label} observation must be an object")
                continue
            _reject_unknown_keys(
                observation,
                {"sequence", "status", "cpu", "gpu"},
                issues,
                "observation_unknown_field",
                f"{label} observation",
            )
            sequence = observation.get("sequence")
            if not _valid_sequence(sequence, domain):
                _issue(issues, "observation_sequence_invalid", f"{label} observation sequence is invalid")
                continue
            key = _sequence_key(sequence)
            if key not in executed_by_revision[label]:
                _issue(issues, "observation_sequence_not_executed", f"{label} observation sequence was not executed")
            if key in observations_by_revision[label]:
                _issue(issues, "observation_sequence_duplicate", f"{label} observation sequence is duplicated")
            observations_by_revision[label][key] = observation
            if observation.get("status") != "pass":
                _issue(issues, "observation_status_invalid", f"{label} observation status must be pass")
            cpu = observation.get("cpu")
            gpu = observation.get("gpu")
            if not isinstance(cpu, dict) or not isinstance(gpu, dict):
                _issue(issues, "observation_projection_invalid", f"{label} cpu and gpu projections must be objects")
                continue
            for key_name in semantic_keys:
                if key_name not in cpu or key_name not in gpu:
                    _issue(issues, "semantic_observable_missing", f"{label} missing semantic observable {key_name}")
                elif not _json_values_equal(cpu[key_name], gpu[key_name]):
                    _issue(issues, "semantic_cpu_gpu_mismatch", f"{label} semantic observable {key_name} differs")
            if not isinstance(oracle_field, str) or oracle_field not in cpu or oracle_field not in gpu:
                _issue(issues, "oracle_field_missing", f"{label} oracle field is missing")
                continue
            if not _json_values_equal(cpu[oracle_field], gpu[oracle_field]):
                _issue(issues, "oracle_cpu_gpu_mismatch", f"{label} oracle field differs between CPU and GPU")
            if not _is_int(cpu[oracle_field]) or cpu[oracle_field] not in (0, 1):
                _issue(issues, "oracle_value_invalid", f"{label} oracle value must be 0 or 1")
            if label == "bad" and key in failure_keys and cpu[oracle_field] != 1:
                _issue(issues, "bad_failure_oracle_not_asserted", "bad failure sequence does not assert oracle")
            if label == "bad" and cpu[oracle_field] == 1 and key not in failure_keys:
                _issue(
                    issues,
                    "bad_oracle_assertion_not_declared_failure",
                    "bad revision oracle assertion is missing from oracle_failure_action_sequences",
                )
            if label == "fixed" and cpu[oracle_field] != 0:
                _issue(issues, "fixed_observation_oracle_asserted", "fixed revision observation asserts oracle")
        missing_failure_observations = sorted(failure_keys - set(observations_by_revision[label]))
        if missing_failure_observations:
            _issue(
                issues,
                "failure_sequence_not_observed",
                f"{label} failure sequences lack semantic observations: {missing_failure_observations}",
            )
        missing_executed_observations = sorted(
            executed_by_revision[label] - set(observations_by_revision[label])
        )
        if missing_executed_observations:
            _issue(
                issues,
                "executed_sequence_not_observed",
                f"{label} executed sequences lack semantic observations: {missing_executed_observations}",
            )
    return executed_by_revision, observations_by_revision


def _validate_seed_corpora(
    evidence: Mapping[str, Any],
    target: Mapping[str, Any],
    executed_by_revision: Mapping[str, set[tuple[str, ...]]],
    observations_by_revision: Mapping[
        str, Mapping[tuple[str, ...], Mapping[str, Any]]
    ],
    issues: list[dict[str, str]],
) -> None:
    corpora = evidence.get("seed_corpora")
    if not isinstance(corpora, dict):
        _issue(issues, "seed_corpora_missing", "seed_corpora must be an object")
        return
    _reject_unknown_keys(
        corpora,
        {"coverage_gain", "oracle_violation"},
        issues,
        "seed_corpora_unknown_field",
        "seed_corpora",
    )
    coverage = corpora.get("coverage_gain")
    violations = corpora.get("oracle_violation")
    if not isinstance(coverage, list) or not coverage:
        _issue(issues, "coverage_gain_corpus_missing", "coverage_gain corpus must be a nonempty list")
        coverage = []
    if not isinstance(violations, list) or not violations:
        _issue(issues, "oracle_violation_corpus_missing", "oracle_violation corpus must be a nonempty list")
        violations = []
    domain = set(_string_list(target.get("campaign_action_domain")))
    for entry in coverage:
        if not isinstance(entry, dict):
            _issue(issues, "coverage_gain_entry_invalid", "coverage_gain entry must be an object")
            continue
        _reject_unknown_keys(
            entry,
            {"schema_version", "corpus_kind", "source_revision", "sequence", "coverage_delta_bits"},
            issues,
            "coverage_gain_unknown_field",
            "coverage_gain entry",
        )
        if not _has_schema_version(entry.get("schema_version")):
            _issue(issues, "coverage_gain_schema_version_invalid", "coverage_gain schema_version must be integer 1")
        if entry.get("corpus_kind") != "coverage_gain":
            _issue(issues, "coverage_gain_kind_invalid", "coverage_gain entry has wrong corpus_kind")
        revision = entry.get("source_revision")
        sequence = entry.get("sequence")
        if revision not in _REVISION_LABELS or not _valid_sequence(sequence, domain):
            _issue(issues, "coverage_gain_sequence_invalid", "coverage_gain entry must identify an observed revision and domain sequence")
            continue
        key = _sequence_key(sequence)
        if key not in executed_by_revision[revision] or key not in observations_by_revision[revision]:
            _issue(issues, "coverage_gain_sequence_not_observed", "coverage_gain sequence was not observed")
        if not _coverage_delta_nonzero(entry.get("coverage_delta_bits")):
            _issue(issues, "coverage_gain_delta_empty", "coverage_gain must have nonzero lowercase hex coverage_delta_bits")
    revision_results = evidence.get("revision_results")
    if not isinstance(revision_results, Mapping):
        revision_results = {}
    fixed_result = revision_results.get("fixed")
    if not isinstance(fixed_result, Mapping):
        fixed_result = {}
    bad_result = revision_results.get("bad")
    if not isinstance(bad_result, Mapping):
        bad_result = {}
    fixed_failures = fixed_result.get("oracle_failure_action_sequences", [])
    if not isinstance(fixed_failures, list):
        fixed_failures = []
    fixed_failure_keys = {
        _sequence_key(sequence)
        for sequence in fixed_failures
        if _valid_sequence(sequence, domain)
    }
    bad_failures = bad_result.get("oracle_failure_action_sequences", [])
    if not isinstance(bad_failures, list):
        bad_failures = []
    bad_failure_keys = {
        _sequence_key(sequence)
        for sequence in bad_failures
        if _valid_sequence(sequence, domain)
    }
    for entry in violations:
        if not isinstance(entry, dict):
            _issue(issues, "oracle_violation_entry_invalid", "oracle_violation entry must be an object")
            continue
        _reject_unknown_keys(
            entry,
            {"schema_version", "corpus_kind", "source_revision", "sequence", "minimal"},
            issues,
            "oracle_violation_unknown_field",
            "oracle_violation entry",
        )
        if not _has_schema_version(entry.get("schema_version")):
            _issue(issues, "oracle_violation_schema_version_invalid", "oracle_violation schema_version must be integer 1")
        if entry.get("corpus_kind") != "oracle_violation":
            _issue(issues, "oracle_violation_kind_invalid", "oracle_violation entry has wrong corpus_kind")
        if entry.get("source_revision") != "bad":
            _issue(issues, "oracle_violation_revision_invalid", "oracle_violation entries must come from the bad revision")
        if entry.get("minimal") is not True:
            _issue(issues, "oracle_violation_not_minimal", "oracle_violation entries must be marked minimal")
        sequence = entry.get("sequence")
        if not _valid_sequence(sequence, domain):
            _issue(issues, "oracle_violation_sequence_invalid", "oracle_violation sequence is invalid")
            continue
        reproduction_domain = set(_string_list(target.get("reproduction_action_domain")))
        if not all(action in reproduction_domain for action in sequence):
            _issue(
                issues,
                "oracle_violation_outside_reproduction_domain",
                "oracle_violation sequence uses actions outside reproduction_action_domain",
            )
        key = _sequence_key(sequence)
        if key not in bad_failure_keys:
            _issue(issues, "oracle_violation_not_bad_failure", "oracle_violation sequence is not a bad revision failure")
        if len(sequence) > 1:
            reduced_keys = {
                _sequence_key(sequence[:index] + sequence[index + 1 :])
                for index in range(len(sequence))
            }
            for reduced_key in sorted(reduced_keys):
                reduced_observation = observations_by_revision["bad"].get(reduced_key)
                if (
                    reduced_key not in executed_by_revision["bad"]
                    or reduced_observation is None
                ):
                    _issue(
                        issues,
                        "oracle_violation_minimality_unproven",
                        "every nonempty single-action deletion must be executed and observed on the bad revision",
                    )
                    continue
                reduced_cpu = reduced_observation.get("cpu")
                reduced_oracle = (
                    reduced_cpu.get(target.get("oracle_field"))
                    if isinstance(reduced_cpu, Mapping)
                    else None
                )
                if reduced_key in bad_failure_keys or reduced_oracle != 0:
                    _issue(
                        issues,
                        "oracle_violation_not_minimal",
                        "oracle_violation sequence is not 1-minimal under single-action deletion",
                    )
        if key not in executed_by_revision["fixed"] or key not in observations_by_revision["fixed"]:
            _issue(
                issues,
                "oracle_violation_fixed_sequence_not_observed",
                "oracle_violation sequence must be executed and observed on the fixed revision",
            )
        if key in fixed_failure_keys:
            _issue(issues, "oracle_violation_fixed_failure", "oracle_violation sequence still fails on fixed revision")


def _resolve_artifact(root: Path, raw_path: Any) -> Path | None:
    if not _is_safe_relative_path(raw_path):
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _validate_artifacts(
    evidence: Mapping[str, Any],
    evidence_root: Path,
    field: str,
    issues: list[dict[str, str]],
    *,
    require_payload: bool,
    source_sha256_by_role: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    artifacts = evidence.get(field)
    verified: list[dict[str, str]] = []
    if not isinstance(artifacts, list) or not artifacts:
        _issue(issues, f"{field}_missing", f"{field} must be a nonempty list")
        return verified
    roles: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            _issue(issues, f"{field}_entry_invalid", f"{field} entries must be objects")
            continue
        allowed_fields = {"role", "path", "sha256"}
        if require_payload:
            allowed_fields.update({"payload", "payload_sha256"})
        _reject_unknown_keys(
            artifact,
            allowed_fields,
            issues,
            f"{field}_unknown_field",
            f"{field} artifact",
        )
        role = artifact.get("role")
        expected_hash = artifact.get("sha256")
        if not isinstance(role, str) or not role:
            _issue(issues, f"{field}_role_invalid", f"{field} artifact role must be a nonempty string")
            continue
        if role in roles:
            _issue(issues, f"{field}_role_duplicate", f"{field} artifact role {role!r} is duplicated")
            continue
        roles.add(role)
        if not _hex(expected_hash, 64):
            _issue(issues, f"{field}_sha_invalid", f"{field} artifact {role} sha256 must be lowercase 64-hex")
            continue
        payload_hash = artifact.get("payload_sha256")
        if require_payload:
            payload = artifact.get("payload")
            if not isinstance(payload, dict):
                _issue(issues, f"{field}_payload_missing", f"{field} artifact {role} must include a payload object")
            elif not _hex(payload_hash, 64):
                _issue(issues, f"{field}_payload_sha_invalid", f"{field} artifact {role} payload_sha256 must be lowercase 64-hex")
            elif payload_hash != _sha256_bytes(_canonical_bytes(payload)):
                _issue(issues, f"{field}_payload_hash_mismatch", f"{field} artifact {role} payload_sha256 does not match payload")
            else:
                _validate_payload_provenance(
                    field=field,
                    role=role,
                    payload=payload,
                    source_sha256_by_role=source_sha256_by_role,
                    issues=issues,
                )
        path = _resolve_artifact(evidence_root, artifact.get("path"))
        if path is None:
            _issue(issues, f"{field}_path_invalid", f"{field} artifact {role} path must be relative and stay under evidence root")
            continue
        if not path.is_file():
            _issue(issues, f"{field}_file_missing", f"{field} artifact {role} file does not exist")
            continue
        actual_hash = _sha256_file(path)
        if actual_hash != expected_hash:
            _issue(issues, f"{field}_hash_mismatch", f"{field} artifact {role} sha256 does not match bytes")
        row = {"role": role, "path": artifact["path"], "sha256": actual_hash}
        if require_payload and _hex(payload_hash, 64):
            row["payload_sha256"] = payload_hash
        payload = artifact.get("payload")
        if require_payload and isinstance(payload, Mapping) and source_sha256_by_role is not None:
            source_roles = _payload_source_roles(payload)
            source_hashes = _payload_source_hashes(payload, source_sha256_by_role)
            if source_roles and set(source_roles) == set(source_hashes):
                row["source_artifact_roles"] = source_roles
                row["source_artifact_sha256_by_role"] = source_hashes
        verified.append(row)
    return verified


def _payload_source_hashes(
    payload: Mapping[str, Any],
    source_sha256_by_role: Mapping[str, str],
) -> dict[str, str]:
    roles = _payload_source_roles(payload)
    return {
        role: source_sha256_by_role[role]
        for role in roles
        if role in source_sha256_by_role
    }


def _payload_source_roles(payload: Mapping[str, Any]) -> list[str]:
    roles: list[str] = []
    singular = payload.get("source_artifact_role")
    plural = payload.get("source_artifact_roles")
    if isinstance(singular, str) and singular:
        roles.append(singular)
    if isinstance(plural, list):
        roles.extend(item for item in plural if isinstance(item, str) and item)
    if not roles or len(set(roles)) != len(roles):
        return []
    return roles


def _validate_payload_provenance(
    *,
    field: str,
    role: str,
    payload: Mapping[str, Any],
    source_sha256_by_role: Mapping[str, str] | None,
    issues: list[dict[str, str]],
) -> None:
    if source_sha256_by_role is None:
        return
    source_roles = set(source_sha256_by_role)
    declared_roles: list[str] = []
    singular_present = "source_artifact_role" in payload
    singular_sha_present = "source_artifact_sha256" in payload
    plural_present = "source_artifact_roles" in payload
    plural_sha_present = "source_artifact_sha256_by_role" in payload
    if singular_present != singular_sha_present:
        _issue(
            issues,
            f"{field}_payload_source_pair_incomplete",
            f"{field} artifact {role} must provide source_artifact_role and source_artifact_sha256 together",
        )
    if plural_present != plural_sha_present:
        _issue(
            issues,
            f"{field}_payload_source_pair_incomplete",
            f"{field} artifact {role} must provide source_artifact_roles and source_artifact_sha256_by_role together",
        )
    singular = payload.get("source_artifact_role")
    singular_sha = payload.get("source_artifact_sha256")
    plural = payload.get("source_artifact_roles")
    plural_sha = payload.get("source_artifact_sha256_by_role")
    plural_roles: list[str] = []
    if isinstance(singular, str) and singular:
        declared_roles.append(singular)
    elif singular_present:
        _issue(
            issues,
            f"{field}_payload_source_role_invalid",
            f"{field} artifact {role} source_artifact_role must be a nonempty string",
        )
    if singular_sha_present and not _hex(singular_sha, 64):
        _issue(
            issues,
            f"{field}_payload_source_sha_invalid",
            f"{field} artifact {role} source_artifact_sha256 must be lowercase 64-hex",
        )
    if _nonempty_unique_strings(plural):
        plural_roles = list(plural)
        declared_roles.extend(plural_roles)
    elif plural_present:
        _issue(
            issues,
            f"{field}_payload_source_roles_invalid",
            f"{field} artifact {role} source_artifact_roles must be nonempty strings",
        )
    if plural_sha_present and (
        not isinstance(plural_sha, dict)
        or not plural_sha
        or not all(
            isinstance(source_role, str)
            and _hex(source_hash, 64)
            for source_role, source_hash in plural_sha.items()
        )
    ):
        _issue(
            issues,
            f"{field}_payload_source_sha_map_invalid",
            f"{field} artifact {role} source_artifact_sha256_by_role must be a nonempty sha256 map",
        )
    if not declared_roles:
        _issue(
            issues,
            f"{field}_payload_provenance_missing",
            f"{field} artifact {role} payload must declare source_artifact_role or source_artifact_roles",
        )
        return
    if len(set(declared_roles)) != len(declared_roles):
        _issue(
            issues,
            f"{field}_payload_source_roles_duplicate",
            f"{field} artifact {role} source artifact roles must be unique",
        )
    unknown = sorted(set(declared_roles) - source_roles)
    if unknown:
        _issue(
            issues,
            f"{field}_payload_source_role_unknown",
            f"{field} artifact {role} references unknown source artifact roles: {unknown}",
        )
        return
    if singular in declared_roles:
        declared_sha = singular_sha
        if not _hex(declared_sha, 64):
            _issue(
                issues,
                f"{field}_payload_source_sha_invalid",
                f"{field} artifact {role} source_artifact_sha256 must be lowercase 64-hex",
            )
        elif declared_sha != source_sha256_by_role[singular]:
            _issue(
                issues,
                f"{field}_payload_source_sha_mismatch",
                f"{field} artifact {role} source_artifact_sha256 does not match its source artifact",
            )
    if plural_roles:
        declared_sha_by_role = plural_sha
        if not isinstance(declared_sha_by_role, dict):
            _issue(
                issues,
                f"{field}_payload_source_sha_map_missing",
                f"{field} artifact {role} source_artifact_sha256_by_role must map each source role to its sha256",
            )
        else:
            extra = sorted(set(declared_sha_by_role) - set(plural_roles))
            if extra:
                _issue(
                    issues,
                    f"{field}_payload_source_sha_map_extra",
                    f"{field} artifact {role} source_artifact_sha256_by_role has undeclared roles: {extra}",
                )
            for source_role in plural_roles:
                declared_sha = declared_sha_by_role.get(source_role)
                if not _hex(declared_sha, 64):
                    _issue(
                        issues,
                        f"{field}_payload_source_sha_invalid",
                        f"{field} artifact {role} source sha for {source_role} must be lowercase 64-hex",
                    )
                elif declared_sha != source_sha256_by_role[source_role]:
                    _issue(
                        issues,
                        f"{field}_payload_source_sha_mismatch",
                        f"{field} artifact {role} source sha for {source_role} does not match source_artifacts",
                    )


def _validate_source_artifact_roles(
    source_artifacts: Sequence[Mapping[str, str]],
    target: Mapping[str, Any],
    issues: list[dict[str, str]],
) -> None:
    by_role = {
        artifact["role"]: artifact
        for artifact in source_artifacts
        if isinstance(artifact.get("role"), str)
    }
    required_roles = {
        "semantic_manifest_bad",
        "semantic_manifest_fixed",
        "equivalence_report",
        "campaign_report",
    }
    missing = sorted(required_roles - set(by_role))
    if missing:
        _issue(issues, "source_artifact_role_missing", f"missing required source artifact roles: {missing}")
    manifests = target.get("semantic_manifest_sha256")
    if not isinstance(manifests, dict):
        return
    for label in _REVISION_LABELS:
        role = f"semantic_manifest_{label}"
        artifact = by_role.get(role)
        if artifact is None:
            continue
        if artifact.get("sha256") != manifests.get(label):
            _issue(
                issues,
                "semantic_manifest_artifact_hash_mismatch",
                f"{role} bytes do not match target semantic_manifest_sha256",
            )


def _validate_semantic_manifest(
    manifest: Mapping[str, Any],
    target: Mapping[str, Any],
    label: str,
    issues: list[dict[str, str]],
) -> None:
    prefix = f"semantic_manifest_{label}"
    _reject_unknown_keys(
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
        issues,
        f"{prefix}_unknown_field",
        prefix,
    )
    if not _has_schema_version(manifest.get("schema_version")):
        _issue(issues, f"{prefix}_schema_version_invalid", "semantic manifest schema_version must be integer 1")
    if manifest.get("surface") != OPENTITAN_SEMANTIC_MANIFEST_SURFACE:
        _issue(issues, f"{prefix}_surface_invalid", "semantic manifest surface is not recognized")
    expected_scalars = {
        "target_id": target.get("target_id"),
        "revision_label": label,
        "revision_sha": target.get("revisions", {}).get(label)
        if isinstance(target.get("revisions"), Mapping)
        else None,
        "checkpoint_identity": target.get("checkpoint_identity"),
        "oracle_identity": target.get("oracle_identity"),
    }
    for name, expected in expected_scalars.items():
        if manifest.get(name) != expected:
            _issue(
                issues,
                f"{prefix}_{name}_mismatch",
                f"semantic manifest {name} does not match the target contract",
            )
    observables = manifest.get("observables")
    if not isinstance(observables, list) or not observables:
        _issue(issues, f"{prefix}_observables_invalid", "semantic manifest observables must be a nonempty list")
        return
    names: list[str] = []
    semantic_ids: list[str] = []
    for index, observable in enumerate(observables):
        if not isinstance(observable, Mapping):
            _issue(issues, f"{prefix}_observable_invalid", f"observable {index} must be an object")
            continue
        _reject_unknown_keys(
            observable,
            {"name", "semantic_id", "width_bits"},
            issues,
            f"{prefix}_observable_unknown_field",
            f"{prefix} observable {index}",
        )
        name = observable.get("name")
        semantic_id = observable.get("semantic_id")
        width_bits = observable.get("width_bits")
        if not isinstance(name, str) or not name:
            _issue(issues, f"{prefix}_observable_name_invalid", f"observable {index} name must be nonempty")
        else:
            names.append(name)
        if not isinstance(semantic_id, str) or not semantic_id:
            _issue(issues, f"{prefix}_semantic_id_invalid", f"observable {index} semantic_id must be nonempty")
        else:
            semantic_ids.append(semantic_id)
        if not _is_int(width_bits) or width_bits <= 0:
            _issue(issues, f"{prefix}_width_invalid", f"observable {index} width_bits must be a positive integer")
    expected_names = _string_list(target.get("semantic_observables"))
    oracle_field = target.get("oracle_field")
    if isinstance(oracle_field, str) and oracle_field:
        expected_names.append(oracle_field)
    if names != expected_names:
        _issue(
            issues,
            f"{prefix}_observable_names_mismatch",
            "semantic manifest observable names/order must equal semantic_observables followed by oracle_field",
        )
    if len(set(names)) != len(names):
        _issue(issues, f"{prefix}_observable_names_duplicate", "semantic manifest observable names must be unique")
    if len(set(semantic_ids)) != len(semantic_ids):
        _issue(issues, f"{prefix}_semantic_ids_duplicate", "semantic manifest semantic_id values must be unique")


def _validate_semantic_manifest_artifacts(
    source_artifacts: Sequence[Mapping[str, str]],
    evidence_root: Path,
    target: Mapping[str, Any],
    issues: list[dict[str, str]],
) -> None:
    by_role = {
        artifact.get("role"): artifact
        for artifact in source_artifacts
        if isinstance(artifact.get("role"), str)
    }
    for label in _REVISION_LABELS:
        artifact = by_role.get(f"semantic_manifest_{label}")
        if not isinstance(artifact, Mapping):
            continue
        path = _resolve_artifact(evidence_root, artifact.get("path"))
        if path is None or not path.is_file():
            continue
        try:
            manifest = read_strict_json_object(path)
        except (OSError, OpenTitanEvidenceError) as error:
            _issue(
                issues,
                f"semantic_manifest_{label}_parse_error",
                str(error),
            )
            continue
        _validate_semantic_manifest(manifest, target, label, issues)


def _validate_campaign(evidence: Mapping[str, Any], target: Mapping[str, Any], issues: list[dict[str, str]]) -> None:
    campaign = evidence.get("campaign")
    if not isinstance(campaign, dict):
        _issue(issues, "campaign_missing", "campaign must be an object")
        return
    _reject_unknown_keys(
        campaign,
        {"policies"},
        issues,
        "campaign_unknown_field",
        "campaign",
    )
    policies = campaign.get("policies")
    domain = _string_list(target.get("campaign_action_domain"))
    if not isinstance(policies, list) or not policies:
        _issue(issues, "campaign_policies_missing", "campaign.policies must be a nonempty list")
        return
    seen: set[str] = set()
    for policy in policies:
        if not isinstance(policy, dict):
            _issue(issues, "campaign_policy_invalid", "campaign policy must be an object")
            continue
        _reject_unknown_keys(
            policy,
            {"name", "orders", "metrics"},
            issues,
            "campaign_policy_unknown_field",
            "campaign policy",
        )
        name = policy.get("name")
        if not isinstance(name, str) or not name:
            _issue(issues, "campaign_policy_name_invalid", "campaign policy name must be a nonempty string")
            continue
        if name in seen:
            _issue(issues, "campaign_policy_duplicate", f"campaign policy {name!r} is duplicated")
        seen.add(name)
        orders = policy.get("orders")
        if not isinstance(orders, list) or not orders:
            _issue(issues, "campaign_orders_missing", f"campaign policy {name} must include raw orders")
            continue
        domain_length = len(domain) if isinstance(domain, list) else 0
        domain_set = set(domain) if isinstance(domain, list) else set()
        for order in orders:
            if (
                not isinstance(order, list)
                or len(order) != domain_length
                or not all(isinstance(action, str) for action in order)
                or len(set(order)) != len(order)
                or set(order) != domain_set
            ):
                _issue(issues, "campaign_order_domain_mismatch", f"campaign policy {name} order must be a full action-domain permutation")
        metrics = policy.get("metrics")
        if not isinstance(metrics, dict):
            _issue(issues, "campaign_metrics_missing", f"campaign policy {name} metrics must be an object")
            continue
        _reject_unknown_keys(
            metrics,
            {"mean", "p50", "p95", "max", "long_tail_rate"},
            issues,
            "campaign_metrics_unknown_field",
            f"campaign policy {name} metrics",
        )
        for metric_name in ("mean", "p50", "p95", "max", "long_tail_rate"):
            if not _is_finite_number(metrics.get(metric_name)):
                _issue(issues, "campaign_metric_invalid", f"campaign metric {name}.{metric_name} must be finite")
        if all(_is_finite_number(metrics.get(metric_name)) for metric_name in ("mean", "p50", "p95", "max")):
            episode_limit = domain_length
            for metric_name in ("mean", "p50", "p95", "max"):
                observed = metrics[metric_name]
                if observed < 1 or observed > episode_limit:
                    _issue(
                        issues,
                        "campaign_metric_out_of_range",
                        f"campaign metric {name}.{metric_name} must be within one action and one full domain pass",
                    )
            if not (metrics["p50"] <= metrics["p95"] <= metrics["max"]):
                _issue(issues, "campaign_metric_order_invalid", f"campaign policy {name} must satisfy p50 <= p95 <= max")
            if metrics["mean"] > metrics["max"]:
                _issue(issues, "campaign_metric_order_invalid", f"campaign policy {name} mean must be <= max")
        if _is_finite_number(metrics.get("long_tail_rate")):
            if metrics["long_tail_rate"] < 0 or metrics["long_tail_rate"] > 1:
                _issue(issues, "campaign_metric_out_of_range", f"campaign policy {name} long_tail_rate must be in [0, 1]")


def _verified_identity(target: Mapping[str, Any]) -> dict[str, Any]:
    campaign_domain = list(target["campaign_action_domain"])
    reproduction_domain = list(target["reproduction_action_domain"])
    return {
        "target_id": target["target_id"],
        "ip": target["ip"],
        "issue": target["issue"],
        "revisions": dict(target["revisions"]),
        "checkpoint_identity": target["checkpoint_identity"],
        "oracle_identity": target["oracle_identity"],
        "campaign_action_domain": campaign_domain,
        "campaign_action_domain_sha256": _sha256_bytes(_canonical_bytes(campaign_domain)),
        "reproduction_action_domain": reproduction_domain,
        "reproduction_action_domain_sha256": _sha256_bytes(_canonical_bytes(reproduction_domain)),
        "semantic_observables": list(target["semantic_observables"]),
        "oracle_field": target["oracle_field"],
        "semantic_manifest_sha256": dict(target["semantic_manifest_sha256"]),
    }


def adjudicate_external_evidence(
    *,
    target_contract: Mapping[str, Any],
    evidence: Mapping[str, Any],
    evidence_root: Path,
) -> dict[str, Any]:
    """Validate one external evidence bundle against one tracked target contract."""

    issues: list[dict[str, str]] = []
    _reject_unknown_keys(
        evidence,
        {
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
        issues,
        "evidence_unknown_field",
        "evidence bundle",
    )
    target_contract_report = validate_target_contract_document(target_contract)
    report_issues = target_contract_report.get("issues")
    if isinstance(report_issues, list):
        for issue in report_issues:
            if isinstance(issue, dict):
                _issue(issues, str(issue.get("code", "unknown")), str(issue.get("detail", "")))
    target_id = evidence.get("target_id")
    if not isinstance(target_id, str) or not target_id:
        _issue(issues, "target_id_invalid", "evidence target_id must be a nonempty string")
        target = None
    else:
        target, target_issues = _target_by_id(target_contract, target_id)
        issues.extend(target_issues)
    if target is not None:
        _validate_identity(evidence, target, issues)
        executed, observations = _collect_revision_results(evidence, target, issues)
        _validate_seed_corpora(evidence, target, executed, observations, issues)
        _validate_campaign(evidence, target, issues)
    source_artifacts = _validate_artifacts(evidence, evidence_root, "source_artifacts", issues, require_payload=False)
    if target is not None:
        _validate_source_artifact_roles(source_artifacts, target, issues)
        _validate_semantic_manifest_artifacts(source_artifacts, evidence_root, target, issues)
    source_sha256_by_role = {
        artifact["role"]: artifact["sha256"]
        for artifact in source_artifacts
    }
    graph_artifacts = _validate_artifacts(
        evidence,
        evidence_root,
        "graph_artifacts",
        issues,
        require_payload=True,
        source_sha256_by_role=source_sha256_by_role,
    )
    report_artifacts = _validate_artifacts(
        evidence,
        evidence_root,
        "report_artifacts",
        issues,
        require_payload=True,
        source_sha256_by_role=source_sha256_by_role,
    )

    status = "fail" if issues else "pass"
    verified_identity = _verified_identity(target) if status == "pass" and target is not None else None
    return {
        "schema_version": OPENTITAN_EVIDENCE_SCHEMA_VERSION,
        "surface": OPENTITAN_ADJUDICATION_SURFACE,
        "target_id": target_id
        if isinstance(target_id, str) and target_id
        else None,
        "status": status,
        "verified_identity": verified_identity,
        "checks": _build_checks(issues),
        "issue_count": len(issues),
        "issues": issues,
        "target_contract": target_contract_report,
        "verified_artifacts": {
            "source_artifacts": source_artifacts,
            "graph_artifacts": graph_artifacts,
            "report_artifacts": report_artifacts,
        },
    }


def summarize_adjudications(
    rows: Sequence[Mapping[str, Any]],
    *,
    target_contract_sha256: str | None = None,
) -> dict[str, Any]:
    """Summarize multiple external-evidence adjudications."""

    results: list[dict[str, Any]] = []
    check_counts = {
        name: {"pass": 0, "fail": 0}
        for name in _CHECK_NAMES
    }
    for index, row in enumerate(rows):
        path = row.get("evidence_path")
        adjudication = row.get("adjudication")
        if not isinstance(adjudication, Mapping):
            adjudication = adjudication_input_error("adjudication row is malformed")
        status = adjudication.get("status")
        if status not in {"pass", "fail"}:
            status = "fail"
        checks = adjudication.get("checks")
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, Mapping):
                    continue
                name = check.get("name")
                check_status = check.get("status")
                if isinstance(name, str) and check_status in {"pass", "fail"}:
                    check_counts.setdefault(name, {"pass": 0, "fail": 0})
                    check_counts[name][check_status] += 1
        results.append(
            {
                "index": index,
                "evidence_path": path if isinstance(path, str) else None,
                "target_id": adjudication.get("target_id"),
                "status": status,
                "verified_identity": adjudication.get("verified_identity"),
                "issue_count": adjudication.get("issue_count", 0),
                "issue_codes": [
                    issue.get("code", "unknown")
                    for issue in adjudication.get("issues", [])
                    if isinstance(issue, Mapping)
                ],
                "issues": [
                    dict(issue)
                    for issue in adjudication.get("issues", [])
                    if isinstance(issue, Mapping)
                ],
                "input_sha256": adjudication.get("input_sha256", {}),
            }
        )
    if not results:
        rows = [
            {
                "evidence_path": None,
                "adjudication": adjudication_input_error("no evidence bundles were provided"),
            }
        ]
        return summarize_adjudications(rows, target_contract_sha256=target_contract_sha256)
    pass_count = sum(1 for result in results if result["status"] == "pass")
    fail_count = len(results) - pass_count
    return {
        "schema_version": OPENTITAN_EVIDENCE_SCHEMA_VERSION,
        "surface": OPENTITAN_ADJUDICATION_SUMMARY_SURFACE,
        "status": "pass" if fail_count == 0 else "fail",
        "input_sha256": {
            "target_contract": target_contract_sha256,
        },
        "evidence_count": len(results),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "check_counts": check_counts,
        "results": results,
    }


def validate_adjudication_document(adjudication: Mapping[str, Any]) -> dict[str, Any]:
    """Validate machine-readable adjudication output for internal consistency."""

    issues: list[dict[str, str]] = []
    _reject_unknown_keys(
        adjudication,
        {
            "schema_version",
            "surface",
            "target_id",
            "status",
            "verified_identity",
            "checks",
            "issue_count",
            "issues",
            "target_contract",
            "verified_artifacts",
            "input_sha256",
        },
        issues,
        "adjudication_output_unknown_field",
        "adjudication output",
    )
    if adjudication.get("surface") != OPENTITAN_ADJUDICATION_SURFACE:
        _issue(issues, "adjudication_output_surface_mismatch", "adjudication surface is not recognized")
    if not _has_schema_version(adjudication.get("schema_version")):
        _issue(issues, "adjudication_output_schema_version_mismatch", "adjudication schema_version must be integer 1")
    status = adjudication.get("status")
    if status not in {"pass", "fail"}:
        _issue(issues, "adjudication_output_status_invalid", "adjudication status must be pass or fail")
    target_id = adjudication.get("target_id")
    if "target_id" not in adjudication:
        _issue(issues, "adjudication_output_target_id_missing", "adjudication output must contain target_id")
    if target_id is not None and not _is_string(target_id):
        _issue(issues, "adjudication_output_target_id_invalid", "adjudication target_id must be a string or null")
    if status == "pass" and (not _is_string(target_id) or not target_id):
        _issue(issues, "adjudication_output_target_id_invalid", "a passing adjudication requires a nonempty target_id")
    if "verified_identity" not in adjudication:
        _issue(
            issues,
            "adjudication_output_verified_identity_missing",
            "adjudication output must contain verified_identity, using null for a failed adjudication",
        )
    verified_identity = _validate_verified_identity(
        adjudication.get("verified_identity"),
        target_id,
        status,
        issues,
        "adjudication_output",
    )
    source_issues = _validated_output_issues(adjudication.get("issues"), issues, "adjudication_output")
    _validate_output_issue_count(adjudication, source_issues, issues, "adjudication_output")
    if status in {"pass", "fail"}:
        expected_status = "fail" if source_issues else "pass"
        if status != expected_status:
            _issue(issues, "adjudication_output_status_mismatch", "adjudication status must match issue presence")
    _validate_output_checks(adjudication.get("checks"), source_issues, issues, "adjudication_output")
    target_contract_report = adjudication.get("target_contract")
    if target_contract_report is None:
        if status == "pass":
            _issue(
                issues,
                "adjudication_output_target_contract_missing",
                "a passing adjudication must include its target-contract validation report",
            )
    else:
        target_contract_status = _validate_target_contract_report_output(
            target_contract_report,
            issues,
            "adjudication_output_target_contract",
        )
        if status == "pass" and target_contract_status != "pass":
            _issue(
                issues,
                "adjudication_output_target_contract_status_mismatch",
                "a passing adjudication must embed a passing target-contract report",
            )
        if status == "pass" and target_contract_status == "pass":
            _validate_target_contract_report_link(
                target_contract_report,
                target_id,
                issues,
                "adjudication_output_target_contract",
            )
    verified_artifacts = adjudication.get("verified_artifacts")
    if not isinstance(verified_artifacts, dict):
        _issue(issues, "adjudication_output_verified_artifacts_invalid", "verified_artifacts must be an object")
    else:
        _reject_unknown_keys(
            verified_artifacts,
            {"source_artifacts", "graph_artifacts", "report_artifacts"},
            issues,
            "adjudication_output_verified_artifacts_unknown_field",
            "verified_artifacts",
        )
        for field in ("source_artifacts", "graph_artifacts", "report_artifacts"):
            _validate_verified_artifact_rows(
                verified_artifacts.get(field),
                issues,
                f"adjudication_output_{field}",
                require_payload=field != "source_artifacts",
                require_complete=status == "pass",
            )
        _validate_verified_artifact_provenance(
            verified_artifacts,
            verified_identity,
            status,
            issues,
        )
    _validate_input_sha256(
        adjudication.get("input_sha256"),
        issues,
        "adjudication_output",
        allow_null=False,
        allowed_keys={"run_spec", "target_contract", "evidence"},
        required_keys={"target_contract", "evidence"} if status == "pass" else set(),
    )
    return {
        "schema_version": OPENTITAN_EVIDENCE_SCHEMA_VERSION,
        "surface": OPENTITAN_ADJUDICATION_VALIDATION_REPORT_SURFACE,
        "status": "fail" if issues else "pass",
        "issue_count": len(issues),
        "issues": issues,
    }


def validate_adjudication_summary_document(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Validate machine-readable summary output for internal consistency."""

    issues: list[dict[str, str]] = []
    _reject_unknown_keys(
        summary,
        {
            "schema_version",
            "surface",
            "status",
            "input_sha256",
            "evidence_count",
            "pass_count",
            "fail_count",
            "check_counts",
            "results",
            "run_spec",
            "target_contract",
        },
        issues,
        "summary_output_unknown_field",
        "adjudication summary",
    )
    if summary.get("surface") != OPENTITAN_ADJUDICATION_SUMMARY_SURFACE:
        _issue(issues, "summary_output_surface_mismatch", "summary surface is not recognized")
    if not _has_schema_version(summary.get("schema_version")):
        _issue(issues, "summary_output_schema_version_mismatch", "summary schema_version must be integer 1")
    status = summary.get("status")
    if status not in {"pass", "fail"}:
        _issue(issues, "summary_output_status_invalid", "summary status must be pass or fail")
    results = summary.get("results")
    normalized_results: list[Mapping[str, Any]] = []
    if not isinstance(results, list):
        _issue(issues, "summary_output_results_invalid", "summary results must be a list")
    elif not results:
        _issue(issues, "summary_output_results_empty", "summary results must be nonempty")
    else:
        for index, result in enumerate(results):
            if not isinstance(result, Mapping):
                _issue(issues, "summary_output_result_invalid", f"summary result {index} must be an object")
                continue
            normalized_results.append(result)
            _reject_unknown_keys(
                result,
                {
                    "index",
                    "evidence_path",
                    "target_id",
                    "status",
                    "verified_identity",
                    "issue_count",
                    "issue_codes",
                    "issues",
                    "input_sha256",
                },
                issues,
                "summary_output_result_unknown_field",
                f"summary result {index}",
            )
            for required_field in (
                "evidence_path",
                "target_id",
                "verified_identity",
                "input_sha256",
            ):
                if required_field not in result:
                    _issue(
                        issues,
                        f"summary_output_result_{required_field}_missing",
                        f"summary result {index} must contain {required_field}",
                    )
            result_index = result.get("index")
            if not _is_int(result_index) or result_index != index:
                _issue(issues, "summary_output_result_index_mismatch", f"summary result {index} has wrong index")
            evidence_path = result.get("evidence_path")
            if evidence_path is not None and (not _is_string(evidence_path) or not evidence_path):
                _issue(issues, "summary_output_result_evidence_path_invalid", f"summary result {index} evidence_path must be nonempty or null")
            result_target_id = result.get("target_id")
            if result_target_id is not None and (not _is_string(result_target_id) or not result_target_id):
                _issue(issues, "summary_output_result_target_id_invalid", f"summary result {index} target_id must be nonempty or null")
            result_status = result.get("status")
            if result_status not in {"pass", "fail"}:
                _issue(issues, "summary_output_result_status_invalid", f"summary result {index} status must be pass or fail")
            if "verified_identity" not in result:
                _issue(
                    issues,
                    "summary_output_result_verified_identity_missing",
                    f"summary result {index} must contain verified_identity, using null for failure",
                )
            _validate_verified_identity(
                result.get("verified_identity"),
                result.get("target_id"),
                result_status,
                issues,
                f"summary_output_result_{index}",
            )
            result_issues = _validated_output_issues(result.get("issues"), issues, "summary_output_result")
            _validate_output_issue_count(result, result_issues, issues, "summary_output_result")
            expected_status = "fail" if result_issues else "pass"
            if result_status in {"pass", "fail"} and result_status != expected_status:
                _issue(issues, "summary_output_result_status_mismatch", f"summary result {index} status must match issue presence")
            expected_codes = [issue["code"] for issue in result_issues]
            if result.get("issue_codes") != expected_codes:
                _issue(issues, "summary_output_result_issue_codes_mismatch", f"summary result {index} issue_codes must match issues")
            required_result_hashes = (
                {"target_contract", "evidence"}
                | ({"run_spec"} if "run_spec" in summary else set())
                if result_status == "pass"
                else set()
            )
            _validate_input_sha256(
                result.get("input_sha256"),
                issues,
                "summary_output_result",
                allow_null=False,
                allowed_keys={"run_spec", "target_contract", "evidence"},
                required_keys=required_result_hashes,
            )
    evidence_count = summary.get("evidence_count")
    pass_count = summary.get("pass_count")
    fail_count = summary.get("fail_count")
    if not _is_int(evidence_count) or evidence_count < 1:
        _issue(issues, "summary_output_evidence_count_invalid", "summary evidence_count must be a positive integer")
    elif evidence_count != len(normalized_results):
        _issue(issues, "summary_output_evidence_count_mismatch", "summary evidence_count must equal results length")
    observed_pass = sum(1 for result in normalized_results if result.get("status") == "pass")
    observed_fail = sum(1 for result in normalized_results if result.get("status") != "pass")
    if not _is_int(pass_count) or pass_count < 0:
        _issue(issues, "summary_output_pass_count_invalid", "summary pass_count must be a nonnegative integer")
    elif pass_count != observed_pass:
        _issue(issues, "summary_output_pass_count_mismatch", "summary pass_count must equal passing results")
    if not _is_int(fail_count) or fail_count < 0:
        _issue(issues, "summary_output_fail_count_invalid", "summary fail_count must be a nonnegative integer")
    elif fail_count != observed_fail:
        _issue(issues, "summary_output_fail_count_mismatch", "summary fail_count must equal failing results")
    if status in {"pass", "fail"}:
        expected_status = "fail" if observed_fail else "pass"
        if status != expected_status:
            _issue(issues, "summary_output_status_mismatch", "summary status must match failing result count")
    _validate_summary_check_counts(summary.get("check_counts"), normalized_results, issues)
    required_summary_hashes = (
        {"target_contract"} | ({"run_spec"} if "run_spec" in summary else set())
        if status == "pass"
        else set()
    )
    _validate_input_sha256(
        summary.get("input_sha256"),
        issues,
        "summary_output",
        allowed_keys={"run_spec", "target_contract"},
        required_keys=required_summary_hashes,
    )
    summary_hashes = summary.get("input_sha256")
    if isinstance(summary_hashes, Mapping):
        for index, result in enumerate(normalized_results):
            if result.get("status") != "pass":
                continue
            result_hashes = result.get("input_sha256")
            if not isinstance(result_hashes, Mapping):
                continue
            for name in ("target_contract", "run_spec"):
                if name in summary_hashes and result_hashes.get(name) != summary_hashes.get(name):
                    _issue(
                        issues,
                        "summary_output_input_sha256_mismatch",
                        f"summary result {index} {name} hash must match the summary input hash",
                    )
    run_spec_report = summary.get("run_spec")
    if run_spec_report is not None:
        run_spec_status = _validate_validation_report_output(
            run_spec_report,
            OPENTITAN_ADJUDICATION_RUN_SPEC_REPORT_SURFACE,
            issues,
            "summary_output_run_spec",
        )
        if status == "pass" and run_spec_status != "pass":
            _issue(
                issues,
                "summary_output_run_spec_status_mismatch",
                "a passing summary cannot embed a failing run-spec report",
            )
    target_contract_report = summary.get("target_contract")
    if target_contract_report is not None:
        target_contract_status = _validate_target_contract_report_output(
            target_contract_report,
            issues,
            "summary_output_target_contract",
        )
        if status == "pass" and target_contract_status != "pass":
            _issue(
                issues,
                "summary_output_target_contract_status_mismatch",
                "a passing summary cannot embed a failing target-contract report",
            )
        if target_contract_status == "pass":
            for index, result in enumerate(normalized_results):
                if result.get("status") == "pass":
                    _validate_target_contract_report_link(
                        target_contract_report,
                        result.get("target_id"),
                        issues,
                        f"summary_output_target_contract_result_{index}",
                    )
    return {
        "schema_version": OPENTITAN_EVIDENCE_SCHEMA_VERSION,
        "surface": OPENTITAN_ADJUDICATION_SUMMARY_VALIDATION_REPORT_SURFACE,
        "status": "fail" if issues else "pass",
        "issue_count": len(issues),
        "issues": issues,
    }


def _validated_output_issues(
    value: Any,
    issues: list[dict[str, str]],
    prefix: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        _issue(issues, f"{prefix}_issues_invalid", "issues must be a list")
        return []
    result: list[dict[str, str]] = []
    for index, issue in enumerate(value):
        if not isinstance(issue, Mapping):
            _issue(issues, f"{prefix}_issue_invalid", f"issue {index} must be an object")
            continue
        _reject_unknown_keys(
            issue,
            {"code", "detail"},
            issues,
            f"{prefix}_issue_unknown_field",
            f"{prefix} issue {index}",
        )
        code = issue.get("code")
        detail = issue.get("detail")
        if not isinstance(code, str) or not code:
            _issue(issues, f"{prefix}_issue_code_invalid", f"issue {index} code must be a nonempty string")
            continue
        if not isinstance(detail, str):
            _issue(issues, f"{prefix}_issue_detail_invalid", f"issue {index} detail must be a string")
            continue
        result.append({"code": code, "detail": detail})
    return result


def _validate_output_issue_count(
    value: Mapping[str, Any],
    output_issues: Sequence[Mapping[str, str]],
    issues: list[dict[str, str]],
    prefix: str,
) -> None:
    issue_count = value.get("issue_count")
    if not _is_int(issue_count) or issue_count < 0:
        _issue(issues, f"{prefix}_issue_count_invalid", "issue_count must be a nonnegative integer")
    elif issue_count != len(output_issues):
        _issue(issues, f"{prefix}_issue_count_mismatch", "issue_count must equal len(issues)")


def _validate_output_checks(
    checks: Any,
    output_issues: Sequence[Mapping[str, str]],
    issues: list[dict[str, str]],
    prefix: str,
) -> None:
    expected = _expected_check_issue_codes(output_issues)
    if not isinstance(checks, list):
        _issue(issues, f"{prefix}_checks_invalid", "checks must be a list")
        return
    seen: set[str] = set()
    for check in checks:
        if not isinstance(check, Mapping):
            _issue(issues, f"{prefix}_check_invalid", "check entries must be objects")
            continue
        _reject_unknown_keys(
            check,
            {"name", "status", "issue_codes"},
            issues,
            f"{prefix}_check_unknown_field",
            f"{prefix} check",
        )
        name = check.get("name")
        if not isinstance(name, str) or not name:
            _issue(issues, f"{prefix}_check_name_invalid", "check name must be a nonempty string")
            continue
        if name in seen:
            _issue(issues, f"{prefix}_check_duplicate", f"check {name!r} is duplicated")
        seen.add(name)
        expected_codes = expected.get(name, [])
        if check.get("issue_codes") != expected_codes:
            _issue(issues, f"{prefix}_check_issue_codes_mismatch", f"check {name} issue_codes do not match issues")
        expected_status = "fail" if expected_codes else "pass"
        if check.get("status") != expected_status:
            _issue(issues, f"{prefix}_check_status_mismatch", f"check {name} status does not match issue_codes")
    missing = sorted(set(expected) - seen)
    if missing:
        _issue(issues, f"{prefix}_check_missing", f"missing checks: {missing}")
    extra = sorted(seen - set(expected))
    if extra:
        _issue(issues, f"{prefix}_check_extra", f"unexpected checks: {extra}")


def _expected_check_issue_codes(output_issues: Sequence[Mapping[str, str]]) -> dict[str, list[str]]:
    expected: dict[str, set[str]] = {
        name: set()
        for name in _CHECK_NAMES
    }
    for issue in output_issues:
        code = issue["code"]
        expected.setdefault(_check_name_for_issue(code), set()).add(code)
    return {
        name: sorted(codes)
        for name, codes in expected.items()
    }


def _validate_summary_check_counts(
    check_counts: Any,
    results: Sequence[Mapping[str, Any]],
    issues: list[dict[str, str]],
) -> None:
    if not isinstance(check_counts, Mapping):
        _issue(issues, "summary_output_check_counts_invalid", "check_counts must be an object")
        return
    for name, counts in check_counts.items():
        if not isinstance(name, str) or not name or not isinstance(counts, Mapping):
            _issue(issues, "summary_output_check_count_invalid", "check_counts must map names to count objects")
            continue
        _reject_unknown_keys(
            counts,
            {"pass", "fail"},
            issues,
            "summary_output_check_count_unknown_field",
            f"check_counts {name}",
        )
        for count_name in ("pass", "fail"):
            count = counts.get(count_name)
            if not _is_int(count) or count < 0:
                _issue(issues, "summary_output_check_count_invalid", f"check_counts {name}.{count_name} must be nonnegative")
    expected = {
        name: {"pass": 0, "fail": 0}
        for name in _CHECK_NAMES
    }
    for result in results:
        issue_codes = result.get("issue_codes")
        if not isinstance(issue_codes, list):
            issue_codes = []
        failed_checks = {
            _check_name_for_issue(code)
            for code in issue_codes
            if isinstance(code, str)
        }
        for name in sorted(set(expected) | failed_checks):
            expected.setdefault(name, {"pass": 0, "fail": 0})
            expected[name]["fail" if name in failed_checks else "pass"] += 1
    if check_counts != expected:
        _issue(issues, "summary_output_check_counts_mismatch", "check_counts must be derived from result issue codes")


def _validate_verified_artifact_rows(
    rows: Any,
    issues: list[dict[str, str]],
    prefix: str,
    *,
    require_payload: bool,
    require_complete: bool,
) -> None:
    if not isinstance(rows, list):
        _issue(issues, f"{prefix}_invalid", f"{prefix} must be a list")
        return
    seen_roles: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            _issue(issues, f"{prefix}_row_invalid", f"{prefix} row {index} must be an object")
            continue
        _reject_unknown_keys(
            row,
            {
                "role",
                "path",
                "sha256",
                "payload_sha256",
                "source_artifact_roles",
                "source_artifact_sha256_by_role",
            },
            issues,
            f"{prefix}_unknown_field",
            f"{prefix} row {index}",
        )
        for name in ("role", "path"):
            if not isinstance(row.get(name), str) or not row.get(name):
                _issue(issues, f"{prefix}_row_invalid", f"{prefix} row {index} {name} must be a nonempty string")
        role = row.get("role")
        if isinstance(role, str) and role:
            if role in seen_roles:
                _issue(issues, f"{prefix}_role_duplicate", f"{prefix} role {role!r} is duplicated")
            seen_roles.add(role)
        path = row.get("path")
        if isinstance(path, str) and path and not _is_safe_relative_path(path):
            _issue(issues, f"{prefix}_path_invalid", f"{prefix} row {index} path must be relative without parent traversal")
        if not _hex(row.get("sha256"), 64):
            _issue(issues, f"{prefix}_sha_invalid", f"{prefix} row {index} sha256 must be lowercase 64-hex")
        if require_payload:
            if require_complete and not _hex(row.get("payload_sha256"), 64):
                _issue(issues, f"{prefix}_payload_sha_invalid", f"{prefix} row {index} payload_sha256 must be lowercase 64-hex")
            elif "payload_sha256" in row and not _hex(row.get("payload_sha256"), 64):
                _issue(issues, f"{prefix}_payload_sha_invalid", f"{prefix} row {index} payload_sha256 must be lowercase 64-hex")
        elif "payload_sha256" in row:
            _issue(issues, f"{prefix}_payload_sha_unexpected", f"{prefix} row {index} must not contain payload_sha256")
        source_roles = row.get("source_artifact_roles")
        source_hashes = row.get("source_artifact_sha256_by_role")
        if require_payload and require_complete and not _nonempty_unique_strings(source_roles):
            _issue(
                issues,
                f"{prefix}_source_roles_invalid",
                f"{prefix} row {index} must retain nonempty unique source roles",
            )
        elif require_payload and source_roles is not None and not _nonempty_unique_strings(source_roles):
            _issue(
                issues,
                f"{prefix}_source_roles_invalid",
                f"{prefix} row {index} source roles must be nonempty unique strings",
            )
        elif not require_payload and source_roles is not None:
            _issue(
                issues,
                f"{prefix}_source_roles_unexpected",
                f"{prefix} row {index} must not contain source artifact roles",
            )
        if require_payload and require_complete and (
            not isinstance(source_hashes, Mapping) or not source_hashes
        ):
            _issue(issues, f"{prefix}_source_hashes_invalid", f"{prefix} row {index} must map at least one source role to sha256")
        elif not require_payload and source_hashes is not None:
            _issue(
                issues,
                f"{prefix}_source_hashes_unexpected",
                f"{prefix} row {index} must not contain source artifact hashes",
            )
        elif source_hashes is not None:
            if not isinstance(source_hashes, Mapping):
                _issue(issues, f"{prefix}_source_hashes_invalid", f"{prefix} row {index} source hashes must be an object")
            else:
                for role, digest in source_hashes.items():
                    if not isinstance(role, str) or not role or not _hex(digest, 64):
                        _issue(issues, f"{prefix}_source_hash_invalid", f"{prefix} row {index} source hashes must map roles to sha256")
        if _nonempty_unique_strings(source_roles) and isinstance(source_hashes, Mapping):
            if set(source_roles) != set(source_hashes):
                _issue(
                    issues,
                    f"{prefix}_source_provenance_incomplete",
                    f"{prefix} row {index} source role and hash-map keys must match exactly",
                )


def _validate_verified_identity(
    value: Any,
    target_id: Any,
    status: Any,
    issues: list[dict[str, str]],
    prefix: str,
) -> Mapping[str, Any] | None:
    if value is None:
        if status == "pass":
            _issue(issues, f"{prefix}_verified_identity_missing", "a passing result must include verified_identity")
        return None
    if not isinstance(value, Mapping):
        _issue(issues, f"{prefix}_verified_identity_invalid", "verified_identity must be an object or null")
        return None
    _reject_unknown_keys(
        value,
        {
            "target_id",
            "ip",
            "issue",
            "revisions",
            "checkpoint_identity",
            "oracle_identity",
            "campaign_action_domain",
            "campaign_action_domain_sha256",
            "reproduction_action_domain",
            "reproduction_action_domain_sha256",
            "semantic_observables",
            "oracle_field",
            "semantic_manifest_sha256",
        },
        issues,
        f"{prefix}_verified_identity_unknown_field",
        "verified_identity",
    )
    if value.get("target_id") != target_id or not _is_string(value.get("target_id")) or not value.get("target_id"):
        _issue(issues, f"{prefix}_verified_identity_target_mismatch", "verified_identity target_id must match the result target_id")
    for name in ("ip", "issue", "checkpoint_identity", "oracle_identity", "oracle_field"):
        if not _is_string(value.get(name)) or not value.get(name):
            _issue(issues, f"{prefix}_verified_identity_string_invalid", f"verified_identity {name} must be nonempty")
    revisions = value.get("revisions")
    if not isinstance(revisions, Mapping) or set(revisions) != set(_REVISION_LABELS):
        _issue(issues, f"{prefix}_verified_identity_revisions_invalid", "verified_identity revisions must contain exactly bad and fixed")
    elif not all(_hex(revisions.get(label), 40) for label in _REVISION_LABELS):
        _issue(issues, f"{prefix}_verified_identity_revision_sha_invalid", "verified_identity revisions must be lowercase 40-hex")
    elif revisions["bad"] == revisions["fixed"]:
        _issue(issues, f"{prefix}_verified_identity_revisions_not_distinct", "verified_identity bad and fixed revisions must differ")
    for name in ("campaign_action_domain", "reproduction_action_domain"):
        domain = value.get(name)
        if not _nonempty_unique_strings(domain):
            _issue(issues, f"{prefix}_verified_identity_domain_invalid", f"verified_identity {name} must be nonempty unique strings")
            continue
        digest_name = f"{name}_sha256"
        if value.get(digest_name) != _sha256_bytes(_canonical_bytes(domain)):
            _issue(issues, f"{prefix}_verified_identity_domain_hash_mismatch", f"verified_identity {digest_name} does not match {name}")
    campaign_domain = value.get("campaign_action_domain")
    reproduction_domain = value.get("reproduction_action_domain")
    if _nonempty_unique_strings(campaign_domain) and _nonempty_unique_strings(reproduction_domain):
        if not set(reproduction_domain).issubset(set(campaign_domain)):
            _issue(issues, f"{prefix}_verified_identity_reproduction_domain_mismatch", "verified reproduction domain must be a campaign-domain subset")
    semantic_observables = value.get("semantic_observables")
    if not _nonempty_unique_strings(semantic_observables):
        _issue(issues, f"{prefix}_verified_identity_observables_invalid", "verified_identity semantic_observables must be nonempty unique strings")
    elif value.get("oracle_field") in semantic_observables:
        _issue(
            issues,
            f"{prefix}_verified_identity_oracle_field_not_distinct",
            "verified_identity oracle_field must be distinct from semantic_observables",
        )
    manifests = value.get("semantic_manifest_sha256")
    if not isinstance(manifests, Mapping) or set(manifests) != set(_REVISION_LABELS):
        _issue(issues, f"{prefix}_verified_identity_manifests_invalid", "verified_identity semantic manifests must contain bad and fixed")
    elif not all(_hex(manifests.get(label), 64) for label in _REVISION_LABELS):
        _issue(issues, f"{prefix}_verified_identity_manifest_sha_invalid", "verified_identity semantic manifests must be lowercase 64-hex")
    return value


def _validate_verified_artifact_provenance(
    verified_artifacts: Mapping[str, Any],
    verified_identity: Mapping[str, Any] | None,
    status: Any,
    issues: list[dict[str, str]],
) -> None:
    source_rows = verified_artifacts.get("source_artifacts")
    graph_rows = verified_artifacts.get("graph_artifacts")
    report_rows = verified_artifacts.get("report_artifacts")
    if status == "pass":
        for name, rows in (
            ("source_artifacts", source_rows),
            ("graph_artifacts", graph_rows),
            ("report_artifacts", report_rows),
        ):
            if not isinstance(rows, list) or not rows:
                _issue(issues, f"adjudication_output_{name}_empty", f"a passing adjudication requires nonempty {name}")
    if not isinstance(source_rows, list):
        return
    source_by_role = {
        row.get("role"): row.get("sha256")
        for row in source_rows
        if isinstance(row, Mapping) and isinstance(row.get("role"), str) and _hex(row.get("sha256"), 64)
    }
    if status == "pass":
        required_roles = {
            "semantic_manifest_bad",
            "semantic_manifest_fixed",
            "equivalence_report",
            "campaign_report",
        }
        missing = sorted(required_roles - set(source_by_role))
        if missing:
            _issue(issues, "adjudication_output_source_roles_missing", f"verified source roles are missing: {missing}")
        if isinstance(verified_identity, Mapping):
            manifests = verified_identity.get("semantic_manifest_sha256")
            if isinstance(manifests, Mapping):
                for label in _REVISION_LABELS:
                    if source_by_role.get(f"semantic_manifest_{label}") != manifests.get(label):
                        _issue(
                            issues,
                            "adjudication_output_semantic_manifest_provenance_mismatch",
                            f"semantic_manifest_{label} does not match verified_identity",
                        )
    for field, rows in (("graph_artifacts", graph_rows), ("report_artifacts", report_rows)):
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            source_hashes = row.get("source_artifact_sha256_by_role")
            if not isinstance(source_hashes, Mapping) or not source_hashes:
                if status == "pass":
                    _issue(
                        issues,
                        f"adjudication_output_{field}_source_provenance_empty",
                        f"a passing {field} row must retain nonempty source provenance",
                    )
                continue
            for role, digest in source_hashes.items():
                if source_by_role.get(role) != digest:
                    _issue(
                        issues,
                        f"adjudication_output_{field}_source_mismatch",
                        f"{field} row {index} source {role!r} does not match verified source artifacts",
                    )


def _validate_input_sha256(
    value: Any,
    issues: list[dict[str, str]],
    prefix: str,
    *,
    allow_null: bool = True,
    allowed_keys: set[str] | None = None,
    required_keys: set[str] | None = None,
) -> None:
    if not isinstance(value, Mapping):
        _issue(issues, f"{prefix}_input_sha256_invalid", "input_sha256 must be an object")
        return
    observed_keys = set(value)
    if allowed_keys is not None:
        unknown = sorted(str(key) for key in observed_keys - allowed_keys)
        if unknown:
            _issue(
                issues,
                f"{prefix}_input_sha256_unknown",
                f"input_sha256 has unknown keys: {unknown}",
            )
    if required_keys is not None:
        missing = sorted(required_keys - observed_keys)
        if missing:
            _issue(
                issues,
                f"{prefix}_input_sha256_missing",
                f"input_sha256 is missing required keys: {missing}",
            )
    for name, digest in value.items():
        if digest is None and allow_null:
            continue
        if not _hex(digest, 64):
            requirement = "null or lowercase 64-hex" if allow_null else "lowercase 64-hex"
            _issue(issues, f"{prefix}_input_sha256_invalid", f"input_sha256 {name!r} must be {requirement}")


def _validate_validation_report_output(
    value: Any,
    expected_surface: str,
    issues: list[dict[str, str]],
    prefix: str,
    allowed_extra: set[str] | None = None,
) -> str | None:
    if not isinstance(value, Mapping):
        _issue(issues, f"{prefix}_invalid", f"{prefix} must be an object")
        return None
    allowed = {"schema_version", "surface", "status", "checks", "issue_count", "issues", "input_sha256"}
    if allowed_extra:
        allowed.update(allowed_extra)
    _reject_unknown_keys(value, allowed, issues, f"{prefix}_unknown_field", prefix)
    if value.get("surface") != expected_surface:
        _issue(issues, f"{prefix}_surface_mismatch", f"{prefix} surface is not recognized")
    if not _has_schema_version(value.get("schema_version")):
        _issue(issues, f"{prefix}_schema_version_mismatch", f"{prefix} schema_version must be integer 1")
    status = value.get("status")
    if status not in {"pass", "fail"}:
        _issue(issues, f"{prefix}_status_invalid", f"{prefix} status must be pass or fail")
        status = None
    report_issues = _validated_output_issues(value.get("issues"), issues, prefix)
    _validate_output_issue_count(value, report_issues, issues, prefix)
    if status in {"pass", "fail"}:
        expected_status = "fail" if report_issues else "pass"
        if status != expected_status:
            _issue(issues, f"{prefix}_status_mismatch", f"{prefix} status must match issue presence")
    _validate_output_checks(value.get("checks"), report_issues, issues, prefix)
    if "input_sha256" in value:
        expected_input_names = {
            OPENTITAN_ADJUDICATION_RUN_SPEC_REPORT_SURFACE: {"run_spec"},
            OPENTITAN_TARGET_CONTRACT_REPORT_SURFACE: {"target_contract"},
            OPENTITAN_ADJUDICATION_VALIDATION_REPORT_SURFACE: {"adjudication"},
            OPENTITAN_ADJUDICATION_SUMMARY_VALIDATION_REPORT_SURFACE: {"summary"},
        }.get(expected_surface)
        _validate_input_sha256(
            value.get("input_sha256"),
            issues,
            prefix,
            allow_null=False,
            allowed_keys=expected_input_names,
        )
    return status


def _validate_target_contract_report_output(
    value: Any,
    issues: list[dict[str, str]],
    prefix: str,
) -> str | None:
    status = _validate_validation_report_output(
        value,
        OPENTITAN_TARGET_CONTRACT_REPORT_SURFACE,
        issues,
        prefix,
        {"target_count", "targets"},
    )
    if not isinstance(value, Mapping):
        return status
    targets = value.get("targets")
    if not isinstance(targets, list):
        _issue(issues, f"{prefix}_targets_invalid", f"{prefix} targets must be a list")
        normalized_targets: list[Any] = []
    else:
        normalized_targets = targets
    if not _is_int(value.get("target_count")) or value.get("target_count") < 0:
        _issue(issues, f"{prefix}_target_count_invalid", f"{prefix} target_count must be a nonnegative integer")
    elif value.get("target_count") != len(normalized_targets):
        _issue(issues, f"{prefix}_target_count_mismatch", f"{prefix} target_count must equal len(targets)")
    target_ids: list[str] = []
    target_statuses: list[str] = []
    for index, target in enumerate(normalized_targets):
        if not isinstance(target, Mapping):
            _issue(issues, f"{prefix}_target_invalid", f"{prefix} target {index} must be an object")
            continue
        _reject_unknown_keys(
            target,
            {"index", "target_id", "status", "issue_count"},
            issues,
            f"{prefix}_target_unknown_field",
            f"{prefix} target {index}",
        )
        if not _is_int(target.get("index")) or target.get("index") != index:
            _issue(issues, f"{prefix}_target_index_mismatch", f"{prefix} target {index} has wrong index")
        if "target_id" not in target:
            _issue(issues, f"{prefix}_target_id_missing", f"{prefix} target {index} must contain target_id")
        target_id = target.get("target_id")
        if target_id is not None and (not _is_string(target_id) or not target_id):
            _issue(issues, f"{prefix}_target_id_invalid", f"{prefix} target {index} target_id must be nonempty or null")
        elif isinstance(target_id, str) and target_id:
            target_ids.append(target_id)
        target_status = target.get("status")
        if target_status not in {"pass", "fail"}:
            _issue(issues, f"{prefix}_target_status_invalid", f"{prefix} target {index} status must be pass or fail")
        else:
            target_statuses.append(target_status)
        target_issue_count = target.get("issue_count")
        if not _is_int(target_issue_count) or target_issue_count < 0:
            _issue(issues, f"{prefix}_target_issue_count_invalid", f"{prefix} target {index} issue_count must be nonnegative")
        elif target_status in {"pass", "fail"}:
            expected_target_status = "fail" if target_issue_count else "pass"
            if target_status != expected_target_status:
                _issue(issues, f"{prefix}_target_status_mismatch", f"{prefix} target {index} status must match issue_count")
    if status == "pass" and len(set(target_ids)) != len(target_ids):
        _issue(issues, f"{prefix}_target_id_duplicate", f"{prefix} target_id values must be unique")
    if status == "pass":
        if not normalized_targets:
            _issue(issues, f"{prefix}_targets_empty", f"a passing {prefix} must contain at least one target")
        if len(target_ids) != len(normalized_targets):
            _issue(issues, f"{prefix}_target_id_missing", f"every target in a passing {prefix} must have a nonempty target_id")
        if any(target_status != "pass" for target_status in target_statuses) or len(target_statuses) != len(normalized_targets):
            _issue(issues, f"{prefix}_target_failure_unreported", f"a passing {prefix} cannot contain a failing target")
    return status


def _validate_target_contract_report_link(
    report: Any,
    target_id: Any,
    issues: list[dict[str, str]],
    prefix: str,
) -> None:
    if not isinstance(report, Mapping) or not isinstance(report.get("targets"), list):
        return
    matches = [
        row
        for row in report["targets"]
        if isinstance(row, Mapping) and row.get("target_id") == target_id
    ]
    if len(matches) != 1:
        _issue(
            issues,
            f"{prefix}_target_link_mismatch",
            "passing output target_id must occur exactly once in the embedded target-contract report",
        )


def format_adjudication_summary_report(summary: Mapping[str, Any]) -> str:
    """Render a human-readable summary for multiple evidence bundles."""

    lines = [
        "# OpenTitan External Evidence Summary",
        "",
        f"Status: `{summary.get('status', 'unknown')}`",
        f"Evidence bundles: `{summary.get('evidence_count', 0)}`",
        f"Pass: `{summary.get('pass_count', 0)}`",
        f"Fail: `{summary.get('fail_count', 0)}`",
        "",
        "## Inputs",
        "",
    ]
    inputs = summary.get("input_sha256")
    if isinstance(inputs, Mapping):
        for name, digest in sorted(inputs.items()):
            lines.append(f"- `{name}` `{digest}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Checks", ""])
    run_spec_report = summary.get("run_spec")
    if isinstance(run_spec_report, Mapping):
        lines.extend([
            f"- `run_spec` status=`{run_spec_report.get('status', 'unknown')}` "
            f"issues=`{run_spec_report.get('issue_count', 0)}`",
        ])
    target_contract_report = summary.get("target_contract")
    if isinstance(target_contract_report, Mapping):
        lines.extend([
            f"- `target_contract` status=`{target_contract_report.get('status', 'unknown')}` "
            f"issues=`{target_contract_report.get('issue_count', 0)}`",
        ])
    check_counts = summary.get("check_counts")
    if isinstance(check_counts, Mapping):
        for name in sorted(check_counts):
            counts = check_counts[name]
            if isinstance(counts, Mapping):
                lines.append(
                    f"- `{name}` pass=`{counts.get('pass', 0)}` "
                    f"fail=`{counts.get('fail', 0)}`"
                )
    else:
        lines.append("- none")
    lines.extend(["", "## Results", ""])
    results = summary.get("results")
    if isinstance(results, list) and results:
        for result in results:
            if not isinstance(result, Mapping):
                continue
            path = result.get("evidence_path", "<unknown>")
            target_id = result.get("target_id", "<unknown>")
            status = result.get("status", "<unknown>")
            issue_count = result.get("issue_count", 0)
            lines.append(
                f"- `{path}` target=`{target_id}` "
                f"status=`{status}` issues=`{issue_count}`"
            )
            identity = result.get("verified_identity")
            if isinstance(identity, Mapping):
                revisions = identity.get("revisions")
                if isinstance(revisions, Mapping):
                    lines.append(
                        f"  - revisions bad=`{revisions.get('bad')}` "
                        f"fixed=`{revisions.get('fixed')}`"
                    )
                lines.append(
                    f"  - checkpoint=`{identity.get('checkpoint_identity')}` "
                    f"oracle=`{identity.get('oracle_identity')}`"
                )
                lines.append(
                    "  - campaign-domain-sha256="
                    f"`{identity.get('campaign_action_domain_sha256')}`"
                )
            issues = result.get("issues")
            if isinstance(issues, list):
                for issue in issues:
                    if not isinstance(issue, Mapping):
                        continue
                    lines.append(
                        f"  - `{issue.get('code', 'unknown')}`: "
                        f"{issue.get('detail', '')}"
                    )
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def format_output_validation_report(report: Mapping[str, Any]) -> str:
    """Render a human-readable validation report for stored adjudication output."""

    lines = [
        "# OpenTitan Output Validation",
        "",
        f"Surface: `{report.get('surface', 'unknown')}`",
        f"Status: `{report.get('status', 'unknown')}`",
        f"Issues: `{report.get('issue_count', 0)}`",
        "",
        "## Issues",
        "",
    ]
    issues = report.get("issues")
    if isinstance(issues, list) and issues:
        for issue in issues:
            if not isinstance(issue, Mapping):
                continue
            lines.append(f"- `{issue.get('code', 'unknown')}`: {issue.get('detail', '')}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def format_target_contract_report(report: Mapping[str, Any]) -> str:
    """Render a human-readable target-contract validation report."""

    lines = [
        "# OpenTitan Target Contract Validation",
        "",
        f"Status: `{report.get('status', 'unknown')}`",
        f"Targets: `{report.get('target_count', 0)}`",
        f"Issues: `{report.get('issue_count', 0)}`",
        "",
        "## Checks",
        "",
    ]
    checks = report.get("checks")
    if isinstance(checks, list) and checks:
        for check in checks:
            if not isinstance(check, Mapping):
                continue
            lines.append(
                f"- `{check.get('name', '<unknown>')}`: "
                f"`{check.get('status', '<unknown>')}`"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Targets", ""])
    targets = report.get("targets")
    if isinstance(targets, list) and targets:
        for target in targets:
            if not isinstance(target, Mapping):
                continue
            lines.append(
                f"- `{target.get('target_id', '<unknown>')}` "
                f"status=`{target.get('status', '<unknown>')}` "
                f"issues=`{target.get('issue_count', 0)}`"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Issues", ""])
    issues = report.get("issues")
    if isinstance(issues, list) and issues:
        for issue in issues:
            if not isinstance(issue, Mapping):
                continue
            lines.append(f"- `{issue.get('code', 'unknown')}`: {issue.get('detail', '')}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def format_adjudication_report(adjudication: Mapping[str, Any]) -> str:
    """Render a small human-readable adjudication report."""

    status = adjudication.get("status", "unknown")
    target_id = adjudication.get("target_id", "<unknown>")
    lines = [
        "# OpenTitan External Evidence Adjudication",
        "",
        f"Target: `{target_id}`",
        f"Status: `{status}`",
        f"Issues: `{adjudication.get('issue_count', 0)}`",
        "",
        "## Verified Identity",
        "",
    ]
    identity = adjudication.get("verified_identity")
    if isinstance(identity, Mapping):
        revisions = identity.get("revisions")
        manifests = identity.get("semantic_manifest_sha256")
        lines.extend(
            [
                f"- IP: `{identity.get('ip')}`",
                f"- Issue: `{identity.get('issue')}`",
                f"- Checkpoint: `{identity.get('checkpoint_identity')}`",
                f"- Oracle: `{identity.get('oracle_identity')}`",
                f"- Oracle field: `{identity.get('oracle_field')}`",
                f"- Campaign action-domain SHA-256: `{identity.get('campaign_action_domain_sha256')}`",
                f"- Reproduction action-domain SHA-256: `{identity.get('reproduction_action_domain_sha256')}`",
            ]
        )
        if isinstance(revisions, Mapping):
            lines.extend(
                [
                    f"- Bad revision: `{revisions.get('bad')}`",
                    f"- Fixed revision: `{revisions.get('fixed')}`",
                ]
            )
        if isinstance(manifests, Mapping):
            lines.extend(
                [
                    f"- Bad semantic manifest: `{manifests.get('bad')}`",
                    f"- Fixed semantic manifest: `{manifests.get('fixed')}`",
                ]
            )
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Checks",
        "",
    ])
    checks = adjudication.get("checks")
    if isinstance(checks, list) and checks:
        for check in checks:
            if not isinstance(check, dict):
                continue
            name = check.get("name", "<unknown>")
            check_status = check.get("status", "<unknown>")
            lines.append(f"- `{name}`: `{check_status}`")
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Issues",
        "",
    ])
    issues = adjudication.get("issues")
    if isinstance(issues, list) and issues:
        for issue in issues:
            code = issue.get("code", "unknown") if isinstance(issue, dict) else "unknown"
            detail = issue.get("detail", "") if isinstance(issue, dict) else str(issue)
            lines.append(f"- `{code}`: {detail}")
    else:
        lines.append("- none")
    inputs = adjudication.get("input_sha256")
    if isinstance(inputs, dict):
        lines.extend(["", "## Inputs", ""])
        for name, digest in sorted(inputs.items()):
            lines.append(f"- `{name}` `{digest}`")
    lines.extend(["", "## Verified Artifacts", ""])
    artifacts = adjudication.get("verified_artifacts")
    if isinstance(artifacts, dict):
        for group, entries in artifacts.items():
            if not isinstance(entries, list):
                continue
            lines.append(f"- `{group}`: {len(entries)}")
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                role = entry.get("role", "<unknown>")
                path = entry.get("path", "<unknown>")
                digest = entry.get("sha256", "<unknown>")
                lines.append(f"  - `{role}` `{path}` `{digest}`")
                payload_digest = entry.get("payload_sha256")
                if isinstance(payload_digest, str):
                    lines.append(f"    - payload `{payload_digest}`")
                sources = entry.get("source_artifact_sha256_by_role")
                if isinstance(sources, Mapping):
                    for source_role, source_digest in sorted(sources.items()):
                        lines.append(f"    - source `{source_role}` `{source_digest}`")
    return "\n".join(lines) + "\n"
