# OpenTitan UART physical-binding Contract

## Contract

Outcome:

> A measured Verilator 5.050 generated-C++ ABI observation is joined to the
> existing OpenTitan UART semantic identities, and the Syms image, root offset,
> and all 13 signal bindings equal the pinned independent adapter oracle.

Acceptance criteria:

1. Layout measurement uses compiled C++ `sizeof`/`offsetof`, not guessed header
   text offsets.
2. Measurement is a separate artifact; semantic `analyze` does not invoke a
   compiler, Verilator, or a shell.
3. The measured Syms image is 2,340,480 bytes and its root begins at byte 192.
4. All 13 measured signal offsets and storage sizes equal the pinned oracle.
5. Each physical row carries the resolved semantic ID and canonical RTL name.
6. Repeated probes and repeated complete analyses are byte-identical.
7. A one-byte oracle drift produces a valid mismatch manifest and CLI exit 1.
8. Checkpoint packing, coverage mapping, and eval effects remain fail-closed.

## Verified evidence

Environment and inputs:

- Producer: `Verilator 5.050 2026-07-01 rev v5.050`
- ABI compiler: `c++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- C++ standard: `c++20`
- Semantic contract SHA-256:
  `7c837cb98704e23ac72127eec9a97ff345a6ff734a075097550d06cc3f741f77`
- Physical oracle SHA-256:
  `d0da9b132773f89063759061ed184de03ce0e83bb624d285fcdaf507d18aab4d`
- Syms header SHA-256:
  `560673c15c67943b4fa7c36f9e4b1d7c939c54a44a465e2af1598d8478a189c4`
- Root header SHA-256:
  `2fef3acc22813146d4a9e46db39d8f35a104c4c5778243970d49540ac695d06a`
- Layout observation fingerprint:
  `707b2a8e516faa9f940250203eb5dd956f08334d40cc9c2769fed08f4112420a`
- Complete analysis fingerprint:
  `4839931416d74e7c7f994539795ace24fde1e13f05eea7d84a9ffd0ffc9812a7`

Result:

| Criterion | Evidence | Status |
| --- | --- | --- |
| ABI measurement | Compiled probe records `measurement=compiled_cpp_sizeof_offsetof` | PROVEN |
| Explicit boundary | `probe-layout` emits observation; subprocess-rejecting test passes around `analyze_manifest` | PROVEN |
| State image | Observed and expected bytes both 2,340,480; root offsets both 192 | PROVEN |
| Signal bindings | 13 verified, 0 resolved-only, 0 mismatched | PROVEN |
| Semantic join | Every row has a unique `sem:v1:*` ID and canonical RTL name | PROVEN |
| Determinism | Two layout files and two complete manifests compare byte-identical | PROVEN |
| Negative control | Clock oracle offset changed 248 to 249; exit 1 and exactly one mismatch | PROVEN |
| Remaining boundary | Checkpoint, coverage, and eval sections are `not_analyzed` | PROVEN |

Repeated artifact SHA-256 values:

```text
layout observation  7b866cd20fd6d40d15d48e7d8c905692a89db4d531aa14ca8c441113292aca80
complete manifest   bacdc7fd529fde130912825eca7fe6691621ca75db527a719b4b2ac47f4e6061
```

## Reproduction

The generated model remains an external dependency and is not stored here.

```bash
sidecar_root=/path/to/verilator-model-sidecar
opentitan_root=/path/to/opentitan-experiment
opentitan_json=/path/to/json-artifacts
opentitan_obj=/path/to/obj_dir
producer_version="Verilator 5.050 2026-07-01 rev v5.050"

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar probe-layout \
  --obj-dir "$opentitan_obj" \
  --adapter "$sidecar_root/contracts/opentitan_uart_semantic_signals.json" \
  --producer "$producer_version" \
  --output /tmp/opentitan-uart-layout.json

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar analyze \
  --source-root "$opentitan_root" \
  --top chip_sim_tb \
  --tree "$opentitan_json/Vsim.tree.json" \
  --meta "$opentitan_json/Vsim.tree.meta.json" \
  --obj-dir "$opentitan_obj" \
  --producer "$producer_version" \
  --adapter "$sidecar_root/contracts/opentitan_uart_semantic_signals.json" \
  --layout /tmp/opentitan-uart-layout.json \
  --physical-oracle "$sidecar_root/contracts/opentitan_uart_physical_oracle.json" \
  --output /tmp/opentitan-uart-model-manifest.json

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar \
  validate /tmp/opentitan-uart-model-manifest.json
```

## Boundary

This proves the generated C++ ABI for the pinned headers and compiler. It does
not make `Vsim__Syms` pointer-free, stable across Verilator/compiler revisions,
or sufficient as a semantic checkpoint format.

This phase record does not itself assign semantic coverage meaning to the
measured `__Vcoverage` array. That subsequent Contract is now complete in the
[OpenTitan UART coverage evidence](opentitan-uart-coverage-evidence.md), including
all 7,842 words, aliases, and aggregation.
