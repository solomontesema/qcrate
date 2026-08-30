#!/usr/bin/env python3
"""Focused QCRUN/QIDX reader tests."""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path


ANALYZER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ANALYZER_DIR))

import qcrate_run  # noqa: E402


def index_header(run_id: int = 0x12, stream_id: int = 3) -> bytes:
    return struct.pack("!IHHH6xQI4x", 0x51494458, 1, 32, 128, run_id, stream_id)


def index_record(
    shot_id: int,
    *,
    state: int = 1,
    issues: int = 0,
    offset: int = 0,
    sample_bytes: int = 16,
) -> bytes:
    if state == 2:
        offset = (1 << 64) - 1
        sample_bytes = 0
    return struct.pack(
        "!QIIQQQQQQQIIIIIIIIIHHQ8x",
        shot_id,
        state,
        issues,
        0,
        5,
        shot_id * 1000,
        100,
        200,
        offset,
        sample_bytes,
        1,
        4,
        1,
        3,
        0,
        0,
        0,
        0x12345678,
        0,
        2,
        4,
        0x55,
    )


class RunReaderTests(unittest.TestCase):
    def make_run(self, root: Path) -> None:
        root.mkdir()
        (root / "samples.iq16").write_bytes(bytes(range(32)))
        (root / "shots.qidx").write_bytes(
            index_header()
            + index_record(1)
            + index_record(2, state=2, issues=2)
            + index_record(3, offset=16)
        )
        (root / "run.json").write_text(json.dumps({
            "format": "qcrate-run-v1",
            "complete": False,
            "run_id": "0x0000000000000012",
            "stream_id": "0x00000003",
            "stream": {
                "sample_rate_numerator": 12_500_000,
                "sample_rate_denominator": 1,
            },
        }))

    def test_loads_complete_and_incomplete_shots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            self.make_run(root)
            bundle = qcrate_run.RunBundle.open(root)
            self.assertEqual(len(bundle.shots), 3)
            self.assertEqual(len(bundle.complete_shots), 2)
            self.assertEqual(bundle.incomplete_shots[0].issue_names, ("missing",))
            self.assertEqual(bundle.read_samples(bundle.shots[2]), bytes(range(16, 32)))
            self.assertEqual(bundle.sample_rate_hz(), (12_500_000.0, "run metadata"))

    def test_rejects_extent_beyond_sample_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            self.make_run(root)
            (root / "samples.iq16").write_bytes(b"short")
            with self.assertRaisesRegex(ValueError, "exceeds"):
                qcrate_run.RunBundle.open(root)

    def test_old_manifest_uses_explicit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            self.make_run(root)
            manifest = json.loads((root / "run.json").read_text())
            manifest.pop("stream")
            (root / "run.json").write_text(json.dumps(manifest))
            bundle = qcrate_run.RunBundle.open(root)
            self.assertEqual(bundle.sample_rate_hz(12_500_000), (12_500_000.0, "fallback"))


if __name__ == "__main__":
    unittest.main()
