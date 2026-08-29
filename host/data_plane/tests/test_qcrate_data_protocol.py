#!/usr/bin/env python3
"""Exact and malformed-vector tests for Q-Crate Data Plane v1."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


DATA_PLANE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DATA_PLANE_DIR))

import qcrate_data_protocol as protocol  # noqa: E402


GOLDEN_HEADER = bytes.fromhex(
    "51434450000100400002000200000003"
    "01020304111213141516171821222324"
    "25262728313233343536373841424344"
    "51525354012300046162636465666768"
)


def golden_header(**changes: object) -> protocol.Header:
    values: dict[str, object] = {
        "packet_type": protocol.PacketType.DATA,
        "payload_format": protocol.PayloadFormat.IQ_S16_LE,
        "flags": protocol.Flags.TIMESTAMP_VALID | protocol.Flags.FRAME_START,
        "stream_id": 0x01020304,
        "run_id": 0x1112131415161718,
        "shot_id": 0x2122232425262728,
        "packet_sequence": 0x3132333435363738,
        "frame_id": 0x41424344,
        "sample_offset": 0x51525354,
        "sample_count": 0x0123,
        "sample_bytes": 4,
        "timestamp_ticks": 0x6162636465666768,
    }
    values.update(changes)
    return protocol.Header(**values)  # type: ignore[arg-type]


class HeaderTests(unittest.TestCase):
    def test_matches_cross_language_golden_vector(self) -> None:
        header = golden_header()
        self.assertEqual(header.encode(), GOLDEN_HEADER)
        self.assertEqual(protocol.Header.decode(GOLDEN_HEADER), header)

    def test_rejects_magic_version_and_short_header(self) -> None:
        with self.assertRaisesRegex(ValueError, "shorter"):
            protocol.Header.decode(GOLDEN_HEADER[:-1])
        bad_magic = bytearray(GOLDEN_HEADER)
        bad_magic[0] = 0
        with self.assertRaisesRegex(ValueError, "magic"):
            protocol.Header.decode(bad_magic)
        bad_version = bytearray(GOLDEN_HEADER)
        bad_version[5] = 2
        with self.assertRaisesRegex(ValueError, "version"):
            protocol.Header.decode(bad_version)


class ObjectTests(unittest.TestCase):
    def test_stream_info_round_trip(self) -> None:
        info = protocol.StreamInfo(
            frame_samples=1024,
            sample_rate_numerator=12_500_000,
            sample_rate_denominator=1,
            timestamp_rate_numerator=200_000_000,
            timestamp_rate_denominator=1,
            center_frequency_hz=-1_000_000,
            channel_count=2,
            component_bits=16,
            fraction_bits=15,
            timestamp_clock_id=protocol.TimestampClock.QCRATE_TIMEBASE,
            config_id=0x1020304050607080,
        )
        self.assertEqual(protocol.StreamInfo.decode(info.encode()), info)

    def test_shot_end_round_trip_and_reserved_check(self) -> None:
        end = protocol.ShotEnd(
            status=int(protocol.ShotStatus.COMPLETE),
            end_flags=int(protocol.Flags.END_OF_STREAM),
            total_frames=4,
            total_samples=4096,
            total_data_packets=52,
            first_packet_sequence=2,
            last_packet_sequence=53,
            payload_bytes=16384,
            payload_crc32=0x12345678,
        )
        encoded = end.encode()
        self.assertEqual(protocol.ShotEnd.decode(encoded), end)
        malformed = bytearray(encoded)
        malformed[15] = 1
        with self.assertRaisesRegex(ValueError, "reserved"):
            protocol.ShotEnd.decode(malformed)


class PacketTests(unittest.TestCase):
    def test_stream_info_packet_round_trip(self) -> None:
        info = protocol.StreamInfo(
            frame_samples=1024,
            sample_rate_numerator=12_500_000,
            sample_rate_denominator=1,
            timestamp_rate_numerator=200_000_000,
            timestamp_rate_denominator=1,
            center_frequency_hz=1_000_000,
            channel_count=2,
            component_bits=16,
            fraction_bits=15,
            timestamp_clock_id=protocol.TimestampClock.QCRATE_TIMEBASE,
            config_id=1,
        )
        packet = protocol.Packet(
            protocol.Header(
                packet_type=protocol.PacketType.STREAM_INFO,
                payload_format=protocol.PayloadFormat.IQ_S16_LE,
                flags=protocol.Flags(0),
                stream_id=1,
                run_id=2,
                shot_id=0,
                packet_sequence=0,
                sample_bytes=4,
            ),
            info.encode(),
        )
        self.assertEqual(protocol.Packet.decode(packet.encode()), packet)

    def test_data_packet_round_trip(self) -> None:
        payload = bytes.fromhex("0100010002000200")
        header = golden_header(
            flags=protocol.Flags.FRAME_START,
            frame_id=0,
            sample_offset=0,
            sample_count=2,
            timestamp_ticks=0,
        )
        packet = protocol.Packet(header, payload)
        self.assertEqual(protocol.Packet.decode(packet.encode()), packet)

    def test_rejects_truncated_wrong_format_and_oversize_data(self) -> None:
        header = golden_header(
            flags=protocol.Flags.FRAME_START,
            sample_offset=0,
            sample_count=2,
            timestamp_ticks=0,
        )
        with self.assertRaisesRegex(ValueError, "expected 8"):
            protocol.Packet(header, b"1234").encode()
        with self.assertRaisesRegex(ValueError, "requires 4"):
            protocol.Packet(
                golden_header(sample_bytes=2), b"1234"
            ).encode()
        with self.assertRaisesRegex(ValueError, "exceeds"):
            protocol.Packet(
                golden_header(
                    flags=protocol.Flags.FRAME_START,
                    sample_offset=0,
                    sample_count=335,
                    timestamp_ticks=0,
                ),
                bytes(335 * 4),
            ).encode()

    def test_rejects_false_timestamp_and_bad_frame_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "TIMESTAMP_VALID"):
            protocol.Packet(
                golden_header(flags=protocol.Flags(0)), bytes(0x0123 * 4)
            ).encode()
        with self.assertRaisesRegex(ValueError, "sample offset"):
            protocol.Packet(
                golden_header(flags=protocol.Flags.FRAME_START, timestamp_ticks=0),
                bytes(0x0123 * 4),
            ).encode()

    def test_rejects_metadata_sample_fields_and_format(self) -> None:
        base = {
            "packet_type": protocol.PacketType.HEARTBEAT,
            "payload_format": protocol.PayloadFormat.NONE,
            "flags": protocol.Flags(0),
            "stream_id": 1,
            "run_id": 2,
            "shot_id": 3,
            "packet_sequence": 4,
        }
        with self.assertRaisesRegex(ValueError, "NONE requires 0"):
            protocol.Packet(protocol.Header(**base, sample_bytes=4)).encode()
        with self.assertRaisesRegex(ValueError, "must not identify"):
            protocol.Packet(protocol.Header(**base, frame_id=1)).encode()


if __name__ == "__main__":
    unittest.main()
