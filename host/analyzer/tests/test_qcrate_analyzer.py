#!/usr/bin/env python3
"""Numerical and headless-render tests for qcrate-analyzer."""
from __future__ import annotations

import binascii
import copy
import json
import shutil
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


def make_iq_run(
    root: Path,
    *,
    payload: bytes | None = None,
    config_id: int = 0x55,
    frame_count: int = 1,
    frame_samples: int = 1000,
) -> None:
    sample_rate = 12_500_000
    count = frame_count * frame_samples
    if payload is None:
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
        frame_count,
        frame_samples,
        3,
        5,
        0,
        0,
        0,
        binascii.crc32(payload) & 0xFFFFFFFF,
        0,
        2,
        4,
        config_id,
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

    def test_bundled_runtime_profile_selects_bit_exact_model(self) -> None:
        resolved = (
            analyzer.PROFILE_DIR / "examples" / "resolved" /
            "lo_28_5mhz.resolved.json"
        )
        document = json.loads(resolved.read_text(encoding="ascii"))
        config_id = int(document["identity"]["dsp_config_id"], 0)
        words = analyzer.deployed_reference.generate_words(
            resolved, analyzer.dsp.TABLE_DIR, 4096
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            make_iq_run(
                root, payload=np.asarray(words, dtype="<u4").tobytes(),
                config_id=config_id, frame_count=4, frame_samples=1024,
            )
            shutil.copyfile(resolved, root / analyzer.RUN_PROFILE_NAME)
            bundle = qcrate_run.RunBundle.open(root)
            result = analyzer.analyze_shot(bundle, bundle.shots[0])
        self.assertEqual(result.reference_mismatches, 0)

    def test_tampered_bundled_profile_is_rejected(self) -> None:
        resolved = (
            analyzer.PROFILE_DIR / "examples" / "resolved" /
            "lo_29mhz.resolved.json"
        )
        document = json.loads(resolved.read_text(encoding="ascii"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            make_iq_run(root)
            tampered = copy.deepcopy(document)
            tampered["dsp"]["active"]["LO_PHASE_INCREMENT"] += 1
            (root / analyzer.RUN_PROFILE_NAME).write_text(json.dumps(tampered))
            bundle = qcrate_run.RunBundle.open(root)
            with self.assertRaises(ValueError):
                analyzer.analyze_shot(bundle, bundle.shots[0])


if __name__ == "__main__":
    unittest.main()
