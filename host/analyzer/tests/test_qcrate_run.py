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
            health = bundle.health()
            self.assertEqual(health.state, "failed")
            self.assertEqual(health.complete_shots, 2)
            self.assertEqual(health.incomplete_shots, 1)
            self.assertFalse(health.integrity_ok)
            with qcrate_run.RunIndex.open(root) as catalog:
                self.assertEqual(catalog.health(), health)
                self.assertEqual(
                    catalog.read_samples(catalog.record_at(2)), bytes(range(16, 32))
                )

    def test_live_reader_accepts_index_before_first_datagram(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            root.mkdir()
            (root / "samples.iq16").write_bytes(b"")
            (root / "shots.qidx").write_bytes(b"")
            bundle = qcrate_run.RunBundle.open(root, allow_in_progress=True)
            self.assertEqual((bundle.run_id, bundle.stream_id), (0, 0))
            self.assertEqual(bundle.shots, ())
            self.assertEqual(bundle.health().state, "recording")
            with self.assertRaisesRegex(ValueError, "header is truncated"):
                qcrate_run.RunBundle.open(root)

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

    def test_loads_identity_matched_sender_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            self.make_run(root)
            (root / "sender.json").write_text(json.dumps({
                "format": "qcrate-sender-report-v1",
                "complete": True,
                "run_id": "0x12",
                "stream_id": "0x3",
                "timing": {"process_cpu_percent": 4.25},
                "health": {
                    "token_queue_high_water": 2,
                    "dma_ready_high_water": 1,
                    "dma_stall_cycles": 7,
                    "missed_triggers": 0,
                    "starvation_events": 0,
                    "skipped_triggers": 0,
                    "dma_error_events": 0,
                },
            }))
            health = qcrate_run.RunBundle.open(root).health()
            self.assertEqual(health.token_queue_high_water, 2)
            self.assertEqual(health.dma_stall_cycles, 7)
            self.assertEqual(health.sender_cpu_percent, 4.25)

    def test_random_access_catalog_refreshes_only_committed_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            root.mkdir()
            (root / "samples.iq16").write_bytes(bytes(range(48)))
            initial = [
                index_record(shot_id, offset=(shot_id - 1) * 16)
                for shot_id in range(1, 3)
            ]
            index_path = root / "shots.qidx"
            index_path.write_bytes(index_header() + b"".join(initial))
            with qcrate_run.RunIndex.open(root, cache_records=4) as catalog:
                self.assertEqual(catalog.record_count, 2)
                self.assertEqual(catalog.complete_count, 2)
                self.assertEqual(catalog.record_at(1).shot_id, 2)
                self.assertEqual(catalog.find_shot_id(2), 1)
                self.assertIsNone(catalog.find_shot_id(9))

                third = index_record(3, offset=32)
                with index_path.open("ab") as stream:
                    stream.write(third[:17])
                self.assertFalse(catalog.refresh())
                self.assertEqual(catalog.record_count, 2)
                with index_path.open("ab") as stream:
                    stream.write(third[17:])
                self.assertTrue(catalog.refresh())
                self.assertEqual(catalog.record_count, 3)
                self.assertEqual(catalog.record_at(2).shot_id, 3)
                self.assertEqual(catalog.health().complete_shots, 3)

    def test_large_catalog_keeps_a_bounded_record_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            root.mkdir()
            count = 20_000
            samples = root / "samples.iq16"
            with samples.open("wb") as stream:
                stream.truncate(count * 4)
            with (root / "shots.qidx").open("wb") as stream:
                stream.write(index_header())
                for ordinal in range(count):
                    stream.write(index_record(
                        ordinal + 100, offset=ordinal * 4, sample_bytes=4
                    ))
            with qcrate_run.RunIndex.open(root, cache_records=32) as catalog:
                self.assertEqual(catalog.record_count, count)
                self.assertEqual(catalog.complete_count, count)
                self.assertEqual(catalog.record_at(count - 1).shot_id, count + 99)
                self.assertEqual(catalog.find_shot_id(10_099), 9_999)
                self.assertLessEqual(catalog.cache_entries, 32)


if __name__ == "__main__":
    unittest.main()
