# Verilator Model Sidecar

`verilator-model-sidecar` is a small, backend-neutral experiment for recovering
machine-readable simulation metadata from an unmodified Verilator release.
The first supported producer is Verilator 5.050.

The project deliberately starts outside the Verilator source tree. It combines
Verilator's parser/elaboration JSON with measured generated C++ layout, while
keeping the JSON tree—not generated C++ names—as the source of semantic
identities. An experimental Verilator fork supplies compiler-owned storage,
checkpoint-membership, toggle-lowering, and final-AST eval metadata where
released output is insufficient.

## Current contract

The implemented semantic tracer bullet:

1. either captures a small model with `verilator --json-only` plus
   `verilator --cc`, or analyzes existing JSON/meta/`obj_dir` artifacts without
   invoking another process;
2. checks Verilator 5.050 on the legacy path, or exact producer agreement between
   native artifacts on the fork path;
3. emits a deterministic, versioned `model_manifest.json`;
4. reconstructs the elaborated instance hierarchy through `CELL.modp`, retaining
   named generate/block scopes;
5. extracts module-definition variables, widths, source identities, and a
   conservative semantic lifecycle classification; and
6. optionally resolves adapter signal bindings to unique semantic entities and
   checks their widths;
7. measures generated C++ state with a separate `sizeof`/`offsetof` probe; and
8. joins each measured member to its semantic ID and optionally verifies a
   pinned physical-layout oracle; and
9. optionally joins JSON `COVERTOGGLEDECL` semantics to native compiler-owned
   insertion/update regions, expanding bit/direction identities and preserving
   every Verilator counter alias explicitly without parsing generated C++; and
10. independently validates compiler-owned final-AST eval metadata and
    classifies explicit downstream LLVM eval closures as
    `proven_device_clean`, `host_dependent`, or `unknown`, with transitive,
    fail-closed propagation and optional oracle verification.

The experimental Verilator fork can additionally emit definition fields plus
instance-to-symbol-table bindings. `verify-native` consumes that output directly
and verifies adapter signals without reading Verilator JSON or parsing generated
C++ headers:

```bash
verilator-model-sidecar verify-native \
  --manifest /path/to/model-manifest.json \
  --adapter contracts/opentitan_uart_semantic_signals.json \
  --output /tmp/opentitan-uart-native-verification.json
```

The same native manifest can authorize a compiled ABI measurement without
parsing generated headers for member types:

```bash
verilator-model-sidecar probe-layout \
  --obj-dir /path/to/obj_dir \
  --adapter contracts/opentitan_uart_semantic_signals.json \
  --native-manifest /path/to/model-manifest.json \
  --producer "$(verilator --version)" \
  --output /tmp/opentitan-uart-native-layout.json
```

This native path verifies names, hierarchy, generated member bindings, widths,
and compiler-measured offsets for the exact generated model. The same manifest
can authorize toggle storage measurement and semantic-to-physical alias mapping.
The offsets are not a stable cross-version ABI. The manifest now also provides
definition-level checkpoint membership and final-AST eval calls, state accesses,
effects, and fixed-point classifications. Pointer-free checkpoint packing and
eval schedule/convergence semantics remain explicitly `not_provided`.

When the native manifest contains compiler-owned `--savable` membership,
definition fields can be expanded onto the stored instances without parsing
generated serializer code:

```bash
verilator-model-sidecar project-native-checkpoint \
  --manifest /path/to/model-manifest.json \
  --output /tmp/native-checkpoint-projection.json
```

This projection is intentionally limited to field occurrence membership. It
does not claim runtime-context completeness, serialization order, byte packing,
pointer freedom, or coverage/timing compatibility. Included storage kinds that
cannot be projected are reported with `status=incomplete` and a failing exit
status.

Physical bindings fail closed as `not_analyzed` unless an explicit measured
layout is supplied. Coverage mapping likewise fails closed unless an explicit
native manifest, coverage contract, and layout containing its measured array are
supplied. Checkpoint runtime state and packing remain `not_provided`. Eval
effects remain `not_analyzed` unless an explicit observation is supplied. For a
native host region, the sidecar independently recomputes the compiler manifest's
call-closure fixed point; a downstream device region still requires explicit
LLVM IR. A clean classification proves only that declared closure under its
effect policy. The current manifest is not a pointer-free state ABI,
arbitrary-input memory-safety proof, CUDA backend, or stable upstream Verilator
API.

OpenTitan regression-discovery evidence is adjudicated only from externally
generated bundles. The sidecar validates target identity, bad/fixed revisions,
semantic CPU/GPU equality, seed-corpus separation, and artifact provenance, but
does not compile DUTs, run RTL simulation, search/replay failure sequences, or
emit runner commands. Passing JSON and Markdown reports retain the verified
revision, checkpoint, oracle, action-domain, and structured semantic-manifest
identities. Oracle-violation seeds are checked for single-action-deletion
1-minimality rather than accepted from a `minimal` flag alone. See
[OpenTitan external evidence adjudication](docs/opentitan-external-evidence-adjudication.md).

Known-regression parameter spaces can be modeled independently with the pure
[RTL boundary model](docs/rtl-boundary-model.md). It canonicalizes finite
ordered/categorical axes, gives external runners deterministic point IDs, and
recomputes bad/fixed boundary edges, failure regions, and applicable minimal
failing points from complete raw observations without invoking a DUT or backend.
It also provides deterministic selector replay for random, stratified,
ordered-refinement, and novelty/boundary-guided policy trials, separating
selector efficiency from CPU/GPU execution throughput.

Externally executed finite-grid campaigns can be admitted through the
[RTL boundary benchmark adjudicator](docs/rtl-boundary-benchmark.md). It checks
CPU/GPU semantic equality, bad-to-fixed disappearance, selector replay,
state-eval and launch accounting, and CPU/GPU backend trace identity, then
generates hash-bound JSON, SVG, and Markdown representations without invoking a
DUT.

## Requirements

- Python 3.10 or newer
- Verilator 5.050 for the end-to-end capture smoke test and generated headers
- The experimental Verilator manifest fork for native storage, checkpoint,
  coverage, and eval metadata
- A C++20 compiler for physical-layout measurement

There are no Python runtime dependencies outside the standard library.

## Setup and test

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

The unit tests do not require Verilator. The capture/analyze equivalence test
runs only when a Verilator 5.050 executable is available on `PATH`.

## Capture the tiny fixture

```bash
verilator-model-sidecar capture \
  --source-root . \
  --top tiny \
  --source tests/fixtures/tiny/tiny.sv \
  --work-dir /tmp/verilator-model-sidecar-tiny \
  --output /tmp/tiny-model-manifest.json

verilator-model-sidecar validate /tmp/tiny-model-manifest.json
```

## Analyze existing artifacts

`analyze` consumes existing artifacts and does not run Verilator, a shell, a
compiler, or a build tool. Physical layout is measured separately so that an
ABI observation is an explicit input rather than a hidden analysis side effect.
The producer string is explicit because Verilator's JSON metadata does not carry
the producer version.

```bash
native_manifest=/path/to/model-manifest.json
producer="$(jq -r .producer "$native_manifest")"

verilator-model-sidecar classify-effects \
  --contract contracts/opentitan_uart_eval_effects.json \
  --native-manifest "verilator_native_manifest=$native_manifest" \
  --ir gpu_eval_slice_ir=/path/to/entry_slices/vl_eval_batch_gpu.slice.ll \
  --producer "$producer" \
  --oracle contracts/opentitan_uart_eval_effects_oracle.json \
  --output /tmp/opentitan-uart-eval-effects.json

verilator-model-sidecar probe-layout \
  --obj-dir /path/to/obj_dir \
  --adapter contracts/opentitan_uart_semantic_signals.json \
  --coverage-contract contracts/opentitan_uart_toggle_coverage.json \
  --native-manifest "$native_manifest" \
  --producer "$producer" \
  --output /tmp/opentitan-uart-layout.json

verilator-model-sidecar analyze \
  --source-root /path/to/source-root \
  --top chip_sim_tb \
  --tree /path/to/Vsim.tree.json \
  --meta /path/to/Vsim.tree.meta.json \
  --obj-dir /path/to/obj_dir \
  --producer "$producer" \
  --adapter contracts/opentitan_uart_semantic_signals.json \
  --layout /tmp/opentitan-uart-layout.json \
  --coverage-contract contracts/opentitan_uart_toggle_coverage.json \
  --native-manifest "$native_manifest" \
  --effects /tmp/opentitan-uart-eval-effects.json \
  --output /tmp/opentitan-uart-model-manifest.json
```

`classify-effects` reads existing native JSON and textual LLVM IR and invokes no
external process. It exits nonzero when an expected classification or pinned
oracle value differs. `analyze` exits nonzero if any declared semantic signal is
unresolved, ambiguous, or has a different width, or if a physical observation
differs from its oracle. It also exits nonzero if coverage storage, counts,
mappings, or fingerprints differ from the coverage oracle. Adapter
`drive`/`observe` directions remain contract-owned annotations: the JSON AST
does not independently derive them for internal testbench variables.

No absolute build path or timestamp is written to the manifest. Verilator's
non-deterministic pointer table from `.tree.meta.json` is intentionally excluded
from the manifest fingerprint.

## Manifest boundary

```text
model_manifest
├── provenance
├── semantic_projection
│   ├── top definitions       implemented
│   └── instance hierarchy    implemented
├── adapter_verification      optional, semantic names and widths
├── physical_bindings         implemented, measured generated C++ ABI
├── checkpoint_projection     separate native field-occurrence report
├── coverage_mapping          implemented, AST ↔ native lowering ↔ words
└── eval_effects              native host closure + explicit downstream LLVM
```

The OpenTitan UART semantic Contract is measured: all 13 declared signals resolve
uniquely with matching widths through a fully resolved 39,379-instance hierarchy.
The pinned 5.050 physical Contract measures a 2,340,480-byte Syms image. The
matching current fork model measures 2,337,664 bytes with the same 192-byte root
offset; this expected cross-version difference is rejected by the old physical
oracle. Its compiler manifest contains 43,422 field definitions and seven stored
instances. Native checkpoint projection expands those definitions into 43,417
stored field occurrences while leaving runtime state and packing
`not_provided`. The coverage Contract maps 691 AST declarations through 2,764
native lowering declarations and 2,541 update sites into 16,160 directional
semantic observations and all 7,842 physical words. Of those physical words,
4,858 explicitly aggregate aliases. The native path reproduces every existing
golden metric and fingerprint with no generated C++ coverage parsing. See the
[semantic evidence](docs/opentitan-uart-semantic-evidence.md),
[physical evidence](docs/opentitan-uart-physical-evidence.md), and
[coverage evidence](docs/opentitan-uart-coverage-evidence.md). Eval-effect
classification now obtains the host closure from compiler-owned final-AST
metadata instead of host generated C++/LLVM reverse engineering. It separates a
290-function host-dependent main-eval closure from the exact 289-function
device-clean GPU entry closure, then revalidates a width-1 eval from the same
fork at 13/13 semantic signals and 7,842/7,842 coverage words. See the
[eval-effect evidence](docs/opentitan-uart-eval-effect-evidence.md).

The same schema and command surfaces also close on a temporal VeeR AXI
LSU/DMA bridge and an OpenTitan UART TL-UL stall profile without target-specific
Python branches. They verify 17/17 and 27/27 semantic/physical bindings, map all
9,462 and 5,024 native toggle words, reproduce both independent canonical
manifests, and separate each host-dependent eval from an explicitly clean GPU
closure. The original minimum ABI proposal, its fork implementation status, and
the remaining unprovided semantics are recorded in the
[target-independent evidence and minimum ABI proposal](docs/target-independent-validation-and-upstream-abi.md).

## Repository hygiene

Only source, the small RTL fixture, and tests belong in Git. Verilator object
directories, generated manifests, reports, and experiment artifacts are ignored.
After review, a new GitHub repository can be populated with the normal sequence:

```bash
git add .
git commit -m "Initial Verilator model sidecar"
git remote add origin <repository-url>
git push -u origin main
```
