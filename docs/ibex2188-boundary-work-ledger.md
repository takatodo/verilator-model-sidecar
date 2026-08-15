# Ibex #2188 Boundary Benchmark Work Ledger

Last reviewed: 2026-08-15

This ledger is the authority for the second known-bug temporal-boundary
benchmark. It is intentionally separate from the completed TL-UL #10818
benchmark. Runtime evidence is admitted only after the repository-defined
runner produces it and the sidecar accepts its identity and provenance.

## Fixed target identity

The public [Ibex #2188 issue](https://github.com/lowRISC/ibex/issues/2188)
pins the bad source revision
`668233699df9ec2a40413e69e0de0a5b10185980`. The closing
[fix commit](https://github.com/lowRISC/ibex/commit/9e4a950aa6aa0e20eb638aeeb78743d4a9ddaaeb)
is the fixed revision and changes only the register-file ECC-alert predicates.

The target Contract is tracked in the gpu-rtl-sim branch
`feature/ibex2188-boundary` at
`config/ibex2188_boundary_benchmark.json`.
It requires the Ibex `opentitan` configuration: `SecureIbex=1`,
`WritebackStage=1`, and `RegFileFF`; `RegFileECC` is derived from
`SecureIbex` by `ibex_top`.

The independent oracle follows the existing upstream
`core_ibex_rf_intg_test`: inject a single ECC read-data error only when the
operand is read and either it does not match WB or WB is not writing. If the
instruction is valid, a major internal alert must result. The #2188 failing
subcase is the second disjunct: `rf_rd_wb_match=1` and `rf_write_wb=0`.

## Status vocabulary

- `PROVEN`: source and current verification prove the result.
- `STATIC_READY`: target/sidecar implementation exists without runtime
  evidence.
- `PENDING_EXTERNAL`: requires the repository-defined runner or CI.
- `REJECTED_CURRENT_CONTRACT`: does not currently prevent the benchmark
  Contract from being proven.

## ASIC/RTL verification essentials for this benchmark

This benchmark covers RTL verification evidence, not tapeout signoff. The
required ASIC-design inputs are therefore the items needed to prove a
reproducible local simulation Contract:

- pinned design authority: public issue, bad/fixed revisions, RTL
  configuration, checkpoint, oracle identity, and semantic observables;
- deterministic stimulus surface: finite sweep axes, canonical point IDs, and
  a repository-defined runner or externally produced evidence profile;
- independent correctness signals: CPU/GPU semantic equality, oracle violation,
  and coverage or novelty features recorded as separate facts;
- complete bad/fixed comparison: every admitted point has paired bad/fixed
  observations and fixed-revision disappearance is recomputed, not asserted;
- boundary reconstruction: pass/fail points, adjacent boundary edges, failure
  components, and applicable minimal failing points are derived from raw
  observations;
- accounting separation: selector policy, backend resident execution, launch
  timing, and wall time remain independently attributable; and
- fail-closed provenance: manifests, reports, graphs, profiles, and hashes are
  validated before a `pass` result is admitted.

Physical-design signoff, synthesis QoR, STA, DFT, CDC signoff, DRC/LVS, power
closure, and gate-level timing are outside this benchmark Contract unless a
future target explicitly makes one of them part of the oracle.

## Completion Contract

Complete #2188 only after a finite, source-justified sweep has paired CPU/GPU
evidence on both pinned revisions; all points have semantic equality; the bad
failure region and fixed disappearance are recomputed by the sidecar; and the
same selector/backend comparison surfaces used for #10818 pass their
provenance and accounting gates.

## Work items

| ID | Required result | Status | Evidence | Next condition |
| --- | --- | --- | --- | --- |
| I01 | Public issue and distinct bad/fixed revisions pinned | `PROVEN` | Issue #2188 and commit `9e4a950`; its parent is the issue-pinned SHA | Reopen only if local checkout lacks either object. |
| I02 | ECC-capable, writeback-stage configuration pinned | `PROVEN` | `ibex_configs.yaml` `opentitan`; `ibex_top.sv` derives `RegFileECC` from `SecureIbex` | Runner must build this configuration unchanged. |
| I03 | Independent alert oracle and raw observables defined | `PROVEN` | Existing `core_ibex_rf_intg_test` injects ECC read-data corruption and checks `alert_major_internal_o` under the corrected predicate | Directed wrapper must expose the projection verbatim. |
| I04 | Deterministic checkpoint and program/memory harness | `PROVEN` | `run_ibex2188_cpu_regression.sh` compiled a local `ibex_core` wrapper at both pinned revisions; observation SHA `5ce107018362183af088d4759e492afa8c7bb965754b1517d6e50a3f4dda06d8` records bad violation `1` and fixed violation `0` | GPU adapter must use the same checkpoint semantics. |
| I05 | Finite sweep axes and deterministic point IDs | `PROVEN` | Two-point `fault_enable={disabled,guarded_bit0}` CPU grid, canonical sweep SHA `02933b99491bfbe4a66fedb92fec519d93a01cad9f5479fea285b37dcc60cfba`; the values are exactly the wrapper's no-fault control and guarded bit-0 fault | GPU must execute this grid unchanged. |
| I06 | CPU/GPU paired enumeration and semantic manifests | `PENDING_EXTERNAL` | Sidecar admission surfaces are proven by #10818 | Runner emits paired per-point observations/manifests. |
| I07 | Ground truth, selector replay, metrics, report, and profile | `PENDING_EXTERNAL` | Target-independent sidecar surfaces are proven by #10818 | Evidence passes sidecar adjudication. |

## Rejected current claims

- A finite grid before the directed wrapper proves control of its values.
- Unknown-bug discovery, exploit claims, PPO/RL, or selector-GPU superiority.
- Treating the randomized upstream integrity test as a complete boundary
  profile without a deterministic checkpoint and full paired enumeration.
