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

### Verification必須（この境界契約を成立させるための最小要件）

- 設計同一性（pinned実体）: issue、bad/fixed リビジョン、RTL設定、checkpoint、oracle、semantic可観測量。
- 設定空間同一性: 有限の`fault_enable`軸、点ID（`point:v1:*`）と`action_domain`。
- 生成物同一性: manifestの`rev` / `checkpoint` / `oracle` とその SHA-256、runner identity、`experiment_contract`の`schema`/`surface`。
- 観測再現性:
  - 全点の`bad/fixed`ペア観測（`ground_truth`）、
  - bad→fixedの消失集合をsidecar再計算で検証（固定値注入禁止）、
  - `cpu`と`gpu`の投影値を完全一致。
- 実行証跡/選択再生:
  - `policy_trial`再生可能性と`execution`/`launch`の対応整合、
  - backend resident 幅、時刻順、launch partitionの整合、比較トレース一致。
- 生成物検証:
  - `semantic`・`ground_truth`・`trials`のfail-closed判定後に
    `adjudication`/`report_bundle`/`graph`/`markdown`が成立すること。

### Tapeout signoff非必須（現契約外）

- 合成実装（synthesis）、STA/STAサインオフ、DFT、CDC、DRC/LVS、電力、
  ゲートレベルタイミング、QORは、今回の境界契約では要求しない。

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
| I05 | Finite sweep axes and deterministic point IDs | `PROVEN` | Four-point CPU/GPU grid `fault_enable={disabled,guarded_bit0}` x `load_response_delay_cycles={0,1}`, canonical sweep SHA `781a94cd68240a327a75139341938a0cb56c19e66c3e9ea6729fe484a5f89b22`; every value is a wrapper-verified control | Runner must execute this grid unchanged. |
| I06 | CPU/GPU paired enumeration and semantic manifests | `PROVEN` | `run_ibex2188_boundary_observations.py` (executor `ibex2188-gpu-runner:v1`) enumerated all 4 points x bad/fixed through both adapters; `experiment_contract.json` + manifests + real `evidence_bundle.json` in `evidence/ibex2188_boundary_profile_inputs_v1/` | Complete. |
| I07 | Ground truth, selector replay, metrics, report, and profile | `PROVEN` | Sidecar adjudication `status=pass` + `build_boundary_report_bundle` validation pass on the real evidence bundle; profile pinned in `evidence/ibex2188_boundary_profile_v1/` (adjudication + report bundle + graph + markdown) | Complete. |
| I08 | Smallest source-justified ordered control selected and wrapper-verified | `PROVEN` | Directed wrapper controls `+load-response-delay` ∈ {0,1}; delay=1 reproduces the pinned guarded-bit0 semantics (bad oracle 1 / fixed oracle 0, observation SHA `5ce107018362183af088d4759e492afa8c7bb965754b1517d6e50a3f4dda06d8`); delay=0 terminates with a real no-fault-window observation. Candidate `load_response_delay_cycles` marked `verified_ordered_control` with finite values `[0,1]` in both configs. | Complete. |
| I09 | CPU/GPU semantic equivalence on the full grid | `PROVEN` | `run_ibex2188_boundary_observations.py` built `sm_89` cubins via `build_vl_gpu.py` and ran `run_vl_hybrid.py` resident mode on both pinned revisions; all 4 points x bad/fixed report `match=true` for the declared semantic observables. Storage 4224 B per state. | Complete. |
| I10 | Full-grid GPU boundary profile pinned with 4-policy replay | `PROVEN` | `evidence/ibex2188_boundary_profile_v1/` pins adjudication (`status=pass`, 4 points, 1 bad fail, 1 disappeared), report bundle (plot payload + SVG + Markdown), and profile; trials replay random, stratified, ordered_refinement, and novelty selectors on GPU plus an identical-trace random CPU backend comparison through the shared selector interface | Complete. |

## Rejected current claims

- A finite grid before the directed wrapper proves control of its values.
- A delay=0 fault point before the wrapper can terminate it: the wrapper previously hung waiting for `fault_seen_q`; it now terminates when the load writeback is seen while WB is still writing (`fault_seen_q || (load_writeback_seen_q && core_i.rf_write_wb)`).
- A GPU observation produced by copying CPU values into the `gpu` slot: the old `pending-v1` fixture was replaced by real `ibex2188-gpu-runner:v1` observations on 2026-08-15; the fixture is no longer the evidence source.
- Unknown-bug discovery, exploit claims, PPO/RL, or selector-GPU superiority.
- Treating the randomized upstream integrity test as a complete boundary
  profile without a deterministic checkpoint and full paired enumeration.
