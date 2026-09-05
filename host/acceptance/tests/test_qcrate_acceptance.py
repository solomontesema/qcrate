#!/usr/bin/env python3
"""Focused DP-5D evidence and verdict tests."""
from __future__ import annotations

import binascii
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


ACCEPTANCE_DIR = Path(__file__).resolve().parents[1]
ANALYZER_DIR = ACCEPTANCE_DIR.parent / "analyzer"
sys.path[:0] = [str(ACCEPTANCE_DIR), str(ANALYZER_DIR)]

import qcrate_acceptance as acceptance  # noqa: E402


def make_accepted_run(root: Path) -> None:
    run_id = 0x1234
    stream_id = 1
    words = acceptance.analyzer.expected_words(
        str(acceptance.analyzer.DEFAULT_CONFIG.resolve()), 64
    ).astype("<u4")
    payload = words.tobytes()
    root.mkdir()
    (root / "samples.iq16").write_bytes(payload)
    header = struct.pack(
        "!IHHH6xQI4x", 0x51494458, 1, 32, 128, run_id, stream_id
    )
    record = struct.pack(
        "!QIIQQQQQQQIIIIIIIIIHHQ8x",
        7, 1, 0, 0, 2, 123456, 100, 200, 0, len(payload),
        1, 64, 1, 3, 0, 0, 0,
        binascii.crc32(payload) & 0xFFFFFFFF,
        0, 2, 4, acceptance.analyzer.current_config_id(),
    )
    (root / "shots.qidx").write_bytes(header + record)
    (root / "run.json").write_text(json.dumps({
        "format": "qcrate-run-v1",
        "complete": True,
        "run_id": f"0x{run_id:016x}",
        "stream_id": f"0x{stream_id:08x}",
        "terminal_received": True,
        "run_continuity_error": False,
        "shots_complete": 1,
        "shots_incomplete": 0,
        "datagrams_journaled": 3,
        "kernel_receive_drops": 0,
        "timing": {"duration_seconds": 301.0, "process_cpu_percent": 1.25},
        "performance": {
            "shot_rate_hz": 1.0 / 301.0,
            "sample_payload_mbps": len(payload) * 8 / 301.0 / 1e6,
            "udp_payload_mbps": 0.001,
        },
        "stream": {
            "sample_rate_numerator": 12_500_000,
            "sample_rate_denominator": 1,
        },
    }))
    (root / "sender.json").write_text(json.dumps({
        "format": "qcrate-sender-report-v1",
        "complete": True,
        "run_id": f"0x{run_id:016x}",
        "stream_id": f"0x{stream_id:08x}",
        "timing": {
            "capture_duration_seconds": 300.25,
            "process_cpu_percent": 2.5,
        },
        "capture": {"shots_transmitted": 1},
        "health": {
            "token_queue_high_water": 1,
            "dma_ready_high_water": 1,
            "dma_stall_cycles": 0,
            "missed_triggers": 0,
            "starvation_events": 0,
            "skipped_triggers": 0,
            "dma_error_events": 0,
        },
    }))


class AcceptanceTests(unittest.TestCase):
    def test_extracts_report_from_pty_transcript(self) -> None:
        begin = "__QCRATE_REPORT_deadbeef_BEGIN__"
        end = "__QCRATE_REPORT_deadbeef_END__"
        payload = (
            "Password: \r\n" + begin + "\r\n" + json.dumps({
                "format": "qcrate-sender-report-v1",
                "complete": True,
            }) + "\r\n" + end + "\r\nConnection closed\r\n"
        ).encode()
        report = acceptance.extract_remote_report(payload, begin, end)
        self.assertTrue(report["complete"])

    def test_rejects_missing_embedded_report(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "did not return"):
            acceptance.extract_remote_report(b"connection failed", "BEGIN", "END")

    def test_clean_soak_is_pending_until_faults_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            make_accepted_run(run)
            result = acceptance.evaluate_run(
                run, minimum_duration=300.0, faults_path=None
            )
            self.assertEqual(result["result"], "PENDING")
            self.assertTrue(result["clean_soak_pass"])
            self.assertEqual(result["measurement"]["reference_mismatches"], 0)
            image = Path(directory) / "report.png"
            acceptance.render_report(run, result, image)
            self.assertGreater(image.stat().st_size, 10_000)

    def test_safe_pool_backpressure_does_not_claim_measurement_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            make_accepted_run(run)
            sender = json.loads((run / "sender.json").read_text())
            sender["health"]["starvation_events"] = 123
            (run / "sender.json").write_text(json.dumps(sender))
            result = acceptance.evaluate_run(
                run, minimum_duration=300.0, faults_path=None
            )
            self.assertTrue(result["clean_soak_pass"])
            self.assertEqual(result["result"], "PENDING")
            self.assertEqual(
                result["instrument_health"]["starvation_events"], 123
            )

    def test_post_recording_corruption_fails_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            make_accepted_run(run)
            payload = bytearray((run / "samples.iq16").read_bytes())
            payload[0] ^= 1
            (run / "samples.iq16").write_bytes(payload)
            result = acceptance.evaluate_run(
                run, minimum_duration=300.0, faults_path=None
            )
            self.assertEqual(result["result"], "FAIL")
            self.assertFalse(result["clean_soak_pass"])

    def test_fault_evidence_promotes_clean_soak_to_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            faults = root / "faults.json"
            make_accepted_run(run)
            faults.write_text(json.dumps({
                "format": acceptance.FAULT_FORMAT,
                "cases": {
                    name: {"pass": True}
                    for name in acceptance.REQUIRED_FAULTS
                },
            }))
            result = acceptance.evaluate_run(
                run, minimum_duration=300.0, faults_path=faults
            )
            self.assertEqual(result["result"], "PASS")


if __name__ == "__main__":
    unittest.main()
