# Target-independent validation and minimum upstream ABI Contract

## Contract

Outcome:

> Apply the same sidecar schema and command surfaces to the temporal VeeR AXI
> LSU/DMA bridge and a second OpenTitan UART TL-UL stall profile without
> target-specific Python logic, then state only the information that still
> requires a stable Verilator upstream ABI.

Acceptance criteria:

1. Both targets use unmodified Verilator 5.050 JSON, generated C++ headers and
   LLVM artifacts with explicit identities; only declarative target Contracts
   differ.
2. The existing semantic comparison surfaces resolve uniquely: 17/17 VeeR
   fields and 27/27 OpenTitan TL-UL fields, with measured physical bindings.
3. Every native toggle word is linked to JSON AST semantics and generated
   insertion/update lowering: 9,462/9,462 VeeR words and 5,024/5,024 TL-UL
   words, preserving aliases explicitly.
4. The four canonical observation/group/binding fingerprints equal the
   independent manifests already produced by the external experiment.
5. Each unmodified host eval and GPU-lowered eval entry receives a fail-closed
   `proven_device_clean`, `host_dependent`, or `unknown` classification; a
   clean claim is allowed only with no reachable host, unknown, indirect,
   inline-assembly, or exception-control effect.
6. Complete manifests validate, contain no local absolute path, and repeated
   analyses are byte-identical.
7. Source inspection demonstrates that target names do not appear in sidecar
   implementation branches; target knowledge is confined to JSON Contracts.
8. The upstream proposal contains only information unavailable as a stable,
   machine-readable fact from unmodified Verilator outputs, supported by the
   three measured profiles.

## Verified evidence

Environment:

- Producer: `Verilator 5.050 2026-07-01 rev v5.050`
- ABI compiler: `c++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- ABI language mode: `c++20`
- Analysis path: the same `probe-layout`, `classify-effects`, `analyze`, and
  `validate` commands for both targets
- Implementation dependencies: Python standard library only

The source model names, entry symbols, and expected observations occur only in
the JSON Contracts. The implementation was not patched for either target.
Two generic syntax cases exposed by these inputs were added to the coverage
reader: JSON `ARRAYSEL` expressions and lexical generate/block prefixes in
Verilator display labels. Both are interpreted by node/label syntax, not by
target identity.

### Explicit artifact identities

| Artifact | VeeR temporal bridge | OpenTitan UART TL-UL stall |
| --- | --- | --- |
| JSON tree bytes | 1,479,349 | 12,262,008 |
| JSON tree SHA-256 | `22981324f6112b3e8ab0d153ca2af17477924c811bca2af455ce1aff0dc470027` | `897745e08aa632735e172f6c26381f04db62fd60db6b6b0a89b297115d4f8a5f` |
| Normalized metadata SHA-256 | `c7da1bb7f329d021e840c7074e79a392052d22472b1baa42e4ed2882be651555` | `18e341727514122b5bd8da87d511da6bad3f5f2195584b9ec60313b0f89a952b` |
| Generated C++ file count | 9 | 17 |
| Generated C++ aggregate SHA-256 | `f502c069335aae9bb51372c9653779264f7cec59a4d875ffb50e9e0895ffbbed` | `30dc856efd2e190d2491798dce4668e9e00c972b9478885493eb23bc1043b5c7` |
| Root header SHA-256 | `4b14fcf9a11798d5e09ae9b07550f68c897ec6bd3ce8d2d8f649a53f6bdf3b4e` | `298f6a33d13102937bc1ced863f20e4c78e046d3ebaf7077345d0df6b7149844` |
| Syms header SHA-256 | `00c4d0ba0ea08609b5fc31bc5240ca43c56032398108fb2daf685a25a1a2e493` | `90a4806ab56256598b996345f7795378d3136152d4f2d8aef3bb1ab6906fc02d` |
| Host LLVM SHA-256 | `f3af030edb118d4546830a1bee7a7770c689e268fd9c4090b8bb0cb240ffb18f` | `cc87b86f50dbac0e650c19283a2a446fdbb08f0375b92d41438ed4d59b7300ed` |
| GPU LLVM SHA-256 | `f5b4c74312edc0be263a40b38144ae70398be8231359a390d042d5315a418a3d` | `3ebeff079e7ea9fcdcaab68a7e4ca5cb36342cdde9545024de6c3da81f51e558` |

The JSON and generated C++ were produced from unmodified Verilator 5.050. The
LLVM inputs are explicit downstream compilations of those generated sources;
the sidecar reads them but does not invoke Clang, LLVM, CUDA, or a shell.

### Semantic and physical closure

| Quantity | VeeR temporal bridge | OpenTitan UART TL-UL stall |
| --- | ---: | ---: |
| Elaboration instances | 3 | 175 |
| Used module definitions | 3 | 68 |
| Unresolved hierarchy links | 0 | 0 |
| Declared semantic fields | 17 | 27 |
| Unique width-correct matches | 17 | 27 |
| Verified physical bindings | 17 | 27 |
| Physical mismatches | 0 | 0 |
| Measured Syms image bytes | 78,016 | 42,112 |
| Root offset in Syms | 192 | 192 |
| Layout observation fingerprint | `2f86ebe515318a615b1c0f60a87693d9d956e167483667ae67a944c7bf7c991a` | `89bc69b1af76dd6ec72a4687d5d8460e9d133865418d9a0f610600a53a52f366` |

The VeeR GPU runtime uses the root image rather than the containing Syms image.
Its independently compiled root probe reports the same 17 offsets after
subtracting the measured 192-byte Syms root offset; its coverage array begins
at root byte 2,088, equal to sidecar Syms byte 2,280. The OpenTitan TL-UL
offsets and 5,024-word region independently equal the existing temporal-adapter
and checkpoint-equivalence reports.

### Coverage closure and independent fingerprints

| Quantity | VeeR temporal bridge | OpenTitan UART TL-UL stall |
| --- | ---: | ---: |
| AST toggle declarations | 436 | 1,381 |
| Covered elaborated instances | 3 | 174 |
| Generated insertion calls | 436 | 2,288 |
| Directional semantic observations | 13,120 | 12,728 |
| Generated update sites | 680 | 1,465 |
| Unique update regions | 312 | 612 |
| Bound/updated physical words | 9,462 / 9,462 | 5,024 / 5,024 |
| Aliased physical words | 2,924 | 2,500 |
| Maximum members in one word | 6 | 165 |

All four identities match manifests made earlier by the independent
generated-only adapter:

| Fingerprint | VeeR temporal bridge | OpenTitan UART TL-UL stall |
| --- | --- | --- |
| Canonical observations | `07e7abfa22e05cade1f4efd571f0a998a1bb58ac3822f25748721d48cd399da0` | `26f0289376c4b183de6965d077524ab867d8fa363f5143411e91747fa549625b` |
| Canonical groups | `45ddb6fbaf7b5da3a928be2f6ebbd169691496c60f09379021f59efe123b4eca` | `609f549e4fad7f3c3a159d35d3c4b6a25aab9e7528a7930dfd94acc413d9a383` |
| Semantic-to-word bindings | `99731b5a42b4c20d43ce02cf4c8d246ba68c1e697a593294a631d28d0510e12f` | `17096929e90c064a02b809835c39292db99575f3af89b44297910c887d700915` |
| Group-to-word bindings | `3992770b17174d472fe0d0893d52a280aecc870ec023dc32a72d51e01467f387` | `b82b4fc488a210f0751e1cb62707702f759d02e744ef193a2cd9d61e91469991` |

### Eval effects

| Quantity | VeeR host / GPU | OpenTitan TL-UL host / GPU |
| --- | ---: | ---: |
| Classification | `host_dependent` / `proven_device_clean` | `host_dependent` / `proven_device_clean` |
| Reachable functions | 11 / 5 | 12 / 7 |
| Host-dependent functions | 1 / 0 | 7 / 0 |
| Unknown functions | 0 / 0 | 0 / 0 |
| Host-dependency call sites | 2 / 0 | 25 / 0 |
| Indirect calls | 0 / 0 | 0 / 0 |
| Inline assembly calls | 0 / 0 | 0 / 0 |
| Exception-control instructions | 0 / 0 | 3 / 0 |
| Observation fingerprint | `dfe88b69f55c3a448354964b5ab08aeabca2bd9559d309d09173d1451870dc61` | `db14769a2f8558b904329017c747b443f83c62335de99217a30294fe0efed79e` |

Thus neither unmodified host entry is device-clean. Each explicitly named
downstream GPU closure is clean only because every reachable function and call
site passes the fail-closed policy; no classification is inferred from its
name or intended backend.

Existing runtime reports were retained as independent dynamic anchors rather
than rerun. VeeR compares 336 state pairs with 5,712 semantic values and
3,179,232 native coverage words, with zero semantic, coverage, or oracle
mismatch. The TL-UL report compares 8,192 CPU/GPU states, 221,184 semantic
values and 41,156,608 coverage words, also with zero mismatch or oracle failure.

### Complete-manifest verification

| Property | VeeR temporal bridge | OpenTitan UART TL-UL stall |
| --- | --- | --- |
| Manifest status | `eval_effects_verified` | `eval_effects_verified` |
| Analysis fingerprint | `824a4e3504a97e1c6b7a88be37ad29c95ab287af444c981296f69b077a7184ae` | `43b292984501ffd7facf782eafc608df3d90ccf4c40f799e27dcccbe9cfda658` |
| Manifest bytes | 15,979,142 | 14,334,438 |
| Repeated file SHA-256 | `1c5997791d85c97649852073f45644083ed9c130371c99beb41b47476b631edb` | `59d89c4bba34e535592d8f42fc3e47f4b53501dee28e458d39f2d62e6c1ac1c5` |
| Byte-identical repeated runs | yes | yes |
| Validator result | valid | valid |
| Local absolute paths | 0 | 0 |

Source inspection with target-name patterns produced no match in
`src/verilator_model_sidecar`. Generic uses of the word `target` are contract
validation and call-graph variables, not target dispatch.

| Criterion | Evidence | Status |
| --- | --- | --- |
| Explicit unmodified inputs | Producer and all input identities above are pinned | PROVEN |
| Semantic/physical surfaces | 17/17 and 27/27 unique, width-correct, physically verified | PROVEN |
| Complete toggle mapping | 9,462/9,462 and 5,024/5,024 words bound and updated | PROVEN |
| Independent identity | Eight of eight canonical/binding fingerprints match | PROVEN |
| Fail-closed eval effects | Both hosts dependent; both explicit GPU closures clean with all prohibited counts zero | PROVEN |
| Validation/determinism/path hygiene | Both manifest pairs valid, byte-identical, and path-free | PROVEN |
| No target branch | Source scan has no OpenTitan, VeeR, UART TL-UL, or AXI LSU name | PROVEN |
| Minimum upstream boundary | The two artifacts below follow from facts still absent or unstable | PROVEN |

## Minimum upstream ABI proposal

The sidecar experiment reaches a fixed point: semantic extraction, generated
layout measurement, toggle reconstruction, and explicit-LLVM effect analysis
are possible today. That falsifies the need for a Verilator CUDA backend and
also falsifies a requirement for target-specific compiler code. It does not
produce a stable heterogeneous execution ABI. Two upstream artifacts remain
necessary.

### 1. Stable semantic-state manifest

Minimum machine-readable fields:

```text
model/schema/producer identity
semantic field ID
  hierarchy + source identity + width
  persistent-mutable | immutable-shared | transient-scratch | external
  packed offset + size + alignment, or an explicit pack/unpack binding
  initialization/reset semantics needed by the packed form
observable ID -> semantic field ID
coverage semantic ID
  type + source/bin/expression identity
  physical storage binding
  alias/aggregation semantics
```

Why it survives deletion: compiled `offsetof` proves only one generated C++
ABI. It cannot determine which bytes are portable semantic state, and VeeR
already exposes two valid physical containers (39,936-byte root versus
78,016-byte Syms). All three manifests therefore leave
`checkpoint_projection=not_analyzed`. Removing the semantic-state artifact
leaves no pointer-free, versioned state image that a heterogeneous backend can
safely clone. Removing the coverage portion loses the observed N:M alias
semantics—up to 165 semantic members per TL-UL word—so a raw counter index is
not a semantic coverage identity.

The consumer-owned roles `drive`, `observe`, `oracle`, and
`coverage_summary` do not belong in this upstream artifact. They remain JSON
adapter Contracts.

### 2. Device-clean eval-region manifest

Minimum machine-readable fields:

```text
region ID + exported entry ABI
semantic field IDs read and written
region dependencies and schedule/convergence semantics
reachable external/runtime effects
  scheduler, context, time, DPI, VPI, coroutine, allocation, I/O, termination
classification: proven_device_clean | host_dependent | unknown
```

Why it survives deletion: each normal Verilator host eval reaches host runtime
effects, while each clean result exists only for an explicitly supplied custom
LLVM closure. Generated C++ does not expose stable region boundaries,
read/write sets, an evaluation DAG, or a device-clean assertion. Removing this
artifact forces every backend to rediscover the schedule and host boundary and
cannot distinguish a safe closure from the measured host-dependent entries.
`unknown` must remain a first-class fail-closed result.

The proposal does not require CUDA, NVPTX, HIP, SoA/AoS batching, a particular
coverage counter width, or a fixed generated-C++ object layout. Those are
backend choices once the two semantic contracts exist.

## Reproduction shape

For each target, `probe-layout` receives only its adapter and coverage JSON;
`classify-effects` receives only its effect JSON, named IR inputs, and optional
oracle; `analyze` receives those observations plus the semantic, physical, and
coverage JSON Contracts. Replacing the target changes paths and JSON files, not
the command surface or Python implementation. Finally:

```bash
PYTHONPATH=src python3 -m verilator_model_sidecar validate /tmp/model-manifest-a.json
cmp /tmp/model-manifest-a.json /tmp/model-manifest-b.json
```

## Boundary

Existing CPU/GPU correctness reports remain runtime evidence; the large
campaigns are not rerun. This Contract does not claim full AXI4 compliance,
full-chip OpenTitan coverage closure, cross-version ABI stability, performance
gain, sign-off equivalence, or a need for PPO. Generated JSON, object files,
LLVM IR, manifests, and runtime reports remain outside Git.
