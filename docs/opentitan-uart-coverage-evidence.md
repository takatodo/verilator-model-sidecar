# OpenTitan UART toggle-coverage Contract

## Contract

Outcome:

> Every physical word in the pinned Verilator 5.050 OpenTitan UART-profile
> toggle array is explained by semantic toggle identities linked to JSON AST
> declarations and compiler-owned native insertion/update lowering, with aliases
> represented explicitly rather than treated as one-to-one counters.

Acceptance criteria:

1. The semantic authority is the JSON `COVERTOGGLEDECL`/`COVERTOGGLE` graph;
   Verilator's native manifest supplies the elaborated instance label and
   physical binding.
2. Every native lowering declaration resolves to one covered hierarchy instance
   and one AST declaration with equal source, page, width, and a guarded
   expression label.
3. The set of unique native insertion regions equals the set of unique native
   update regions.
4. Native counter semantics prove the physical direction order, and update
   regions cover every measured physical word.
5. Every directional semantic identity is unique and bound to a physical word;
   every physical word has at least one semantic member.
6. The measured coverage array equals the pinned adapter oracle in binding,
   offset, size, word width, and word count.
7. Canonical identities, groups, and Verilator-local bindings equal the pinned
   independent generated-only manifest fingerprints.
8. Repeated complete analyses are byte-identical.
9. One changed oracle value produces a valid mismatch manifest and CLI exit 1.
10. Checkpoint projection and eval effects remain fail-closed.
11. No generated C++ coverage text is parsed.

## Verified evidence

Environment and inputs:

- Producer: `Verilator 5.050 2026-07-01 rev v5.050`
- JSON tree SHA-256:
  `28ea5e86e4f638abe5688445bb6e479112d91132e46360b75d1c2fce07019c6e`
- Normalized semantic metadata SHA-256:
  `79d681d9fa2a75183045f5385be4d718e490b9f590cdbb30af8f6c145e28d5d4`
- Generated C++/header aggregate SHA-256:
  `c03be5a24100f416bd24328f0f94a5b3f5f06a6f4221ecc246efb25ead4ca63e`
- Coverage contract file SHA-256:
  `6a6989cb2a80b34577ddb94c251a32e1479b03ca6ad1cb11035d3cf713716550`
- Coverage oracle file SHA-256:
  `e40a07ff24184bb2d46153b7d2f2ae8f0873fa15abf6600e5a979c73cb99277c`
- Coverage-aware layout observation fingerprint:
  `70d1a55736598d268bda074541d4ce7ec2e8d81ef4213430c15b635ef96d327c`
- Complete analysis fingerprint:
  `b7d43702e8b2dd352e410e1f98e449b3788a7a28eef22399045a2170ec93561c`

Measured closure:

| Quantity | Result |
| --- | ---: |
| AST toggle declarations | 691 |
| Covered elaborated instances | 20 |
| Native lowering declarations | 2,764 |
| Directional semantic observations | 16,160 |
| Native update sites | 2,541 |
| Unique insertion/update regions | 1,271 / 1,271 |
| Measured physical words | 7,842 |
| Physical words reached by updates | 7,842 |
| Direct one-member words | 2,984 |
| Aliased multi-member words | 4,858 |
| Maximum semantic members per word | 20 |
| Unresolved mappings or issues | 0 |

Native lowering evidence:

- Producer: `Verilator 5.051 devel` fork at the coverage-manifest worktree
- Native manifest size: 86,213,777 bytes
- Repeated native manifest SHA-256:
  `b0abbbc8685527246d65026fcb84b3dfe197658ef549d498bbf27787a67fe184`
- Repeated generated C++/header aggregate SHA-256:
  `19e6f1c5405104a7093ce67392c543aaeb6199988ba0ce04d1b5fb39463337ba`
- Generated C++/header aggregate with and without manifest: byte-identical
- Native lowering declarations: 2,764
- Native update sites/regions: 2,541 / 1,271
- Native unupdated/update-only physical words: 0 / 0

The native `counter_semantics` record establishes word-offset order `1->0`, then
`0->1`, with two `uint32_t` words per bit. The measured 5.050 storage used by the
existing physical oracle is:

```text
binding       Vsim__Syms.TOP.__Vcoverage
state offset  2,275,656 bytes
size          31,368 bytes
word width    32 bits
word count    7,842
```

Canonical and adapter-local fingerprints:

```text
canonical observations  69586cb1224143b99ede5a5643234ff3d72962c2960cdda6fecde6b346d84ac2
canonical groups        a4a12ec0d77146b13bca06579e044c53af41d2fd7114095f271e9484b351f4f0
Verilator bindings      660e23401201c46ad039e1b723a81500922b790e9efe444ecb76a7084597978c
group bindings          6515b4ea3899e58aefa74b928e8e65a5c12034187b69c264e16ed8f992533d6b
AST declarations        6c5f38a50aabb5275785761581753832c2dfd03730c5cddf390cd77be1a6ad20
AST-to-physical links   c9b5831a2cc2a25172d652fb1591f4e53ef3fc63a47872880566cdffaf096e06
lowering regions        af492bb928cb09c7396d5eefdfcb942c27b334c8019615595ca2c55385d1cc59
```

All seven values equal the existing generated-C++-derived oracle. The sidecar
now proves their relationship to the JSON AST declarations and native update
regions without opening a generated C++ source file.

| Criterion | Evidence | Status |
| --- | --- | --- |
| AST authority | 691 declarations; every declaration has one `COVERTOGGLE` relation | PROVEN |
| Elaboration link | 2,764/2,764 lowerings matched across all 20 covered instances | PROVEN |
| Lowering closure | 1,271 unique insertion regions equal 1,271 update regions | PROVEN |
| Direction and update closure | Native order is `1->0`, `0->1`; 7,842/7,842 words updated | PROVEN |
| Semantic/physical completeness | 16,160 unique identities; 7,842 non-empty physical groups | PROVEN |
| Physical region | Offset, size, width, and count equal the pinned oracle | PROVEN |
| Independent fingerprints | All seven semantic, binding, AST, and region hashes equal the prior generated-only tool | PROVEN |
| Determinism | Two 49,859,781-byte hybrid reports are byte-identical | PROVEN |
| Negative control | Expected observations changed 16,160 to 16,161; exit 1 with exactly one issue | PROVEN |
| Remaining boundary | Checkpoint and eval-effect sections remain `not_analyzed` | PROVEN |
| Parser deletion | `generated_cpp_parse=not_used`; generated source count 0 | PROVEN |

The hybrid verification report reused the pinned Verilator 5.050 semantic tree,
measured layout, and existing golden oracle while replacing only its lowering
authority with the new native manifest. It reports 13/13 semantic bindings,
verified physical bindings, exact metrics, exact region values, and exact seven
fingerprints. Its SHA-256 is:

```text
ff5288adf46e2edc7f1b8547d611c83bcfb2a64b9b243d98910c146de7332657
```

Repeated hybrid verification reports both had SHA-256:

```text
ff5288adf46e2edc7f1b8547d611c83bcfb2a64b9b243d98910c146de7332657
```

The repository test suite includes deterministic alias mapping, native identity
validation, actual Verilator 5.050 capture/analyze, measured coverage-array
layout, oracle drift, generated-label drift, and manifest-tamper rejection.

## Reproduction

The OpenTitan source tree, JSON tree, and generated object directory remain
external artifacts and are not copied into this repository.

```bash
sidecar_root=/path/to/verilator-model-sidecar
opentitan_root=/path/to/opentitan-experiment
opentitan_json=/path/to/json-artifacts
opentitan_obj=/path/to/verilator-5.050/obj_dir
native_manifest=/path/to/model-manifest.json
producer_version="Verilator 5.050 2026-07-01 rev v5.050"

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar probe-layout \
  --obj-dir "$opentitan_obj" \
  --adapter "$sidecar_root/contracts/opentitan_uart_semantic_signals.json" \
  --coverage-contract "$sidecar_root/contracts/opentitan_uart_toggle_coverage.json" \
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
  --coverage-contract "$sidecar_root/contracts/opentitan_uart_toggle_coverage.json" \
  --native-manifest "$native_manifest" \
  --coverage-oracle "$sidecar_root/contracts/opentitan_uart_toggle_coverage_oracle.json" \
  --output /tmp/opentitan-uart-model-manifest.json

PYTHONPATH="$sidecar_root/src" python3 -m verilator_model_sidecar \
  validate /tmp/opentitan-uart-model-manifest.json
```

The first command measures the pinned 5.050 ABI. The second command uses the
native manifest only as the coverage-lowering authority and checks every
lowering record against that pinned semantic tree. To measure a model generated
by the fork directly, pass its matching object directory, exact producer string,
and `--native-manifest` to `probe-layout`.

## Boundary

`raw_word_index` and the measured state offsets are Verilator-local. A physical
word with multiple members exposes the logical OR of member hits under the
`nonzero_word` observation; it cannot identify which member toggled. Five AST
declarations are optimized constants whose source display labels do not survive
in JSON, so those declarations are linked by module sequence plus exact source,
page, and width, while the native lowering record supplies the display label.

This is not a CIRCT probe implementation, a sign-off coverage model, or a
cross-version Verilator ABI. The next Contract is eval-effect classification:
`proven_device_clean`, `host_dependent`, or `unknown`, followed by one-eval
CPU/GPU equivalence for the proven-clean closure.
