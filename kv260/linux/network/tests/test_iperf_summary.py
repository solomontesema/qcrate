#!/usr/bin/env python3
"""Focused tests for the iperf3 acceptance-summary parser."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


NETWORK_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NETWORK_DIR))

import iperf_summary  # noqa: E402


def write_result(directory: Path, name: str, document: dict[str, object]) -> Path:
    path = directory / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class IperfParserTests(unittest.TestCase):
    def test_tcp_uses_receiver_rate_and_sender_retransmits(self) -> None:
        document = {
            "start": {
                "timestamp": {"time": "2026-08-29T12:00:00Z"},
                "test_start": {"protocol": "TCP", "reverse": 0},
            },
            "end": {
                "sum_sent": {"bits_per_second": 940e6, "retransmits": 3},
                "sum_received": {"bits_per_second": 935e6},
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = write_result(Path(temporary), "tcp-host-to-kv260.json", document)
            result = iperf_summary.parse_iperf(path)
        self.assertEqual(result.protocol, "TCP")
        self.assertFalse(result.reverse)
        self.assertEqual(result.received_mbps, 935.0)
        self.assertEqual(result.retransmits, 3)

    def test_udp_derives_receiver_rate_from_reported_loss(self) -> None:
        document = {
            "start": {
                "timestamp": {"time": "2026-08-29T12:00:00Z"},
                "test_start": {
                    "protocol": "UDP",
                    "reverse": 1,
                },
            },
            "end": {
                "sum": {
                    "bits_per_second": 500e6,
                    "lost_percent": 2.5,
                    "jitter_ms": 0.125,
                    "sender": True,
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = write_result(Path(temporary), "udp-kv260-to-host-500M.json", document)
            result = iperf_summary.parse_iperf(path)
        self.assertTrue(result.reverse)
        self.assertEqual(result.requested_mbps, 500.0)
        self.assertEqual(result.received_mbps, 487.5)
        self.assertEqual(result.loss_percent, 2.5)
        self.assertEqual(result.jitter_ms, 0.125)

    def test_udp_applies_loss_to_reverse_aggregate(self) -> None:
        document = {
            "start": {
                "timestamp": {"time": "2026-08-29T12:00:00Z"},
                "test_start": {"protocol": "UDP", "reverse": 1},
            },
            "end": {
                "sum": {
                    "bits_per_second": 487.5e6,
                    "lost_percent": 2.5,
                    "jitter_ms": 0.125,
                    "sender": False,
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = write_result(Path(temporary), "udp-kv260-to-host-500M.json", document)
            result = iperf_summary.parse_iperf(path)
        self.assertEqual(result.received_mbps, 475.3125)

    def test_rejects_iperf_error_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_result(Path(temporary), "failed.json", {"error": "connect failed"})
            with self.assertRaisesRegex(ValueError, "connect failed"):
                iperf_summary.parse_iperf(path)


if __name__ == "__main__":
    unittest.main()
