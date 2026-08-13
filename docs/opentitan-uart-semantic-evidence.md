# OpenTitan UART semantic Contract

## Contract

Outcome:

> An unmodified Verilator 5.050 OpenTitan UART-profile artifact can be analyzed
> without running Verilator, and every signal declared by the UART adapter can be
> bound to exactly one elaborated semantic entity with the declared width.

Acceptance criteria:

1. The external JSON tree, JSON metadata, and generated C++ directory are the
   only model artifacts consumed by `analyze`.
2. The hierarchy has no unresolved cell-to-module links or duplicate paths.
3. All 13 UART-profile signals resolve exactly once.
4. All 13 resolved widths equal the adapter declarations.
5. A repeated analysis of identical inputs emits byte-identical manifests.
6. Physical bindings, checkpoint packing, coverage mapping, and eval effects
   remain fail-closed as `not_analyzed`.

## Verified evidence

Environment and inputs:

- Producer: `Verilator 5.050 2026-07-01 rev v5.050`
- Top module: `chip_sim_tb`
- JSON tree bytes: `282691741`
- JSON metadata bytes: `45152116`
- JSON tree SHA-256:
  `28ea5e86e4f638abe5688445bb6e479112d91132e46360b75d1c2fce07019c6e`
- Normalized semantic metadata SHA-256:
  `79d681d9fa2a75183045f5385be4d718e490b9f590cdbb30af8f6c145e28d5d4`
- Generated C++/header files: `172`
- Generated C++ aggregate SHA-256:
  `c03be5a24100f416bd24328f0f94a5b3f5f06a6f4221ecc246efb25ead4ca63e`
- Semantic contract SHA-256:
  `7c837cb98704e23ac72127eec9a97ff345a6ff734a075097550d06cc3f741f77`

Result:

| Criterion | Evidence | Status |
| --- | --- | --- |
| External analysis | Manifest `artifact_mode=external`; unit test rejects any subprocess call from `analyze_manifest` | PROVEN |
| Hierarchy closure | 39,379 instances, 1,738 used module definitions, 0 unresolved | PROVEN |
| Signal identity | 13 matched, 0 unmatched, every `match_count=1` | PROVEN |
| Signal width | 13/13 `width_match=true` | PROVEN |
| Determinism | Two complete manifests were byte-identical | PROVEN |
| Fail-closed boundary | All four unimplemented sections validate only as `not_analyzed` | PROVEN |

The two repeated manifests both had SHA-256:

```text
2fe2540aaa19537fd6aa5d9448299149b0f8aaf3354a9eafada4ac1b53d7bbe3
```

## Reproduction

The large OpenTitan sources and generated artifacts are deliberately not stored
in this repository. Given the matching external artifacts, run:

```bash
sidecar_root=/path/to/verilator-model-sidecar
opentitan_root=/path/to/opentitan-experiment
opentitan_json=/path/to/json-artifacts
opentitan_obj=/path/to/obj_dir
producer_version="Verilator 5.050 2026-07-01 rev v5.050"

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar analyze \
  --source-root "$opentitan_root" \
  --top chip_sim_tb \
  --tree "$opentitan_json/Vsim.tree.json" \
  --meta "$opentitan_json/Vsim.tree.meta.json" \
  --obj-dir "$opentitan_obj" \
  --producer "$producer_version" \
  --adapter "$sidecar_root/contracts/opentitan_uart_semantic_signals.json" \
  --output /tmp/opentitan-uart-model-manifest.json

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar \
  validate /tmp/opentitan-uart-model-manifest.json
```

## Boundary

The `drive` and `observe` directions are adapter-owned behavioral annotations.
Internal testbench variables have RTL direction `none`, so this stage preserves
the adapter direction but does not claim to derive it independently from the AST.

This record itself makes no physical-layout or coverage-mapping claim. The
subsequent generated-C++ layout Contract is now complete and recorded in
[OpenTitan UART physical evidence](opentitan-uart-physical-evidence.md).
