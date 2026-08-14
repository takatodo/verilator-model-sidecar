"""Deterministic reports for a passing RTL boundary benchmark adjudication."""

from __future__ import annotations

import hashlib
import html
import json
from typing import Any, Mapping

from .boundary_benchmark import (
    RTL_BOUNDARY_ADJUDICATION_SURFACE,
    RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
    BoundaryBenchmarkError,
)


RTL_BOUNDARY_PLOT_PAYLOAD_SURFACE = "rtl_boundary_plot_payload"
RTL_BOUNDARY_PIPELINE_RESULT_SURFACE = "rtl_boundary_pipeline_result"
RTL_BOUNDARY_REPORT_BUNDLE_SURFACE = "rtl_boundary_report_bundle"
RTL_BOUNDARY_REPORT_VALIDATION_SURFACE = "rtl_boundary_report_validation"


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
        raise BoundaryBenchmarkError(
            "report_canonical_json_invalid", "report source is not canonical JSON"
        ) from error


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _require_passing_adjudication(adjudication: Mapping[str, Any]) -> None:
    if not isinstance(adjudication, Mapping):
        raise BoundaryBenchmarkError(
            "report_adjudication_invalid", "adjudication must be an object"
        )
    schema_version = adjudication.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION
    ):
        raise BoundaryBenchmarkError(
            "report_adjudication_invalid", "adjudication schema_version is invalid"
        )
    if adjudication.get("surface") != RTL_BOUNDARY_ADJUDICATION_SURFACE:
        raise BoundaryBenchmarkError(
            "report_adjudication_invalid", "adjudication surface is invalid"
        )
    if adjudication.get("status") != "pass" or adjudication.get("issues") != []:
        raise BoundaryBenchmarkError(
            "report_adjudication_not_pass", "only a passing adjudication can be reported"
        )
    required = {
        "verified_identity",
        "ground_truth_analysis",
        "trial_results",
        "selector_comparisons",
        "backend_comparisons",
    }
    if not required.issubset(adjudication):
        raise BoundaryBenchmarkError(
            "report_adjudication_incomplete", "passing adjudication is incomplete"
        )


def _milestone_queries(milestone: Any) -> int | None:
    if not isinstance(milestone, Mapping):
        raise BoundaryBenchmarkError(
            "report_milestone_invalid", "comparison milestone must be an object"
        )
    status = milestone.get("status")
    if status == "reached":
        value = milestone.get("logical_bad_queries")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BoundaryBenchmarkError(
                "report_milestone_invalid", "reached milestone query count is invalid"
            )
        return value
    if status not in {"not_reached", "not_applicable"}:
        raise BoundaryBenchmarkError(
            "report_milestone_invalid", "comparison milestone status is invalid"
        )
    return None


def _ratio_projection(metric: Any) -> dict[str, Any]:
    if not isinstance(metric, Mapping):
        raise BoundaryBenchmarkError(
            "report_metric_invalid", "ratio metric must be an object"
        )
    projected = {"status": metric.get("status")}
    for field in ("reason", "numerator", "denominator", "value"):
        if field in metric:
            projected[field] = metric[field]
    return projected


def _distance_projection(metric: Any) -> dict[str, Any]:
    if not isinstance(metric, Mapping):
        raise BoundaryBenchmarkError(
            "report_metric_invalid", "distance metric must be an object"
        )
    projected = {"status": metric.get("status")}
    for field in ("reason", "ordinal_distance"):
        if field in metric:
            projected[field] = metric[field]
    return projected


def _plot_row(
    comparison_id: str,
    row: Mapping[str, Any],
    *,
    comparison_kind: str,
) -> dict[str, Any]:
    accounting = row.get("accounting")
    metrics = row.get("final_prediction_metrics")
    backend = row.get("backend")
    policy = row.get("policy")
    if not all(isinstance(value, Mapping) for value in (accounting, metrics, backend, policy)):
        raise BoundaryBenchmarkError(
            "report_comparison_row_invalid", "comparison row is incomplete"
        )
    if metrics.get("status") != "computed":
        raise BoundaryBenchmarkError(
            "report_comparison_metric_unavailable",
            "final prediction metrics must be computed",
        )
    integer_fields = {
        "scheduled_state_evals",
        "unique_state_evals",
        "duplicate_state_evals",
        "bad_search_state_evals",
        "fixed_confirmation_state_evals",
        "cycle_evals",
        "batch_launches",
        "wall_time_ns",
    }
    if not integer_fields.issubset(accounting):
        raise BoundaryBenchmarkError(
            "report_accounting_incomplete", "comparison accounting is incomplete"
        )
    if any(
        not isinstance(accounting[field], int)
        or isinstance(accounting[field], bool)
        or accounting[field] < 0
        for field in integer_fields
    ):
        raise BoundaryBenchmarkError(
            "report_accounting_invalid", "comparison accounting must be nonnegative integers"
        )
    resident_width = backend.get("resident_width")
    if (
        not isinstance(resident_width, int)
        or isinstance(resident_width, bool)
        or resident_width <= 0
    ):
        raise BoundaryBenchmarkError(
            "report_backend_invalid", "comparison backend resident width is invalid"
        )
    return {
        "comparison_id": comparison_id,
        "comparison_kind": comparison_kind,
        "trial_id": row.get("trial_id"),
        "policy_kind": policy.get("kind"),
        "backend_id": backend.get("backend_id"),
        "backend_kind": backend.get("kind"),
        "resident_width": resident_width,
        "logical_bad_queries": accounting["bad_search_state_evals"],
        "accounting": {field: accounting[field] for field in sorted(integer_fields)},
        "first_violation_logical_bad_queries": _milestone_queries(
            row.get("first_violation")
        ),
        "first_bracket_logical_bad_queries": _milestone_queries(
            row.get("first_bracket")
        ),
        "first_exact_boundary_logical_bad_queries": _milestone_queries(
            row.get("first_exact_boundary")
        ),
        "boundary_precision": _ratio_projection(metrics.get("boundary_precision")),
        "boundary_recall": _ratio_projection(metrics.get("boundary_recall")),
        "boundary_hausdorff": _distance_projection(
            metrics.get("boundary_hausdorff")
        ),
        "failure_region_iou": _ratio_projection(metrics.get("failure_region_iou")),
        "minimal_failing_point_recovery": _ratio_projection(
            metrics.get("minimal_failing_point_recovery")
        ),
    }


def _build_plot_payload(adjudication: Mapping[str, Any]) -> dict[str, Any]:
    source_sha256 = _sha256(adjudication)
    verified_identity = adjudication["verified_identity"]
    action_domain_sha256 = verified_identity.get("action_domain_sha256")
    if (
        not isinstance(action_domain_sha256, str)
        or len(action_domain_sha256) != 64
        or any(character not in "0123456789abcdef" for character in action_domain_sha256)
    ):
        raise BoundaryBenchmarkError(
            "report_action_domain_identity_invalid",
            "passing adjudication action-domain SHA-256 is invalid",
        )
    selector_rows: list[dict[str, Any]] = []
    backend_rows: list[dict[str, Any]] = []
    for comparison in adjudication["selector_comparisons"]:
        comparison_id = comparison["comparison_id"]
        selector_rows.extend(
            _plot_row(comparison_id, row, comparison_kind="selector")
            for row in comparison["rows"]
        )
    for comparison in adjudication["backend_comparisons"]:
        comparison_id = comparison["comparison_id"]
        backend_rows.extend(
            _plot_row(comparison_id, row, comparison_kind="backend")
            for row in comparison["rows"]
        )
    if not selector_rows or not backend_rows:
        raise BoundaryBenchmarkError(
            "report_comparisons_incomplete",
            "selector and backend comparison rows are required",
        )
    return {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_PLOT_PAYLOAD_SURFACE,
        "source_adjudication_sha256": source_sha256,
        "experiment_id": verified_identity["experiment_id"],
        "target_id": verified_identity["target"]["target_id"],
        "sweep_space_sha256": verified_identity["sweep_space_sha256"],
        "action_domain_sha256": action_domain_sha256,
        "point_count": verified_identity["point_count"],
        "selector_rows": selector_rows,
        "backend_rows": backend_rows,
    }


def _format_value(metric: Mapping[str, Any]) -> str:
    if metric.get("status") == "computed":
        if "numerator" in metric and "denominator" in metric:
            return f"{metric['numerator']}/{metric['denominator']}"
        if "ordinal_distance" in metric:
            return str(metric["ordinal_distance"])
    return str(metric.get("status"))


def _format_milestone(value: int | None) -> str:
    return "—" if value is None else str(value)


def _format_markdown(payload: Mapping[str, Any], payload_sha256: str) -> str:
    lines = [
        f"# RTL boundary benchmark: {payload['experiment_id']}",
        "",
        f"- Target: `{payload['target_id']}`",
        f"- Sweep points: `{payload['point_count']}`",
        f"- Sweep-space SHA-256: `{payload['sweep_space_sha256']}`",
        f"- Action-domain SHA-256: `{payload['action_domain_sha256']}`",
        f"- Source adjudication SHA-256: `{payload['source_adjudication_sha256']}`",
        f"- Plot payload SHA-256: `{payload_sha256}`",
        "",
        "## Selector comparison on one GPU executor",
        "",
        "| Trial | Policy | State evals | Launches | Wall ns | First violation | First bracket | Exact boundary | Edge P | Edge R | Hausdorff | Failure IoU | Minimal recovery |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["selector_rows"]:
        accounting = row["accounting"]
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["trial_id"]),
                    str(row["policy_kind"]),
                    str(accounting["scheduled_state_evals"]),
                    str(accounting["batch_launches"]),
                    str(accounting["wall_time_ns"]),
                    _format_milestone(row["first_violation_logical_bad_queries"]),
                    _format_milestone(row["first_bracket_logical_bad_queries"]),
                    _format_milestone(row["first_exact_boundary_logical_bad_queries"]),
                    _format_value(row["boundary_precision"]),
                    _format_value(row["boundary_recall"]),
                    _format_value(row["boundary_hausdorff"]),
                    _format_value(row["failure_region_iou"]),
                    _format_value(row["minimal_failing_point_recovery"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Same selector across CPU and GPU backends",
            "",
            "| Trial | Backend | Resident width | State evals | Cycle evals | Launches | Wall ns |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["backend_rows"]:
        accounting = row["accounting"]
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["trial_id"]),
                    str(row["backend_kind"]),
                    str(row["resident_width"]),
                    str(accounting["scheduled_state_evals"]),
                    str(accounting["cycle_evals"]),
                    str(accounting["batch_launches"]),
                    str(accounting["wall_time_ns"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            "The selector table compares sampling policies on one declared GPU executor. The backend table compares an identical replayed selector trace across CPU and GPU adapters. Coverage or boundary gain identifies an interesting prefix; only the declared oracle determines failure. This report does not claim unknown-bug discovery, PPO/RL superiority, or that GPU execution improves the sampling algorithm itself.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_svg(payload: Mapping[str, Any], payload_sha256: str) -> str:
    rows = payload["selector_rows"]
    width = 1120
    row_height = 38
    height = 118 + len(rows) * row_height
    query_max = max(row["logical_bad_queries"] for row in rows) or 1
    wall_max = max(row["accounting"]["wall_time_ns"] for row in rows) or 1
    query_x = 300
    wall_x = 690
    bar_width = 300
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f"<metadata id=\"rtl-boundary-plot-payload-sha256\">{payload_sha256}</metadata>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="24" y="30" font-family="sans-serif" font-size="20" font-weight="bold">RTL boundary selector comparison</text>',
        f'<text x="24" y="52" font-family="monospace" font-size="11">source {html.escape(payload["source_adjudication_sha256"])}</text>',
        f'<text x="{query_x}" y="82" font-family="sans-serif" font-size="13">logical bad queries</text>',
        f'<text x="{wall_x}" y="82" font-family="sans-serif" font-size="13">wall time (ns)</text>',
    ]
    for index, row in enumerate(rows):
        y = 104 + index * row_height
        query_width = bar_width * row["logical_bad_queries"] / query_max
        wall_width = bar_width * row["accounting"]["wall_time_ns"] / wall_max
        label = html.escape(f"{row['policy_kind']} / {row['trial_id']}")
        parts.extend(
            [
                f'<text x="24" y="{y + 14}" font-family="sans-serif" font-size="12">{label}</text>',
                f'<rect x="{query_x}" y="{y}" width="{query_width:.3f}" height="18" fill="#2563eb"/>',
                f'<text x="{query_x + query_width + 6:.3f}" y="{y + 14}" font-family="monospace" font-size="11">{row["logical_bad_queries"]}</text>',
                f'<rect x="{wall_x}" y="{y}" width="{wall_width:.3f}" height="18" fill="#059669"/>',
                f'<text x="{wall_x + wall_width + 6:.3f}" y="{y + 14}" font-family="monospace" font-size="11">{row["accounting"]["wall_time_ns"]}</text>',
            ]
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def build_boundary_report_bundle(adjudication: Mapping[str, Any]) -> dict[str, Any]:
    """Build deterministic plot, SVG, Markdown, and their provenance hashes."""

    _require_passing_adjudication(adjudication)
    plot_payload = _build_plot_payload(adjudication)
    plot_payload_sha256 = _sha256(plot_payload)
    graph_svg = _render_svg(plot_payload, plot_payload_sha256)
    markdown_report = _format_markdown(plot_payload, plot_payload_sha256)
    return {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_REPORT_BUNDLE_SURFACE,
        "source_adjudication_sha256": plot_payload["source_adjudication_sha256"],
        "plot_payload": plot_payload,
        "plot_payload_sha256": plot_payload_sha256,
        "graph_svg": graph_svg,
        "graph_sha256": _sha256_bytes(graph_svg.encode("utf-8")),
        "markdown_report": markdown_report,
        "markdown_sha256": _sha256_bytes(markdown_report.encode("utf-8")),
    }


def validate_boundary_report_bundle(
    adjudication: Mapping[str, Any], report_bundle: Mapping[str, Any]
) -> dict[str, Any]:
    """Rebuild and byte-compare a stored report bundle."""

    adjudication_sha256: str | None = None
    report_bundle_sha256: str | None = None
    try:
        adjudication_sha256 = _sha256(adjudication)
        report_bundle_sha256 = _sha256(report_bundle)
        expected = build_boundary_report_bundle(adjudication)
        if not isinstance(report_bundle, Mapping) or not _canonical_bytes(
            report_bundle
        ) == _canonical_bytes(expected):
            raise BoundaryBenchmarkError(
                "report_bundle_mismatch",
                "stored graph/report bundle does not reproduce from adjudication",
            )
        status = "pass"
        issues: list[dict[str, str]] = []
    except BoundaryBenchmarkError as error:
        status = "fail"
        issues = [{"code": error.code, "message": str(error)}]
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        status = "fail"
        issues = [
            {
                "code": "report_bundle_invalid",
                "message": f"stored report bundle is structurally invalid: {error}",
            }
        ]
    return {
        "schema_version": RTL_BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "surface": RTL_BOUNDARY_REPORT_VALIDATION_SURFACE,
        "status": status,
        "issues": issues,
        "input_canonical_sha256": {
            "adjudication": adjudication_sha256,
            "report_bundle": report_bundle_sha256,
        },
    }
