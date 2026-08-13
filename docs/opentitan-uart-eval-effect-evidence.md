# OpenTitan UART eval-effect Contract

## Contract

Outcome:

> Classify the unmodified Verilator UART-profile eval closure and the exact
> lowered GPU closure into `proven_device_clean`, `host_dependent`, or
> `unknown`, without trusting the existing external classifier, then reproduce
> one width-1 eval with exact declared CPU/GPU semantics and coverage.

Acceptance criteria:

1. LLVM inputs and entry symbols are explicit Contract inputs; local paths and
   timestamps do not enter the observation identity.
2. The call graph is reconstructed from the IR itself. Every reachable direct
   callee is defined, a permitted compiler intrinsic, a classified host
   dependency, or an explicit unknown.
3. Classification propagates transitively with `host_dependent` taking
   precedence over `unknown`, and `unknown` is never promoted to clean.
4. The unmodified Verilator eval is classified `host_dependent`; the exact
   lowered GPU entry slice is classified `proven_device_clean`.
5. Repeated observations are byte-identical, and a changed oracle value yields
   a valid mismatch observation plus nonzero CLI exit.
6. The effect observation is embedded in `model_manifest.json`, whose validator
   rejects tampered effect data.
7. The saved diagnostic-free CUBIN executes one resident width-1 eval and
   matches all 13 semantic signals and all 7,842 ordered coverage words.

## Verified evidence

Environment and inputs:

- Producer: `Verilator 5.050 2026-07-01 rev v5.050`
- Effect contract file SHA-256:
  `7eb038039ec08555d60a66e27dc52b1b7b9869f1aa079752a9a59ec18a6f4dbf`
- Effect oracle file SHA-256:
  `d622a982a2ab84c406b5885ba11cab1d118a8253151bf0e030a8799882ba1898`
- Canonical contract SHA-256:
  `8fc638ef2f3539c4f306211b9fe58f608586179fa3eefbb2cece87f9d6164886`
- Unmodified host LLVM IR: 103,379,507 bytes, SHA-256
  `ea84c59e2198b959930c29fae7c888024d0922b511785716df57349d4eebc649`
- Executed GPU entry-slice LLVM IR: 55,854,686 bytes, SHA-256
  `eb5aa7eb054446fac2be94f1a851459c184109cd4556eab91456214238814ba0`

The classifier reads LLVM text directly and invokes no compiler, shell, LLVM
tool, or external classifier. Its precedence is:

```text
host_dependent > unknown > proven_device_clean
```

Measured closures:

| Quantity | Host Verilator eval | GPU eval entry |
| --- | ---: | ---: |
| Region classification | `host_dependent` | `proven_device_clean` |
| Reachable functions | 314 | 310 |
| Clean functions | 251 | 310 |
| Host-dependent functions | 58 | 0 |
| Unknown functions | 5 | 0 |
| Direct call sites | 12,757 | 11,661 |
| Defined-function call sites | 560 | 562 |
| Permitted intrinsic call sites | 11,134 | 11,099 |
| Host-dependency call sites | 1,040 | 0 |
| Unknown external call sites | 23 | 0 |
| Indirect call sites | 0 | 0 |
| Inline-assembly call sites | 0 | 0 |
| Exception-control instructions | 75 | 0 |
| Loads / stores | 168,582 / 95,334 | 167,985 / 95,322 |

The host closure reaches scheduler, runtime context, host time, host I/O,
termination, allocation, and C++ runtime effects. Host precedence intentionally
keeps the region `host_dependent` even though five functions retain unknown
effects. In contrast, all 310 functions in the exact GPU entry slice close over
defined functions and Contract-permitted LLVM/NVVM intrinsics. It has no host,
unknown, indirect-call, inline-assembly, or exception-control effect.

Closure fingerprints:

```text
host Verilator eval  183e21d19bed836fa84da842d700b030f9704dbe764de06da965277882daade7
GPU eval entry       17d60b36af5285aed66089177145f5cc4deff632b551a2848fa085f0e61065aa
whole observation    ace00296469772a4b5cb62ae94feaafc19897301f6ef7a234f9dc0c4cb1368e9
```

Two independently written resolved observations are byte-identical. Each is
1,144,867 bytes with file SHA-256:

```text
63a17974af346053f0e0b3e60c653c7c0b6b8476f21fe49af8b316a2f93ec553
```

The pinned oracle produces `status=verified` and no issues. Changing only the
GPU expected classification from `proven_device_clean` to `unknown` produces a
valid `status=mismatch` observation, CLI exit 1, and exactly:

```text
oracle_region_gpu_eval_entry_classification_mismatch
```

The verified observation was then embedded into the complete OpenTitan model
manifest. The 51,070,492-byte manifest validates with:

| Section | Result |
| --- | --- |
| Manifest status | `eval_effects_verified` |
| Semantic hierarchy | 39,379 instances, unresolved 0 |
| Adapter semantics | 13/13 matched |
| Physical bindings | 13/13 verified |
| Coverage mapping | 16,160 semantics to 7,842/7,842 words, verified |
| Eval-effect regions | clean 1, host-dependent 1, unknown 0, verified |
| Local absolute paths | 0 |

Its analysis fingerprint is
`84f53b0c5d31e28d924a6f665fbbdc8b726407da2243f69b148bcd00a0af14e3`
and file SHA-256 is
`72c2c979ba4f1e1e79323d83369e43c2c3ccb9407f667c542162a80628f8c81f`.

The diagnostic-free runtime regression used the saved 3,752-byte patch CUBIN
and 58,084,464-byte eval CUBIN. One resident eval from tick 945135 through
945140 produced the same candidate-state SHA-256 as the prior baseline:

```text
6f81c8667ec8cb9eaab997a3638b83b33ae2ca8ac07c8d4152453f71d136e67a
```

The independently regenerated comparison passed all 13 semantic signals and
all 7,842 ordered coverage words with zero mismatch. There were 942 raw byte
differences outside that declared comparison surface. The loaded eval CUBIN
reported 4,256 bytes of local storage, matching the diagnostic-free baseline.

| Criterion | Evidence | Status |
| --- | --- | --- |
| Explicit artifact identity | Two input sizes and SHA-256 values are pinned | PROVEN |
| Direct-call closure | Every GPU call resolves to 310 definitions or permitted intrinsics | PROVEN |
| Fail-closed propagation | Synthetic host, unknown external, and indirect-call tests pass | PROVEN |
| Required classifications | Host is dependent; exact GPU entry is clean | PROVEN |
| Determinism and negative control | Byte-identical pair; one-field drift exits 1 | PROVEN |
| Manifest integration | Complete 51,070,492-byte manifest validates | PROVEN |
| Runtime equivalence | 13/13 signals and 7,842/7,842 words exact | PROVEN |

The repository test suite contains 17 tests. Five are specific to eval effects:
the three classification outcomes, transitive propagation, indirect-call
fail-close, oracle verification/drift and exit status, tamper rejection, and
model-manifest acceptance.

## Reproduction

Classify and verify the two LLVM inputs without running another tool:

```bash
sidecar_root=/path/to/verilator-model-sidecar
experiment_root=/path/to/gpu-toggle-coverage-minimal
obj_dir="$experiment_root/artifacts/opentitan_uart_temporal_coverage/cpu_reference/OpenTitan/default/compile-0/obj_dir"
entry_dir="$experiment_root/artifacts/opentitan_uart_gpu_baseline/entry_slices"
producer_version="Verilator 5.050 2026-07-01 rev v5.050"

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar classify-effects \
  --contract "$sidecar_root/contracts/opentitan_uart_eval_effects.json" \
  --ir "verilator_host_ir=$obj_dir/merged.ll" \
  --ir "gpu_eval_slice_ir=$entry_dir/vl_eval_batch_gpu.slice.ll" \
  --producer "$producer_version" \
  --oracle "$sidecar_root/contracts/opentitan_uart_eval_effects_oracle.json" \
  --output /tmp/opentitan-uart-eval-effects.json
```

Pass that observation to the same full analysis used by the earlier Contracts:

```bash
PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar analyze \
  --source-root "$experiment_root" \
  --top chip_sim_tb \
  --tree /path/to/Vsim.tree.json \
  --meta /path/to/Vsim.tree.meta.json \
  --obj-dir "$obj_dir" \
  --producer "$producer_version" \
  --adapter "$sidecar_root/contracts/opentitan_uart_semantic_signals.json" \
  --layout /tmp/opentitan-uart-layout.json \
  --physical-oracle "$sidecar_root/contracts/opentitan_uart_physical_oracle.json" \
  --coverage-contract "$sidecar_root/contracts/opentitan_uart_toggle_coverage.json" \
  --coverage-oracle "$sidecar_root/contracts/opentitan_uart_toggle_coverage_oracle.json" \
  --effects /tmp/opentitan-uart-eval-effects.json \
  --output /tmp/opentitan-uart-model-manifest.json

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar \
  validate /tmp/opentitan-uart-model-manifest.json
```

The width-1 runtime reproduction uses the external experiment's
`run_vl_hybrid.py` and `compare_temporal_checkpoint.py` exactly as documented
there, with `nstates=1`, `steps=1`, diagnostic-free entry CUBINs, the tick
945135 checkpoint, and the tick 945140 CPU reference.

## Boundary

The static classification proves closure over direct LLVM calls and declared
effect policy. It does not prove arbitrary-input pointer safety, pointer-free
checkpoint packing, throughput, multi-state execution, or whole-chip coverage
closure. The prior diagnostic-enabled CUDA error 700 is retained as historical
regression evidence; reproducing a deliberately broken large build is not part
of this Contract.

The external GPU tool's older placement report contains 376 functions because
it deliberately augments direct reachability with all top-level eval-phase
helper closures. That is not the sidecar's graph definition. The executed
`llvm-extract --recursive` GPU entry contains 310 definitions, exactly the 310
functions classified here; no equivalence between the two graph definitions is
claimed.
