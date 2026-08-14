# RTL Boundary Benchmark Adjudication

The benchmark adjudicator consumes evidence that an external runner or CI job
has already generated. It does not compile a DUT, run RTL simulation, search or
replay a failure-triggering sequence, or generate runner commands.

## Interface

```python
from verilator_model_sidecar import (
    adjudicate_boundary_benchmark,
    build_boundary_report_bundle,
    validate_boundary_report_bundle,
)
```

`adjudicate_boundary_benchmark(experiment_contract, evidence_bundle)` is the
single evidence-admission Interface. It returns a machine-readable `pass` or
`fail` adjudication and never treats producer summaries as metric authority.

`build_boundary_report_bundle(adjudication)` deterministically derives the plot
payload, SVG, and Markdown table from a passing adjudication. The bundle records
the adjudication, action-domain, plot, SVG, and Markdown SHA-256 values. The
action-domain identity therefore remains visible in the final machine-readable
payload and Markdown report rather than stopping at adjudication.

`validate_boundary_report_bundle(adjudication, report_bundle)` rebuilds all
three report representations and byte-compares the stored bundle. Editing a
number, graph, source hash, or report sentence therefore invalidates the stored
provenance.

The static CLI entry point reads an already generated Contract and evidence
bundle, writes one authoritative `rtl_boundary_pipeline_result` JSON index, and
only references graph/Markdown artifacts when adjudication passes:

```bash
verilator-model-sidecar adjudicate-boundary-benchmark \
  --experiment-contract contract.json \
  --evidence evidence.json \
  --output boundary-pipeline.json
```

## Experiment Contract

The `rtl_boundary_experiment_contract` surface fixes:

- target, issue, IP, bad/fixed Git revisions;
- checkpoint and oracle identities;
- bad/fixed semantic-manifest hashes and semantic observable names;
- the canonical finite sweep space and its recomputed SHA-256;
- one deterministic action-domain row for every canonical sweep point;
- one shared deterministic reconstructor;
- CPU and GPU executor identities and resident widths;
- every trial's policy, logical requested count, and bad-query budget; and
- selector-comparison and backend-comparison groups.

A selector comparison must contain distinct policies on one GPU backend with
the same logical batch and budget. A backend comparison must contain the same
policy on one CPU and one GPU backend. All trials must belong to at least one
comparison.

## External Evidence

The `rtl_boundary_evidence_bundle` surface contains:

- runner completion identity;
- structured bad/fixed semantic manifests;
- one complete paired bad/fixed observation for every canonical sweep point;
- exact CPU/GPU semantic projections for every point and revision;
- raw selector epochs and bad-revision observations;
- fixed-revision confirmations for every selected failing point;
- one execution record per scheduled state evaluation; and
- physical launch records with executor identity, resident width, execution
  membership, and monotonic offsets.

The adjudicator verifies exact JSON types as well as values. In particular,
boolean `true` cannot substitute for integer `1`. The semantic projection keys
must equal the Contract's observables plus its separate oracle field. Bad and
fixed manifests must preserve identical semantic IDs and widths while binding
their distinct revisions.

The complete ground truth must contain at least one bad-revision failure and no
fixed-revision failure. Boundary edges, failure components, minimal failing
points, and bad-to-fixed disappearance are recomputed by the pure boundary
model.

## Ground-truth isolation

Selectors receive only the canonical sweep and completed public bad-revision
feedback. They do not receive the complete ground truth, fixed observations,
backend kind, resident width, launch timing, or wall time.

All policies use the same `nearest_observed_graph` version 1 reconstructor.
Producer-supplied total predictions are optional audit snapshots and must equal
the sidecar reconstruction. Consequently, a producer cannot report perfect
boundary recall by copying unqueried labels from ground truth.

## Recomputed accounting

For every trial the adjudicator derives:

- logical, unique, and duplicate bad queries;
- scheduled, unique, and duplicate `(revision, point)` state evaluations;
- bad-search and fixed-confirmation evaluations;
- cycle evaluations;
- physical batch launches; and
- trial wall time from the declared monotonic-offset scope.

Launch membership must partition the execution records exactly. A launch may
not exceed the backend's resident width or mix feedback epochs, revisions, or
purposes. This preserves the distinction between a selector's logical batch
and an executor's physical chunking.

The sidecar then recomputes first violation, first bracket, first exact
boundary, boundary edge precision/recall, ordinal-grid Hausdorff-like distance,
failure-region IoU, and minimal-point recovery. CPU/GPU backend comparison is
accepted only when the complete selection, observation, and confirmation trace
is identical.

## Public schemas

- `contracts/rtl_boundary_experiment_contract.schema.json`
- `contracts/rtl_boundary_semantic_manifest.schema.json`
- `contracts/rtl_boundary_evidence_bundle.schema.json`
- `contracts/rtl_boundary_adjudication.schema.json`
- `contracts/rtl_boundary_plot_payload.schema.json`
- `contracts/rtl_boundary_pipeline_result.schema.json`
- `contracts/rtl_boundary_report_bundle.schema.json`
- `contracts/rtl_boundary_report_validation.schema.json`

These extend the finite-grid schemas documented in
[`rtl-boundary-model.md`](rtl-boundary-model.md).

## Current evidence status

The Module and its hermetic evidence fixtures prove the Contract mechanics.
They are not OpenTitan runtime evidence. Completion of the TL-UL #10818
benchmark still requires the external runner or CI to produce the pinned full
grid, semantic manifests, raw trials, executions, launches, and timings.

The current work-item state, candidate sweep axes, external blockers, rejected
claims, and safeguard false-positive diagnostic track are maintained in the
[`TL-UL #10818 boundary benchmark work ledger`](tlul10818-boundary-work-ledger.md).
