"""Verilator model sidecar public package."""

from .coverage import (
    COVERAGE_CONTRACT_SURFACE,
    COVERAGE_ORACLE_SURFACE,
    CoverageMappingError,
    build_toggle_coverage_mapping,
    coverage_region_contracts,
    validate_coverage_mapping,
)

from .effects import (
    EFFECT_CONTRACT_SURFACE,
    EFFECT_OBSERVATION_SURFACE,
    EFFECT_ORACLE_SURFACE,
    EvalEffectError,
    classify_eval_effects,
    validate_effect_contract,
    validate_eval_effects,
    write_eval_effects,
)

from .physical import (
    LAYOUT_OBSERVATION_SURFACE,
    PhysicalProbeError,
    probe_physical_layout,
    validate_layout_observation,
    write_layout_observation,
)

from .native import (
    NATIVE_MANIFEST_SCHEMA_VERSION,
    NATIVE_MANIFEST_SURFACE,
    NATIVE_VERIFICATION_SCHEMA_VERSION,
    NATIVE_VERIFICATION_SURFACE,
    NativeManifestError,
    validate_native_manifest,
    verify_native_adapter,
    write_native_verification,
)

from .semantic import (
    MANIFEST_SCHEMA_VERSION,
    MANIFEST_SURFACE,
    SidecarError,
    analyze_manifest,
    capture_manifest,
    extract_semantic_projection,
    resolve_physical_bindings,
    validate_manifest,
    verify_adapter_semantics,
    write_manifest,
)

__all__ = [
    "COVERAGE_CONTRACT_SURFACE",
    "COVERAGE_ORACLE_SURFACE",
    "EFFECT_CONTRACT_SURFACE",
    "EFFECT_OBSERVATION_SURFACE",
    "EFFECT_ORACLE_SURFACE",
    "MANIFEST_SCHEMA_VERSION",
    "MANIFEST_SURFACE",
    "LAYOUT_OBSERVATION_SURFACE",
    "NATIVE_MANIFEST_SCHEMA_VERSION",
    "NATIVE_MANIFEST_SURFACE",
    "NATIVE_VERIFICATION_SCHEMA_VERSION",
    "NATIVE_VERIFICATION_SURFACE",
    "NativeManifestError",
    "PhysicalProbeError",
    "CoverageMappingError",
    "EvalEffectError",
    "SidecarError",
    "analyze_manifest",
    "capture_manifest",
    "build_toggle_coverage_mapping",
    "classify_eval_effects",
    "coverage_region_contracts",
    "extract_semantic_projection",
    "probe_physical_layout",
    "resolve_physical_bindings",
    "validate_layout_observation",
    "validate_coverage_mapping",
    "validate_effect_contract",
    "validate_eval_effects",
    "validate_manifest",
    "validate_native_manifest",
    "verify_adapter_semantics",
    "verify_native_adapter",
    "write_manifest",
    "write_layout_observation",
    "write_native_verification",
    "write_eval_effects",
]

__version__ = "0.1.0"
