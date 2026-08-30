#!/usr/bin/env python3
"""Numerical and headless-render tests for qcrate-analyzer."""
from __future__ import annotations

import binascii
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


ANALYZER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ANALYZER_DIR))

import qcrate_analyzer as analyzer  # noqa: E402
import qcrate_run  # noqa: E402


def make_iq_run(root: Path) -> None:
    sample_rate = 12_500_000
    count = 1000
    indexes = np.arange(count)
    values = 0.25 * np.exp(2j * np.pi * 1_000_000 * indexes / sample_rate)
    i = np.rint(values.real * 32768).astype(np.int16)
    q = np.rint(values.imag * 32768).astype(np.int16)
    words = i.astype(np.uint16).astype(np.uint32) | (
        q.astype(np.uint16).astype(np.uint32) << 16
    )
    payload = words.astype("<u4").tobytes()
    root.mkdir()
    (root / "samples.iq16").write_bytes(payload)
    header = struct.pack("!IHHH6xQI4x", 0x51494458, 1, 32, 128, 0x12, 3)
    record = struct.pack(
        "!QIIQQQQQQQIIIIIIIIIHHQ8x",
        7,
        1,
        0,
        0,
        5,
        123456,
        100,
        200,
        0,
        len(payload),
        1,
        count,
        3,
        5,
        0,
        0,
        0,
        binascii.crc32(payload) & 0xFFFFFFFF,
        0,
        2,
        4,
        0x55,
    )
    (root / "shots.qidx").write_bytes(header + record)
    (root / "run.json").write_text(json.dumps({
        "format": "qcrate-run-v1",
        "complete": True,
        "run_id": "0x12",
        "stream_id": "0x3",
        "stream": {
            "sample_rate_numerator": sample_rate,
            "sample_rate_denominator": 1,
        },
    }))


class AnalyzerTests(unittest.TestCase):
    def test_analyzes_recorded_iq_tone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            make_iq_run(root)
            bundle = qcrate_run.RunBundle.open(root)
            result = analyzer.analyze_shot(bundle, bundle.shots[0])
        self.assertAlmostEqual(result.dominant_frequency_hz, 1_000_000, places=6)
        self.assertEqual(result.file_crc32, result.shot.payload_crc32)
        self.assertIsNone(result.reference_mismatches)

    def test_writes_headless_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            output = Path(directory) / "shot.png"
            make_iq_run(root)
            analyzer.write_snapshot(root, output, shot_id=7,
                                    fallback_rate=12_500_000)
            self.assertGreater(output.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
