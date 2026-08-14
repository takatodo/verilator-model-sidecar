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
| I04 | Deterministic checkpoint and program/memory harness | `PROVEN` | `run_ibex2188_cpu_regression.sh` compiled a local `ibex_core` wrapper at both pinned revisions; observation SHA `e75c98259ce333919573718cc34ccdef63830258a33084ce5b1d20e3a7a8e53d` records bad violation `1` and fixed violation `0` | GPU adapter must use the same checkpoint semantics. |
| I05 | Finite sweep axes and deterministic point IDs | `PENDING_EXTERNAL` | Candidate axes are source-justified, but values are intentionally unset | External experiment Contract declares only wrapper-controlled values. |
| I06 | CPU/GPU paired enumeration and semantic manifests | `PENDING_EXTERNAL` | Sidecar admission surfaces are proven by #10818 | Runner emits paired per-point observations/manifests. |
| I07 | Ground truth, selector replay, metrics, report, and profile | `PENDING_EXTERNAL` | Target-independent sidecar surfaces are proven by #10818 | Evidence passes sidecar adjudication. |

## Rejected current claims

- A finite grid before the directed wrapper proves control of its values.
- Unknown-bug discovery, exploit claims, PPO/RL, or selector-GPU superiority.
- Treating the randomized upstream integrity test as a complete boundary
  profile without a deterministic checkpoint and full paired enumeration.
