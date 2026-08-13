"""Version boundary for Verilator semantic artifacts."""

from __future__ import annotations


SUPPORTED_SEMANTIC_PRODUCER_PREFIXES = (
    "Verilator 5.050 ",
    "Verilator 5.051 devel ",
)


def is_supported_semantic_producer(producer: str) -> bool:
    """Return whether ``producer`` belongs to a validated semantic format family."""

    return producer.startswith(SUPPORTED_SEMANTIC_PRODUCER_PREFIXES)


def supported_semantic_producers_description() -> str:
    """Return a stable user-facing description of accepted producer families."""

    return "5.050 or 5.051 devel"
