# Verilator Model Sidecar

`verilator-model-sidecar` is a small, backend-neutral experiment for recovering
machine-readable simulation metadata from an unmodified Verilator release.
The first supported producer is Verilator 5.050.

The project deliberately starts outside the Verilator source tree. It combines
Verilator's parser/elaboration JSON with generated C++ provenance, while keeping
the JSON tree—not generated C++ names—as the source of semantic identities.

## Current contract

The implemented semantic tracer bullet:

1. either captures a small model with `verilator --json-only` plus
   `verilator --cc`, or analyzes existing JSON/meta/`obj_dir` artifacts without
   invoking another process;
2. checks that the producer is Verilator 5.050;
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
9. optionally joins JSON `COVERTOGGLEDECL` semantics to generated insertion and
   update regions, expanding bit/direction identities and preserving every
   Verilator counter alias explicitly; and
10. independently classifies explicit LLVM eval closures as
    `proven_device_clean`, `host_dependent`, or `unknown`, with transitive,
    fail-closed propagation and optional oracle verification.

Physical bindings fail closed as `not_analyzed` unless an explicit measured
layout is supplied. Coverage mapping likewise fails closed unless an explicit
coverage contract and a layout containing its measured array are supplied.
Checkpoint packing remains `not_analyzed`. Eval effects remain `not_analyzed`
unless an explicit observation is supplied. A clean classification proves only
the pinned direct-call LLVM closure under its declared effect policy; the
current manifest is not a pointer-free state ABI, arbitrary-input memory-safety
proof, CUDA backend, or stable upstream Verilator API.

## Requirements

- Python 3.10 or newer
- Verilator 5.050 for the end-to-end capture smoke test and generated headers
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
verilator-model-sidecar classify-effects \
  --contract contracts/opentitan_uart_eval_effects.json \
  --ir verilator_host_ir=/path/to/obj_dir/merged.ll \
  --ir gpu_eval_slice_ir=/path/to/entry_slices/vl_eval_batch_gpu.slice.ll \
  --producer "Verilator 5.050 2026-07-01 rev v5.050" \
  --oracle contracts/opentitan_uart_eval_effects_oracle.json \
  --output /tmp/opentitan-uart-eval-effects.json

verilator-model-sidecar probe-layout \
  --obj-dir /path/to/obj_dir \
  --adapter contracts/opentitan_uart_semantic_signals.json \
  --coverage-contract contracts/opentitan_uart_toggle_coverage.json \
  --producer "Verilator 5.050 2026-07-01 rev v5.050" \
  --output /tmp/opentitan-uart-layout.json

verilator-model-sidecar analyze \
  --source-root /path/to/source-root \
  --top chip_sim_tb \
  --tree /path/to/Vsim.tree.json \
  --meta /path/to/Vsim.tree.meta.json \
  --obj-dir /path/to/obj_dir \
  --producer "Verilator 5.050 2026-07-01 rev v5.050" \
  --adapter contracts/opentitan_uart_semantic_signals.json \
  --layout /tmp/opentitan-uart-layout.json \
  --physical-oracle contracts/opentitan_uart_physical_oracle.json \
  --coverage-contract contracts/opentitan_uart_toggle_coverage.json \
  --coverage-oracle contracts/opentitan_uart_toggle_coverage_oracle.json \
  --effects /tmp/opentitan-uart-eval-effects.json \
  --output /tmp/opentitan-uart-model-manifest.json
```

`classify-effects` reads existing textual LLVM IR and invokes no external
process. It exits nonzero when an expected classification or pinned oracle
value differs. `analyze` exits nonzero if any declared semantic signal is unresolved,
ambiguous, or has a different width, or if a physical observation differs from
its oracle. It also exits nonzero if coverage storage, counts, mappings, or
fingerprints differ from the coverage oracle. Adapter `drive`/`observe`
directions remain contract-owned annotations: the JSON AST does not
independently derive them for internal testbench variables.

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
├── checkpoint_projection     not_analyzed
├── coverage_mapping          implemented, AST ↔ insertion/update ↔ words
└── eval_effects              implemented, explicit LLVM direct-call closures
```

The OpenTitan UART semantic Contract is measured: all 13 declared signals resolve
uniquely with matching widths through a fully resolved 39,379-instance hierarchy.
The physical Contract is also measured: the generated C++ ABI reports a
2,340,480-byte Syms image, root offset 192, and 13/13 exact field offsets against
the pinned oracle. The coverage Contract maps 691 AST declarations through 2,764
elaborated insertion calls and 2,541 update sites into 16,160 directional
semantic observations and all 7,842 physical words. Of those physical words,
4,858 explicitly aggregate aliases. See the
[semantic evidence](docs/opentitan-uart-semantic-evidence.md),
[physical evidence](docs/opentitan-uart-physical-evidence.md), and
[coverage evidence](docs/opentitan-uart-coverage-evidence.md). Eval-effect
classification separates the unmodified host-dependent eval from the exact
310-function device-clean GPU entry closure, then revalidates a width-1 eval at
13/13 semantic signals and 7,842/7,842 coverage words. See the
[eval-effect evidence](docs/opentitan-uart-eval-effect-evidence.md).

The same schema and command surfaces also close on a temporal VeeR AXI
LSU/DMA bridge and an OpenTitan UART TL-UL stall profile without target-specific
Python branches. They verify 17/17 and 27/27 semantic/physical bindings, map all
9,462 and 5,024 native toggle words, reproduce both independent canonical
manifests, and separate each host-dependent eval from an explicitly clean GPU
closure. The resulting fixed point and the two remaining upstream ABI artifacts
are recorded in the
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
