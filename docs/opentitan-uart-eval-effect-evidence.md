# OpenTitan UART native eval-effect Contract

## Contract

Outcome:

> Use the Verilator fork's versioned final-AST model manifest as the host eval
> authority, independently verify its call/effect fixed point in the sidecar,
> classify the exact GPU entry slice that is executed, and preserve one-eval
> OpenTitan UART semantic and coverage equivalence.

Acceptance criteria:

1. Host function IDs, direct calls, state accesses, coverage updates, and effects
   come from the compiler-owned manifest rather than generated C++ or host LLVM.
2. The sidecar independently recomputes the native call-closure fixed point with
   precedence `host_dependent > unknown > proven_device_clean`; inconsistent or
   incomplete metadata fails closed.
3. The main host eval is `host_dependent`, while the exact executed GPU LLVM
   slice is `proven_device_clean` with no reachable host, unknown, indirect-call,
   inline-assembly, or exception-control effect.
4. Schedule and convergence semantics, byte packing, runtime-state completeness,
   and pointer-free checkpoints remain `not_provided`.
5. Repeated native manifests, checkpoint projections, effect observations, and
   complete sidecar manifests are byte-identical.
6. Existing OpenTitan UART coverage metrics and all seven semantic/binding
   fingerprints remain exact; the pinned 5.050 physical ABI and coverage offset
   are rejected across the fork's changed generated ABI.
7. A resident width-1 eval from tick 945135 to 945140 matches all 13 semantic
   signals and all 7,842 ordered coverage words against an independent CPU eval.

## Verified evidence

Compiler implementation:

- Manifest source commit: `cd62fb1e8` (`e194ffbb9` changes only test formatting).
- Configured producer string:
  `Verilator 5.051 devel rev vUNKNOWN-built20260813-e194ffbb9`.
- Native manifest schema/surface:
  `1` / `verilator_model_manifest_experimental`.
- Two independently generated 144,368,091-byte manifests are byte-identical,
  with SHA-256
  `29bd4e1480af781172350164afffb2fa836a5b4a3dc6ad27ac71ad858f9dfa0d`.
- All 167 generated C++ and header files are byte-identical between the two
  final-head runs and the generated model used by the one-eval runtime proof.

The native manifest contains:

| Surface | Result |
| --- | ---: |
| Field definitions | 43,422 |
| Stored instances | 7 |
| Checkpoint-included definitions | 43,421 |
| Checkpoint-excluded definitions | 1 |
| Toggle semantic observations | 16,160 |
| Physical toggle words | 7,842 |
| Aliased physical words | 4,858 |
| Eval functions | 464 |
| Final clean / unknown / host functions | 244 / 41 / 179 |
| Main eval classification | `host_dependent` |

The deterministic sidecar checkpoint projection expands the included
definitions onto stored instances without reading serializer C++:

| Quantity | Result |
| --- | ---: |
| Stored field occurrences | 43,417 |
| Included but uninstantiated definitions | 4 |
| Unsupported included definitions | 0 |
| Runtime state | `not_provided` |
| Packing | `not_provided` |

Both 34,263,952-byte projections have SHA-256
`c80f8c4640d2040aefa31aac4461ba533228ce9b6f502482eec5a5786c550a7d`;
their projection fingerprint is
`edbe6295cea8fa8d40701faf66b9072653756574eadb2743c2b62bbbc6ad3b0a`.

### Exact host and GPU closures

The version-2 effect Contract uses `verilator_native_eval` for the host and
`llvm_ir` only for the downstream GPU artifact. The sidecar does not read a host
`merged.ll` file.

| Quantity | Native host main eval | Executed GPU eval slice |
| --- | ---: | ---: |
| Artifact SHA-256 | `29bd4e14…fa0d` | `a5bbe3a6…7c331` |
| Classification | `host_dependent` | `proven_device_clean` |
| Reachable functions | 290 | 289 |
| Clean functions | 216 | 289 |
| Unknown functions | 22 | 0 |
| Host-dependent functions | 52 | 0 |
| Direct call sites | 316 | 11,546 |
| State read / write sites | 335,574 / 93,657 | 171,343 / 87,699 LLVM loads/stores |
| Coverage update sites | 1,270 | represented by lowered stores |
| Host effect sites | 1,019 | 0 |
| Unknown effect sites | 1,086 | 0 |
| Indirect / inline-asm / exception effects | 0 / 0 / 0 | 0 / 0 / 0 |

Closure fingerprints:

```text
native host main eval  3d279ca1d03fc770ea60647b621302929daa839b02d724bebf494b1b1fc30cf9
executed GPU eval      0b0e416994e503f7d34a166edb352a5494581c0156492038f0bcf4817239087b
whole observation     d066fa140d21ff01378be6efefe5c7fd200f223a956f0ef00633b167cf6d64e8
```

Two oracle-verified observations are byte-identical with SHA-256
`56a7b6a2c4c5d754a3de2d100b751ecd2bd1c707c6fbc70a5ec0d9dae54a728d`.
The repository's oracle file has SHA-256
`016da525c80e3c5708ed496896ca410b87cb39cb988ac9f7fca0d493215e8edc`.

The complete current-version sidecar manifest validates and is deterministic:

| Section | Result |
| --- | --- |
| Manifest status | `eval_effects_verified` |
| Semantic hierarchy | 39,379 instances, unresolved 0 |
| Adapter semantics | 13/13 matched |
| Physical bindings | 13/13 resolved |
| Coverage mapping | 16,160 semantics to 7,842 words, resolved |
| Eval regions | clean 1, host-dependent 1, unknown 0, verified |
| Repeated file SHA-256 | `77fc9d8c73ae13bc76c004456db3ab124d8ade4f99e2910fc55f9d914ef71abb` |

All ten coverage metrics and all seven fingerprints equal the existing 5.050
golden. Applying the old coverage oracle reports exactly
`oracle_region_state_offset_mismatch`: the coverage array moved from byte
2,275,656 to 2,272,888. Applying the old physical oracle also rejects the changed
root-header identity and Syms size, which moved from 2,340,480 to 2,337,664
bytes. These are intended cross-version fail-closed results, not semantic
coverage regressions.

### One-eval preservation

The exact classified GPU slice was compiled into a 56,651,432-byte CUBIN with
SHA-256
`d6b431eb245aef757bdf9546c1d27632889116015602938097815cc6dbb22edc`.
The patch kernel is 3,752 bytes with SHA-256
`af8f3aefb101e65ee197cde9753eb96306616e76e7d7c5dc3cb3cb103c4aae25`.

The CPU reference and GPU both started from a post-eval checkpoint at tick
945135. The GPU applied the one-byte clock patch at state offset 248 and ran one
eval through tick 945140. CUDA initialization, both module loads, both launches,
final synchronization, and state download succeeded; CUDA error 700 did not
occur.

| Comparison | Result |
| --- | --- |
| State image size | 2,337,664 bytes |
| Semantic signals | 13/13 exact, mismatch 0 |
| Ordered coverage | 7,842/7,842 exact, mismatch 0 |
| Nonzero coverage words | CPU 1,913 / GPU 1,913 |
| Coverage region SHA-256 | both `09c83b6f…cfe6e` |
| Comparison report SHA-256 | `012bf57f02a2c70f809b7b2c44f53b0de766b33c01837eaf9b75b6d856a0a42e` |

Raw Syms bytes are deliberately not the equivalence surface. This run contained
1,161 differing bytes in host pointers, scheduler state, and other
backend-specific storage. That count is diagnostic only and may vary with host
allocation; it is not an oracle.

## Reproduction

Given a matching fork-generated native manifest and the exact GPU entry slice:

```bash
sidecar_root=/path/to/verilator-model-sidecar
native_manifest=/path/to/model-manifest.json
gpu_slice=/path/to/vl_eval_batch_gpu.slice.ll
producer="$(jq -r .producer "$native_manifest")"

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar \
  project-native-checkpoint \
  --manifest "$native_manifest" \
  --output /tmp/opentitan-uart-checkpoint-projection.json

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar \
  classify-effects \
  --contract "$sidecar_root/contracts/opentitan_uart_eval_effects.json" \
  --native-manifest "verilator_native_manifest=$native_manifest" \
  --ir "gpu_eval_slice_ir=$gpu_slice" \
  --producer "$producer" \
  --oracle "$sidecar_root/contracts/opentitan_uart_eval_effects_oracle.json" \
  --output /tmp/opentitan-uart-eval-effects.json
```

Run the two commands twice and compare their outputs with `cmp`. The complete
analysis then receives the same native manifest, its matching compiled layout,
the pinned semantic/coverage Contracts, and the verified effect observation.
Do not apply the 5.050 physical or coverage-offset oracle to a different
generated ABI; that mismatch must remain visible.

The runtime reproduction uses the external experiment's
`build_vl_gpu_entry_slices.py`, `run_vl_hybrid.py`, and
`compare_temporal_checkpoint.py`. Compile the CPU checkpoint harness against the
same generated model and set its expected Syms size to the measured layout. Use
`nstates=1`, `steps=1`, the tick-945135 checkpoint, the `248:0` patch record, and
the independently generated tick-945140 CPU checkpoint. The comparison command
must exit zero with `ordered_coverage_exact=true` and
`semantic_signals_exact=true`.

## Boundary

This proves the declared final-AST host closure, the exact executed downstream
GPU closure, and one pre-completion eval. It does not provide event-region
schedule semantics, convergence semantics, serialized byte order, runtime
context completeness, a pointer-free checkpoint, arbitrary-input memory safety,
multi-state throughput, whole-chip closure, or sign-off coverage equivalence.
