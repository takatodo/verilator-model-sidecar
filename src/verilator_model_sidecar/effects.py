"""Fail-closed LLVM call-closure and eval-effect classification."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


EFFECT_CONTRACT_SURFACE = "verilator_eval_effect_contract"
EFFECT_ORACLE_SURFACE = "verilator_eval_effect_oracle"
EFFECT_OBSERVATION_SURFACE = "verilator_eval_effect_observation"
EFFECT_SCHEMA_VERSION = 1

_CLASSIFICATIONS = {
    "proven_device_clean",
    "host_dependent",
    "unknown",
}
_CLASSIFICATION_PRECEDENCE = [
    "host_dependent",
    "unknown",
    "proven_device_clean",
]
_SYMBOL_PATTERN = r'(?P<symbol>"(?:[^"\\]|\\.)*"|[-$._A-Za-z0-9]+)'
_DEFINE_RE = re.compile(r"^\s*define\b.*?@" + _SYMBOL_PATTERN + r"\s*\(")
_DECLARE_RE = re.compile(r"^\s*declare\b.*?@" + _SYMBOL_PATTERN + r"\s*\(")
_CALLEE_RE = re.compile(r"@" + _SYMBOL_PATTERN + r"\s*\(")
_ALIAS_HEAD_RE = re.compile(r"^\s*@" + _SYMBOL_PATTERN + r"\s*=.*\balias\b")
_AT_SYMBOL_RE = re.compile(r"@" + _SYMBOL_PATTERN)
_HEX_ESCAPE_RE = re.compile(r"\\([0-9A-Fa-f]{2})")
_CALL_OPCODE_RE = re.compile(r"\b(callbr|invoke|call)\b")
_ASSIGNMENT_RE = re.compile(r"^\s*%[-$._A-Za-z0-9]+\s*=\s*(.*)$")


class EvalEffectError(RuntimeError):
    """Raised when an effect input or observation violates its Contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_symbol(raw: str) -> str:
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    return _HEX_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), raw)


def _matched_symbol(match: re.Match[str]) -> str:
    return _decode_symbol(match.group("symbol"))


@dataclass
class _FunctionFacts:
    name: str
    calls: Counter[str] = field(default_factory=Counter)
    indirect_call_site_count: int = 0
    inline_asm_call_site_count: int = 0
    exception_control_instruction_count: int = 0
    load_instruction_count: int = 0
    store_instruction_count: int = 0
    atomic_instruction_count: int = 0
    alloca_instruction_count: int = 0
    pointer_conversion_instruction_count: int = 0


@dataclass
class _IRModule:
    functions: dict[str, _FunctionFacts]
    declarations: set[str]
    aliases: dict[str, str]


def _instruction_opcode(line: str) -> str | None:
    code = line.split(";", 1)[0].strip()
    if not code or code.endswith(":") or code == "}":
        return None
    assignment = _ASSIGNMENT_RE.match(code)
    if assignment:
        code = assignment.group(1).lstrip()
    words = code.split()
    if not words:
        return None
    if words[0] in {"tail", "musttail", "notail"} and len(words) > 1:
        return words[1]
    return words[0]


def _record_instruction(facts: _FunctionFacts, line: str) -> None:
    code = line.split(";", 1)[0]
    opcode = _instruction_opcode(code)
    if opcode == "load":
        facts.load_instruction_count += 1
    elif opcode == "store":
        facts.store_instruction_count += 1
    elif opcode in {"atomicrmw", "cmpxchg", "fence"}:
        facts.atomic_instruction_count += 1
    elif opcode == "alloca":
        facts.alloca_instruction_count += 1
    elif opcode in {"inttoptr", "ptrtoint", "addrspacecast"}:
        facts.pointer_conversion_instruction_count += 1
    if opcode in {
        "invoke",
        "resume",
        "landingpad",
        "catchswitch",
        "catchpad",
        "catchret",
        "cleanuppad",
        "cleanupret",
    }:
        facts.exception_control_instruction_count += 1

    call_match = _CALL_OPCODE_RE.search(code)
    if call_match is None:
        return
    call_text = code[call_match.start() :]
    if re.search(r"\basm\b", call_text):
        facts.inline_asm_call_site_count += 1
        return
    callee = _CALLEE_RE.search(call_text)
    if callee is None:
        facts.indirect_call_site_count += 1
        return
    facts.calls[_matched_symbol(callee)] += 1


def _parse_ir(path: Path) -> _IRModule:
    if not path.is_file():
        raise EvalEffectError(f"LLVM IR input does not exist: {path}")
    functions: dict[str, _FunctionFacts] = {}
    declarations: set[str] = set()
    aliases: dict[str, str] = {}
    current: _FunctionFacts | None = None

    with path.open("r", encoding="utf-8", errors="strict") as stream:
        for line_number, line in enumerate(stream, start=1):
            if current is not None:
                if line.strip() == "}":
                    current = None
                else:
                    _record_instruction(current, line)
                continue

            definition = _DEFINE_RE.match(line)
            if definition is not None:
                name = _matched_symbol(definition)
                if name in functions:
                    raise EvalEffectError(
                        f"duplicate LLVM function definition {name!r} at line {line_number}"
                    )
                current = _FunctionFacts(name=name)
                functions[name] = current
                continue

            declaration = _DECLARE_RE.match(line)
            if declaration is not None:
                declarations.add(_matched_symbol(declaration))
                continue

            alias_head = _ALIAS_HEAD_RE.match(line)
            if alias_head is not None:
                symbols = [_matched_symbol(match) for match in _AT_SYMBOL_RE.finditer(line)]
                if len(symbols) >= 2:
                    aliases[symbols[0]] = symbols[-1]

    if current is not None:
        raise EvalEffectError(f"unterminated LLVM function {current.name!r}")
    if not functions:
        raise EvalEffectError("LLVM IR contains no function definitions")
    return _IRModule(
        functions=functions,
        declarations=declarations,
        aliases=aliases,
    )


def _resolve_alias(symbol: str, aliases: Mapping[str, str]) -> tuple[str, bool]:
    seen: set[str] = set()
    current = symbol
    while current in aliases:
        if current in seen:
            return current, False
        seen.add(current)
        current = aliases[current]
    return current, True


def _host_dependency_category(symbol: str) -> str | None:
    lower = symbol.lower()
    if "vldelayscheduler" in lower or "timeslot" in lower:
        return "scheduler"
    if any(
        marker in lower
        for marker in (
            "verilatedcontext",
            "verilatedmodel",
            "verilatedscope",
            "verilatedcovcontext",
            "vlcoroutine",
            "vldeleter",
            "vlprocess",
        )
    ) or symbol.startswith(("_ZN9Verilated", "_ZNK9Verilated")):
        return "runtime_context"
    if symbol.startswith(("_Z13sc_time_stamp", "_Z15vl_time_stamp")):
        return "host_time"
    if any(
        marker in lower
        for marker in ("vpi_", "svget", "svput", "dpi", "verilateddpi")
    ):
        return "dpi_vpi"
    if symbol.startswith(
        (
            "_Z10VL_STOP",
            "_Z11VL_FATAL",
            "_Z12VL_FINISH",
        )
    ) or symbol in {"abort", "exit", "_Exit", "quick_exit"}:
        return "termination"
    if symbol.startswith(("_Zn", "_Zdl")) or symbol in {
        "malloc",
        "calloc",
        "realloc",
        "free",
        "strdup",
    }:
        return "allocation"
    if symbol.startswith(
        (
            "_Z11VL_FCLOSE",
            "_Z11VL_FFLUSH",
            "_Z12VL_FFLUSH",
            "_Z12VL_READMEM",
            "_Z12VL_WRITEF",
            "_Z13VL_FWRITEF",
            "_Z13VL_SFORMAT",
            "_Z14VL_FOPEN",
        )
    ) or symbol in {
        "fclose",
        "fflush",
        "fopen",
        "fprintf",
        "fread",
        "fwrite",
        "printf",
        "puts",
    }:
        return "host_io"
    if symbol.startswith(
        (
            "__cxa_",
            "__gxx_",
            "_ZSt",
            "_ZNSt",
            "_ZTH",
            "pthread_",
        )
    ) or symbol in {"__cxa_atexit"}:
        return "host_runtime"
    return None


def _validate_string_array(value: Any, description: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise EvalEffectError(f"{description} must be an array of strings")
    if len(set(value)) != len(value):
        raise EvalEffectError(f"{description} must not contain duplicates")
    return list(value)


def validate_effect_contract(contract: Mapping[str, Any]) -> None:
    if contract.get("schema_version") != EFFECT_SCHEMA_VERSION:
        raise EvalEffectError("unsupported eval-effect contract schema_version")
    if contract.get("surface") != EFFECT_CONTRACT_SURFACE:
        raise EvalEffectError("unexpected eval-effect contract surface")
    if not isinstance(contract.get("target"), str) or not contract["target"]:
        raise EvalEffectError("eval-effect contract target must be a non-empty string")
    policy = contract.get("policy")
    if not isinstance(policy, Mapping):
        raise EvalEffectError("eval-effect contract policy must be an object")
    if not isinstance(policy.get("name"), str) or not policy["name"]:
        raise EvalEffectError("eval-effect policy name must be a non-empty string")
    if policy.get("classification_precedence") != _CLASSIFICATION_PRECEDENCE:
        raise EvalEffectError("eval-effect classification precedence is unsupported")
    _validate_string_array(
        policy.get("permitted_external_symbols", []),
        "permitted_external_symbols",
    )
    _validate_string_array(
        policy.get("permitted_external_prefixes", []),
        "permitted_external_prefixes",
    )
    regions = contract.get("regions")
    if not isinstance(regions, Mapping) or not regions:
        raise EvalEffectError("eval-effect contract regions must be a non-empty object")
    for name, region in regions.items():
        if not isinstance(name, str) or not name or not isinstance(region, Mapping):
            raise EvalEffectError("eval-effect region entries must be named objects")
        for key in ("input", "artifact_role", "entry"):
            if not isinstance(region.get(key), str) or not region[key]:
                raise EvalEffectError(f"eval-effect region {name!r} needs string {key}")
        if region.get("expected_classification") not in _CLASSIFICATIONS:
            raise EvalEffectError(
                f"eval-effect region {name!r} has unsupported expected classification"
            )


def _external_kind(
    symbol: str,
    *,
    permitted_symbols: set[str],
    permitted_prefixes: Sequence[str],
) -> tuple[str, str]:
    if symbol in permitted_symbols or any(
        symbol.startswith(prefix) for prefix in permitted_prefixes
    ):
        category = (
            "memory_intrinsic"
            if symbol.startswith(("llvm.memcpy", "llvm.memmove", "llvm.memset"))
            else "permitted_compiler_intrinsic"
        )
        return "permitted_external", category
    host_category = _host_dependency_category(symbol)
    if host_category is not None:
        return "host_dependency", host_category
    return "unknown_external", "unclassified_external"


def _reachable_functions(module: _IRModule, entry: str) -> set[str]:
    resolved_entry, alias_ok = _resolve_alias(entry, module.aliases)
    if not alias_ok:
        raise EvalEffectError(f"entry alias cycle for {entry!r}")
    if resolved_entry not in module.functions:
        raise EvalEffectError(f"eval entry {entry!r} has no LLVM definition")
    reachable: set[str] = set()
    pending = deque([resolved_entry])
    while pending:
        name = pending.popleft()
        if name in reachable:
            continue
        reachable.add(name)
        for called in module.functions[name].calls:
            target, alias_ok = _resolve_alias(called, module.aliases)
            if alias_ok and target in module.functions and target not in reachable:
                pending.append(target)
    return reachable


def _direct_call_rows(
    facts: _FunctionFacts,
    module: _IRModule,
    *,
    permitted_symbols: set[str],
    permitted_prefixes: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, site_count in sorted(facts.calls.items()):
        target, alias_ok = _resolve_alias(symbol, module.aliases)
        if not alias_ok:
            rows.append(
                {
                    "symbol": symbol,
                    "resolved_symbol": target,
                    "kind": "unknown_external",
                    "category": "alias_cycle",
                    "site_count": site_count,
                }
            )
            continue
        if target in module.functions:
            rows.append(
                {
                    "symbol": symbol,
                    "resolved_symbol": target,
                    "kind": "defined",
                    "site_count": site_count,
                }
            )
            continue
        kind, category = _external_kind(
            target,
            permitted_symbols=permitted_symbols,
            permitted_prefixes=permitted_prefixes,
        )
        rows.append(
            {
                "symbol": symbol,
                "resolved_symbol": target,
                "kind": kind,
                "category": category,
                "declaration_present": target in module.declarations,
                "site_count": site_count,
            }
        )
    return rows


def _direct_classification(row: Mapping[str, Any]) -> str:
    calls = row["calls"]
    if any(call["kind"] == "host_dependency" for call in calls):
        return "host_dependent"
    effects = row["effects"]
    if (
        any(call["kind"] == "unknown_external" for call in calls)
        or effects["indirect_call_site_count"]
        or effects["inline_asm_call_site_count"]
        or effects["exception_control_instruction_count"]
    ):
        return "unknown"
    return "proven_device_clean"


def _classify_function_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    by_name = {row["name"]: row for row in rows}
    classes = {name: _direct_classification(row) for name, row in by_name.items()}
    changed = True
    while changed:
        changed = False
        for name in sorted(by_name):
            row = by_name[name]
            callees = [
                call["resolved_symbol"]
                for call in row["calls"]
                if call["kind"] == "defined"
            ]
            candidate = classes[name]
            if candidate != "host_dependent" and any(
                classes.get(callee) == "host_dependent" for callee in callees
            ):
                candidate = "host_dependent"
            elif candidate == "proven_device_clean" and any(
                classes.get(callee) == "unknown" for callee in callees
            ):
                candidate = "unknown"
            if candidate != classes[name]:
                classes[name] = candidate
                changed = True
    return classes


def _classification_reason(
    row: Mapping[str, Any], classes: Mapping[str, str]
) -> dict[str, Any]:
    host_calls = [call for call in row["calls"] if call["kind"] == "host_dependency"]
    if host_calls:
        call = host_calls[0]
        return {
            "kind": "direct_host_dependency",
            "symbol": call["resolved_symbol"],
            "category": call["category"],
        }
    host_callees = sorted(
        call["resolved_symbol"]
        for call in row["calls"]
        if call["kind"] == "defined"
        and classes[call["resolved_symbol"]] == "host_dependent"
    )
    if host_callees:
        return {"kind": "transitive_host_dependency", "callee": host_callees[0]}
    unknown_calls = [call for call in row["calls"] if call["kind"] == "unknown_external"]
    if unknown_calls:
        call = unknown_calls[0]
        return {
            "kind": "unknown_external",
            "symbol": call["resolved_symbol"],
            "category": call["category"],
        }
    effects = row["effects"]
    for key, reason_kind in (
        ("indirect_call_site_count", "indirect_call"),
        ("inline_asm_call_site_count", "inline_asm_call"),
        ("exception_control_instruction_count", "exception_control"),
    ):
        if effects[key]:
            return {"kind": reason_kind, "count": effects[key]}
    unknown_callees = sorted(
        call["resolved_symbol"]
        for call in row["calls"]
        if call["kind"] == "defined"
        and classes[call["resolved_symbol"]] == "unknown"
    )
    if unknown_callees:
        return {"kind": "transitive_unknown", "callee": unknown_callees[0]}
    return {"kind": "closed_over_defined_functions_and_permitted_externals"}


def _build_function_rows(
    module: _IRModule,
    reachable: set[str],
    *,
    permitted_symbols: set[str],
    permitted_prefixes: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in sorted(reachable):
        facts = module.functions[name]
        calls = _direct_call_rows(
            facts,
            module,
            permitted_symbols=permitted_symbols,
            permitted_prefixes=permitted_prefixes,
        )
        memory_intrinsic_calls = sum(
            call["site_count"]
            for call in calls
            if call.get("category") == "memory_intrinsic"
        )
        rows.append(
            {
                "name": name,
                "calls": calls,
                "effects": {
                    "load_instruction_count": facts.load_instruction_count,
                    "store_instruction_count": facts.store_instruction_count,
                    "atomic_instruction_count": facts.atomic_instruction_count,
                    "alloca_instruction_count": facts.alloca_instruction_count,
                    "pointer_conversion_instruction_count": (
                        facts.pointer_conversion_instruction_count
                    ),
                    "memory_intrinsic_call_site_count": memory_intrinsic_calls,
                    "indirect_call_site_count": facts.indirect_call_site_count,
                    "inline_asm_call_site_count": facts.inline_asm_call_site_count,
                    "exception_control_instruction_count": (
                        facts.exception_control_instruction_count
                    ),
                },
            }
        )
    classes = _classify_function_rows(rows)
    for row in rows:
        row["classification"] = classes[row["name"]]
        row["reason"] = _classification_reason(row, classes)
    return rows


def _region_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    classifications = Counter(row["classification"] for row in rows)
    call_rows = [call for row in rows for call in row["calls"]]
    return {
        "reachable_function_count": len(rows),
        "proven_device_clean_function_count": classifications[
            "proven_device_clean"
        ],
        "host_dependent_function_count": classifications["host_dependent"],
        "unknown_function_count": classifications["unknown"],
        "direct_call_edge_count": sum(len(row["calls"]) for row in rows),
        "direct_call_site_count": sum(call["site_count"] for call in call_rows),
        "defined_call_site_count": sum(
            call["site_count"] for call in call_rows if call["kind"] == "defined"
        ),
        "permitted_external_call_site_count": sum(
            call["site_count"]
            for call in call_rows
            if call["kind"] == "permitted_external"
        ),
        "host_dependency_call_site_count": sum(
            call["site_count"]
            for call in call_rows
            if call["kind"] == "host_dependency"
        ),
        "unknown_external_call_site_count": sum(
            call["site_count"]
            for call in call_rows
            if call["kind"] == "unknown_external"
        ),
        **{
            key: sum(row["effects"][key] for row in rows)
            for key in (
                "load_instruction_count",
                "store_instruction_count",
                "atomic_instruction_count",
                "alloca_instruction_count",
                "pointer_conversion_instruction_count",
                "memory_intrinsic_call_site_count",
                "indirect_call_site_count",
                "inline_asm_call_site_count",
                "exception_control_instruction_count",
            )
        },
    }


def _host_dependencies(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        for call in row["calls"]:
            if call["kind"] != "host_dependency":
                continue
            key = (call["resolved_symbol"], call["category"])
            group = grouped.setdefault(
                key,
                {
                    "symbol": key[0],
                    "category": key[1],
                    "call_site_count": 0,
                    "callers": [],
                },
            )
            group["call_site_count"] += call["site_count"]
            group["callers"].append(row["name"])
    for group in grouped.values():
        group["callers"].sort()
    return [grouped[key] for key in sorted(grouped)]


def _unknown_effects(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        for call in row["calls"]:
            if call["kind"] == "unknown_external":
                result.append(
                    {
                        "kind": "unknown_external",
                        "function": row["name"],
                        "symbol": call["resolved_symbol"],
                        "category": call["category"],
                        "site_count": call["site_count"],
                    }
                )
        for key, kind in (
            ("indirect_call_site_count", "indirect_call"),
            ("inline_asm_call_site_count", "inline_asm_call"),
            ("exception_control_instruction_count", "exception_control"),
        ):
            count = row["effects"][key]
            if count:
                result.append(
                    {"kind": kind, "function": row["name"], "site_count": count}
                )
    return sorted(
        result,
        key=lambda item: (
            item["kind"],
            item["function"],
            item.get("symbol", ""),
        ),
    )


def _region_core(region: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: region[key]
        for key in (
            "name",
            "input",
            "artifact_role",
            "artifact_sha256",
            "artifact_bytes",
            "entry",
            "classification",
            "expected_classification",
            "expectation_met",
            "metrics",
            "host_dependencies",
            "unknown_effects",
            "functions",
        )
    }


def _observation_core(observation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: observation[key]
        for key in (
            "schema_version",
            "surface",
            "producer",
            "target",
            "contract_sha256",
            "policy",
            "input_artifacts",
            "region_count",
            "classification_counts",
            "regions",
        )
    }


def _build_region(
    *,
    name: str,
    specification: Mapping[str, Any],
    module: _IRModule,
    artifact: Mapping[str, Any],
    permitted_symbols: set[str],
    permitted_prefixes: Sequence[str],
) -> dict[str, Any]:
    entry = specification["entry"]
    resolved_entry, alias_ok = _resolve_alias(entry, module.aliases)
    if not alias_ok:
        raise EvalEffectError(f"entry alias cycle for {entry!r}")
    reachable = _reachable_functions(module, entry)
    rows = _build_function_rows(
        module,
        reachable,
        permitted_symbols=permitted_symbols,
        permitted_prefixes=permitted_prefixes,
    )
    by_name = {row["name"]: row for row in rows}
    classification = by_name[resolved_entry]["classification"]
    expected = specification["expected_classification"]
    region: dict[str, Any] = {
        "name": name,
        "input": specification["input"],
        "artifact_role": specification["artifact_role"],
        "artifact_sha256": artifact["sha256"],
        "artifact_bytes": artifact["bytes"],
        "entry": entry,
        "classification": classification,
        "expected_classification": expected,
        "expectation_met": classification == expected,
        "metrics": _region_metrics(rows),
        "host_dependencies": _host_dependencies(rows),
        "unknown_effects": _unknown_effects(rows),
        "functions": rows,
    }
    region["closure_fingerprint"] = _sha256_bytes(
        _canonical_bytes(_region_core(region))
    )
    return region


def _validate_oracle(oracle: Mapping[str, Any]) -> None:
    if oracle.get("schema_version") != EFFECT_SCHEMA_VERSION:
        raise EvalEffectError("unsupported eval-effect oracle schema_version")
    if oracle.get("surface") != EFFECT_ORACLE_SURFACE:
        raise EvalEffectError("unexpected eval-effect oracle surface")
    for key in ("target", "contract_sha256", "observation_fingerprint"):
        if not isinstance(oracle.get(key), str):
            raise EvalEffectError(f"eval-effect oracle {key} must be a string")
    regions = oracle.get("regions")
    if not isinstance(regions, Mapping):
        raise EvalEffectError("eval-effect oracle regions must be an object")


def _oracle_issues(
    observation: Mapping[str, Any], oracle: Mapping[str, Any]
) -> list[str]:
    _validate_oracle(oracle)
    issues: list[str] = []
    for key in ("target", "contract_sha256", "observation_fingerprint"):
        if oracle.get(key) != observation.get(key):
            issues.append(f"oracle_{key}_mismatch")
    actual_regions = {row["name"]: row for row in observation["regions"]}
    oracle_regions = oracle["regions"]
    if set(actual_regions) != set(oracle_regions):
        issues.append("oracle_region_names_mismatch")
    for name in sorted(set(actual_regions) & set(oracle_regions)):
        expected = oracle_regions[name]
        if not isinstance(expected, Mapping):
            raise EvalEffectError(f"eval-effect oracle region {name!r} must be an object")
        actual = actual_regions[name]
        for key in (
            "artifact_sha256",
            "entry",
            "classification",
            "closure_fingerprint",
        ):
            if expected.get(key) != actual.get(key):
                issues.append(f"oracle_region_{name}_{key}_mismatch")
        expected_metrics = expected.get("metrics")
        if not isinstance(expected_metrics, Mapping):
            raise EvalEffectError(
                f"eval-effect oracle region {name!r} metrics must be an object"
            )
        for metric in sorted(expected_metrics):
            if expected_metrics[metric] != actual["metrics"].get(metric):
                issues.append(f"oracle_region_{name}_metric_{metric}_mismatch")
    return issues


def classify_eval_effects(
    *,
    ir_inputs: Mapping[str, Path],
    contract: Mapping[str, Any],
    producer: str,
    oracle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify Contract-selected LLVM eval closures without invoking tools."""

    validate_effect_contract(contract)
    if not isinstance(producer, str) or not producer:
        raise EvalEffectError("producer must be a non-empty string")
    required_inputs = sorted(
        {region["input"] for region in contract["regions"].values()}
    )
    missing = [name for name in required_inputs if name not in ir_inputs]
    extra = sorted(set(ir_inputs) - set(required_inputs))
    if missing:
        raise EvalEffectError(f"missing LLVM IR inputs: {', '.join(missing)}")
    if extra:
        raise EvalEffectError(f"undeclared LLVM IR inputs: {', '.join(extra)}")

    modules: dict[str, _IRModule] = {}
    artifacts: dict[str, dict[str, Any]] = {}
    for name in required_inputs:
        path = Path(ir_inputs[name])
        modules[name] = _parse_ir(path)
        artifacts[name] = {
            "name": name,
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }

    policy = contract["policy"]
    permitted_symbols = set(policy.get("permitted_external_symbols", []))
    permitted_prefixes = list(policy.get("permitted_external_prefixes", []))
    regions = [
        _build_region(
            name=name,
            specification=contract["regions"][name],
            module=modules[contract["regions"][name]["input"]],
            artifact=artifacts[contract["regions"][name]["input"]],
            permitted_symbols=permitted_symbols,
            permitted_prefixes=permitted_prefixes,
        )
        for name in sorted(contract["regions"])
    ]
    counts = Counter(region["classification"] for region in regions)
    observation: dict[str, Any] = {
        "schema_version": EFFECT_SCHEMA_VERSION,
        "surface": EFFECT_OBSERVATION_SURFACE,
        "producer": producer,
        "target": contract["target"],
        "contract_sha256": _sha256_bytes(_canonical_bytes(contract)),
        "policy": {
            "name": policy["name"],
            "classification_precedence": list(_CLASSIFICATION_PRECEDENCE),
            "permitted_external_symbols": sorted(permitted_symbols),
            "permitted_external_prefixes": sorted(permitted_prefixes),
        },
        "input_artifacts": [artifacts[name] for name in required_inputs],
        "region_count": len(regions),
        "classification_counts": {
            classification: counts[classification]
            for classification in _CLASSIFICATION_PRECEDENCE
        },
        "regions": regions,
    }
    observation["observation_fingerprint"] = _sha256_bytes(
        _canonical_bytes(_observation_core(observation))
    )
    issues = [
        f"region_{region['name']}_expected_classification_mismatch"
        for region in regions
        if not region["expectation_met"]
    ]
    if oracle is not None:
        issues.extend(_oracle_issues(observation, oracle))
        observation["oracle_sha256"] = _sha256_bytes(_canonical_bytes(oracle))
    observation["issues"] = issues
    observation["status"] = (
        "mismatch" if issues else "verified" if oracle is not None else "resolved"
    )
    validate_eval_effects(observation)
    return observation


def _recompute_region(region: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    functions = region["functions"]
    rows = [dict(row) for row in functions]
    classes = _classify_function_rows(rows)
    entry = region["entry"]
    if entry not in classes:
        raise EvalEffectError(f"eval-effect entry {entry!r} is absent from closure")
    return classes[entry], _region_metrics(rows)


def validate_eval_effects(observation: Mapping[str, Any]) -> None:
    if observation.get("schema_version") != EFFECT_SCHEMA_VERSION:
        raise EvalEffectError("unsupported eval-effect observation schema_version")
    if observation.get("surface") != EFFECT_OBSERVATION_SURFACE:
        raise EvalEffectError("unexpected eval-effect observation surface")
    for key in ("producer", "target", "contract_sha256", "observation_fingerprint"):
        if not isinstance(observation.get(key), str) or not observation[key]:
            raise EvalEffectError(f"eval-effect observation {key} must be a string")
    policy = observation.get("policy")
    if not isinstance(policy, Mapping):
        raise EvalEffectError("eval-effect observation policy must be an object")
    if policy.get("classification_precedence") != _CLASSIFICATION_PRECEDENCE:
        raise EvalEffectError("eval-effect observation precedence is unsupported")
    _validate_string_array(
        policy.get("permitted_external_symbols"),
        "observation permitted_external_symbols",
    )
    _validate_string_array(
        policy.get("permitted_external_prefixes"),
        "observation permitted_external_prefixes",
    )
    artifacts = observation.get("input_artifacts")
    if not isinstance(artifacts, list):
        raise EvalEffectError("eval-effect input_artifacts must be an array")
    artifact_names: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise EvalEffectError("eval-effect input artifact must be an object")
        if (
            not isinstance(artifact.get("name"), str)
            or not isinstance(artifact.get("bytes"), int)
            or artifact["bytes"] <= 0
            or not isinstance(artifact.get("sha256"), str)
        ):
            raise EvalEffectError("eval-effect input artifact identity is invalid")
        artifact_names.append(artifact["name"])
    if artifact_names != sorted(set(artifact_names)):
        raise EvalEffectError("eval-effect input artifacts must be uniquely sorted")

    regions = observation.get("regions")
    if not isinstance(regions, list):
        raise EvalEffectError("eval-effect regions must be an array")
    if observation.get("region_count") != len(regions):
        raise EvalEffectError("eval-effect region_count does not match regions")
    region_names = [row.get("name") for row in regions if isinstance(row, Mapping)]
    if len(region_names) != len(regions) or region_names != sorted(set(region_names)):
        raise EvalEffectError("eval-effect region names must be uniquely sorted strings")
    observed_counts: Counter[str] = Counter()
    for region in regions:
        if not isinstance(region, Mapping):
            raise EvalEffectError("eval-effect region must be an object")
        name = region["name"]
        if region.get("classification") not in _CLASSIFICATIONS:
            raise EvalEffectError(f"region {name!r} has invalid classification")
        if region.get("expected_classification") not in _CLASSIFICATIONS:
            raise EvalEffectError(f"region {name!r} has invalid expected classification")
        if region.get("expectation_met") != (
            region["classification"] == region["expected_classification"]
        ):
            raise EvalEffectError(f"region {name!r} expectation flag is inconsistent")
        functions = region.get("functions")
        if not isinstance(functions, list) or not functions:
            raise EvalEffectError(f"region {name!r} functions must be non-empty")
        function_names = [
            function.get("name") for function in functions if isinstance(function, Mapping)
        ]
        if (
            len(function_names) != len(functions)
            or function_names != sorted(set(function_names))
        ):
            raise EvalEffectError(f"region {name!r} function names must be uniquely sorted")
        function_set = set(function_names)
        for function in functions:
            calls = function.get("calls")
            effects = function.get("effects")
            if not isinstance(calls, list) or not isinstance(effects, Mapping):
                raise EvalEffectError(f"region {name!r} function facts are invalid")
            for call in calls:
                if not isinstance(call, Mapping) or call.get("kind") not in {
                    "defined",
                    "permitted_external",
                    "host_dependency",
                    "unknown_external",
                }:
                    raise EvalEffectError(f"region {name!r} call fact is invalid")
                if (
                    not isinstance(call.get("resolved_symbol"), str)
                    or not isinstance(call.get("site_count"), int)
                    or call["site_count"] <= 0
                ):
                    raise EvalEffectError(f"region {name!r} call identity is invalid")
                if call["kind"] == "defined" and call["resolved_symbol"] not in function_set:
                    raise EvalEffectError(
                        f"region {name!r} defined callee is absent from closure"
                    )
            for effect_name, value in effects.items():
                if not effect_name.endswith("_count") or not isinstance(value, int) or value < 0:
                    raise EvalEffectError(f"region {name!r} effect count is invalid")
        recalculated_classification, recalculated_metrics = _recompute_region(region)
        recalculated_classes = _classify_function_rows(
            [dict(function) for function in functions]
        )
        for function in functions:
            expected_function_class = recalculated_classes[function["name"]]
            if function.get("classification") != expected_function_class:
                raise EvalEffectError(
                    f"region {name!r} function classification is inconsistent"
                )
            expected_reason = _classification_reason(
                function, recalculated_classes
            )
            if function.get("reason") != expected_reason:
                raise EvalEffectError(
                    f"region {name!r} function reason is inconsistent"
                )
        if region["classification"] != recalculated_classification:
            raise EvalEffectError(f"region {name!r} classification is inconsistent")
        if region.get("metrics") != recalculated_metrics:
            raise EvalEffectError(f"region {name!r} metrics are inconsistent")
        if region.get("host_dependencies") != _host_dependencies(functions):
            raise EvalEffectError(f"region {name!r} host dependencies are inconsistent")
        if region.get("unknown_effects") != _unknown_effects(functions):
            raise EvalEffectError(f"region {name!r} unknown effects are inconsistent")
        expected_fingerprint = _sha256_bytes(_canonical_bytes(_region_core(region)))
        if region.get("closure_fingerprint") != expected_fingerprint:
            raise EvalEffectError(f"region {name!r} closure fingerprint is inconsistent")
        observed_counts[region["classification"]] += 1
    expected_counts = {
        classification: observed_counts[classification]
        for classification in _CLASSIFICATION_PRECEDENCE
    }
    if observation.get("classification_counts") != expected_counts:
        raise EvalEffectError("eval-effect classification_counts are inconsistent")
    expected_observation_fingerprint = _sha256_bytes(
        _canonical_bytes(_observation_core(observation))
    )
    if observation["observation_fingerprint"] != expected_observation_fingerprint:
        raise EvalEffectError("eval-effect observation fingerprint is inconsistent")
    issues = observation.get("issues")
    if not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues):
        raise EvalEffectError("eval-effect issues must be an array of strings")
    if "oracle_sha256" in observation and not isinstance(
        observation["oracle_sha256"], str
    ):
        raise EvalEffectError("eval-effect oracle_sha256 must be a string")
    expected_status = (
        "mismatch"
        if issues
        else "verified"
        if "oracle_sha256" in observation
        else "resolved"
    )
    if observation.get("status") != expected_status:
        raise EvalEffectError("eval-effect status is inconsistent")
    serialized = json.dumps(observation, ensure_ascii=False)
    if re.search(r'(?<![A-Za-z0-9_])/(?:home|tmp)/', serialized):
        raise EvalEffectError("eval-effect observation contains a local absolute path")


def write_eval_effects(path: Path, observation: Mapping[str, Any]) -> None:
    validate_eval_effects(observation)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(observation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
