# RTL Boundary Model

The boundary model is a pure finite-grid Module. It does not compile a DUT,
run RTL simulation, select trials, or invoke an execution backend. An external
runner uses the same Module to enumerate point identities before execution; the
sidecar later uses it to recompute boundary topology from complete bad/fixed raw
oracle observations.

## Interface

```python
from verilator_model_sidecar import (
    analyze_boundary_ground_truth,
    analyze_boundary_policy_trial,
    enumerate_sweep_space,
    reconstruct_boundary_prediction,
    select_boundary_points,
)
```

`enumerate_sweep_space(sweep_space)` validates and canonicalizes the axes, then
returns every Cartesian-product point exactly once. Axis declarations are
canonicalized by name. Categorical value and adjacency-pair declaration order
does not affect identity; ordered value order is semantic.

`analyze_boundary_ground_truth(sweep_space, ground_truth)` requires one paired
bad/fixed observation for every enumerated point. It rejects missing, duplicate,
unknown, or point-ID-mismatched observations and derives all boundary results
instead of trusting producer summaries.

`select_boundary_points(sweep_space, policy_spec, completed_public_batches,
requested_count)` is the deterministic selector interface used by external
runners. It sees only prior bad-revision observations and coverage feature IDs.
It does not accept complete ground truth, fixed-revision observations, backend
identity, resident width, or wall-clock timing.

The same selector interface is available as a CLI for external CI harnesses:

```text
verilator-model-sidecar select-boundary-points \
  --sweep-space sweep_space.json \
  --policy policy.json \
  --completed-public-batches completed_batches.json \
  --requested-count N \
  --output selected_points.json
```

`--completed-public-batches` is optional and defaults to an empty feedback list.
The output is `rtl_boundary_selector_response` with only `selected_point_ids`;
its public schema is `contracts/rtl_boundary_selector_response.schema.json`.
Selector CLI JSON inputs are parsed fail-closed: duplicate object keys and
non-finite tokens such as `NaN` or `Infinity` are rejected before selection.

`reconstruct_boundary_prediction(sweep_space, reconstructor_spec,
completed_public_batches)` applies the shared `nearest_observed_graph` version 1
reconstructor. Each unobserved point inherits the unanimous label of its nearest
observed graph neighbors. A pass/fail distance tie, or a disconnected slice with
no observation, resolves conservatively to pass. It has no ground-truth input.

`analyze_boundary_policy_trial(sweep_space, ground_truth, trial)` replays the
recorded selector trial from public feedback and rejects any selection or bad
oracle observation that is not reproducible. It then recomputes discovery
milestones and prediction metrics from the fixed ground truth and the trial's
raw epochs. Producer-supplied total predictions are optional audit snapshots;
when present they must exactly equal the sidecar reconstruction.

## Axes and adjacency

An ordered axis gets edges only between adjacent ordinal values. A categorical
axis must explicitly declare `adjacent_value_pairs`; an empty list intentionally
separates its categorical slices.

```json
{
  "schema_version": 1,
  "surface": "rtl_boundary_sweep_space",
  "axes": [
    {
      "name": "stall_cycles",
      "kind": "ordered",
      "values": [0, 1, 2, 4]
    },
    {
      "name": "request_integrity",
      "kind": "categorical",
      "values": ["valid", "malformed"],
      "adjacent_value_pairs": [["valid", "malformed"]]
    }
  ]
}
```

Axis values are finite JSON strings, integers, or booleans. Floating-point
timing quantities must be encoded in an explicit integral unit such as cycles
or picoseconds. No arbitrary point-count ceiling is imposed.

## Derived topology

For each revision, the analysis derives:

- exact pass and fail point sets;
- oriented pass-to-fail boundary edges;
- connected components of the failure-induced point graph; and
- Pareto-minimal failing points within each categorical stratum when at least
  one ordered axis exists.

Minimal failing points are `not_applicable` when no ordered axis exists. The
analysis also derives disappeared, persistent, and introduced failures across
the bad-to-fixed revision transition.

The model does not assert that failure is monotonic. Binary refinement is a
separate selector and may only use axes for which its own benchmark Contract can
prove the required ordering assumptions.

## Selector trials

Policy specs use a closed configuration per policy kind:

- `random`: no configuration
- `stratified`: `strata_axes`
- `ordered_refinement`: one ordered `axis`
- `novelty_boundary_guided`: no configuration

All ordering is derived from SHA-256 ranks over the policy identity, seed, sweep
identity, and point identity. Python RNG state is not part of the Contract.

Trial feedback is epoch-based. If a GPU batch proposes many points at once, the
sidecar charges discovery milestones at the end of that epoch because the
selector could not observe earlier results inside the same batch. Backend
resident width and physical launch count are therefore throughput evidence, not
selector-efficiency evidence.

Trials declare `budget_logical_bad_queries`. The replay rejects a trace that
exceeds that budget, or stops while budget remains and the selector can still
produce another point. A shorter prefix can still be analyzed by setting its
budget to the prefix's own logical query count.

Prediction metrics always use the sidecar's common total reconstruction after
each epoch. This keeps selector comparisons attributable to different sampled
prefixes instead of allowing each policy producer to supply its own prediction.

`first_violation` and `first_bracket` are charged at the end of the feedback
epoch, not at a point's position inside a physical batch. A bracket is an
observed pass/fail pair that differs on exactly one axis. Ordered endpoints may
be non-adjacent; categorical endpoints must be an explicitly declared adjacent
pair.

For a total prediction, the Module derives the predicted failure set and
oriented boundary-edge set using the same topology as the ground truth. It then
computes:

- boundary precision and recall from exact oriented-edge intersection;
- failure-region IoU from the true and predicted failure point sets;
- minimal-point recovery from the intersection of true and independently
  recomputed predicted Pareto minima; and
- an ordinal Hausdorff-like boundary distance. Point distance is the sum of
  ordered index distance and categorical adjacency-graph distance. Edge
  distance is the maximum distance between corresponding pass endpoints and
  corresponding fail endpoints, including when the two edges lie on different
  axes.

Ratios include their integer numerator and denominator. A missing true or
predicted set is not assigned an invented perfect score: boundary ratios and
failure IoU use `not_applicable` when both relevant sets are empty; a one-sided
empty boundary makes the Hausdorff-like distance `unbounded`; disconnected
categorical topology also makes it `unbounded`. `first_exact_boundary` is
`not_applicable` when the true boundary is empty.

## Public schemas

- `contracts/rtl_boundary_sweep_space.schema.json`
- `contracts/rtl_boundary_sweep_enumeration.schema.json`
- `contracts/rtl_boundary_selector_response.schema.json`
- `contracts/rtl_boundary_ground_truth.schema.json`
- `contracts/rtl_boundary_analysis.schema.json`
- `contracts/rtl_boundary_policy_trial.schema.json`
- `contracts/rtl_boundary_policy_analysis.schema.json`

The schemas define structural surfaces. The Python Module additionally proves
Cartesian completeness, canonical identities, parameter membership, exact JSON
types, edge topology, connected regions, and minimality.

The current four-action TL-UL #10818 evidence can be adapted to a two-axis,
four-point compatibility fixture. Its existing 4096-state scale result remains
execution-scale evidence for one repeated action, not a 4096-point ground truth.
