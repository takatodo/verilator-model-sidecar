# RTL boundary benchmark: ibex2188-boundary-v1

- Target: `ibex2188`
- Sweep points: `4`
- Sweep-space SHA-256: `781a94cd68240a327a75139341938a0cb56c19e66c3e9ea6729fe484a5f89b22`
- Action-domain SHA-256: `b048b443ecacb4a175926c14eea634837930413710202af2636f79342ffce6f8`
- Source adjudication SHA-256: `98635b24bbe02810bab55ecf8d9044b6c09980d9e989ae37edacaed25001acf2`
- Plot payload SHA-256: `68e0f3f58ddd9b906d791f50ba5fb005896130e3061a2e0dd23a74fa3e197cf1`

## Selector comparison on one GPU executor

| Trial | Policy | State evals | Launches | Wall ns | First violation | First bracket | Exact boundary | Edge P | Edge R | Hausdorff | Failure IoU | Minimal recovery |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| random_gpu | random | 2 | 1 | 668058921 | — | — | — | not_applicable | 0/2 | unbounded | 0/1 | 0/1 |
| stratified_gpu | stratified | 2 | 1 | 1165542289 | — | — | — | not_applicable | 0/2 | unbounded | 0/1 | 0/1 |
| ordered_refinement_gpu | ordered_refinement | 2 | 1 | 1568152917 | — | — | — | not_applicable | 0/2 | unbounded | 0/1 | 0/1 |
| novelty_gpu | novelty_boundary_guided | 3 | 2 | 2715498515 | 2 | — | 2 | 2/2 | 2/2 | 0 | 1/1 | 1/1 |

## Same selector across CPU and GPU backends

| Trial | Backend | Resident width | State evals | Cycle evals | Launches | Wall ns |
|---|---|---:|---:|---:|---:|---:|
| random_cpu | cpu | 1 | 2 | 46 | 2 | 2752759904 |
| random_gpu | gpu | 2 | 2 | 46 | 1 | 668058921 |

## Claim boundary

The selector table compares sampling policies on one declared GPU executor. The backend table compares an identical replayed selector trace across CPU and GPU adapters. Coverage or boundary gain identifies an interesting prefix; only the declared oracle determines failure. This report does not claim unknown-bug discovery, PPO/RL superiority, or that GPU execution improves the sampling algorithm itself.
