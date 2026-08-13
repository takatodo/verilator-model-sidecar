from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, (ROOT / "src").as_posix())

from verilator_model_sidecar.producer import (  # noqa: E402
    is_supported_semantic_producer,
)


class ProducerVersionTest(unittest.TestCase):
    def test_accepts_pinned_release_and_manifest_fork_families(self) -> None:
        self.assertTrue(
            is_supported_semantic_producer(
                "Verilator 5.050 2026-07-01 rev v5.050"
            )
        )
        self.assertTrue(
            is_supported_semantic_producer(
                "Verilator 5.051 devel rev vUNKNOWN-built20260813-750f8a4f7 (mod)"
            )
        )

    def test_rejects_unvalidated_family(self) -> None:
        self.assertFalse(
            is_supported_semantic_producer("Verilator 5.052 devel rev vUNKNOWN")
        )


if __name__ == "__main__":
    unittest.main()
