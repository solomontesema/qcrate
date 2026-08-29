#!/usr/bin/env python3
"""Fault-injection tests for Q-Crate Data Plane capture assembly."""
from __future__ import annotations

import binascii
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path


DATA_PLANE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DATA_PLANE_DIR))

import qcrate_data_protocol as protocol  # noqa: E402
import qcrate_receiver as receiver  # noqa: E402


SOURCE = ("192.0.2.10", 49152)
RUN_ID = 0x1020304050607080
STREAM_ID = 3
SHOT_ID = 9


def header(
    packet_type: protocol.PacketType,
    sequence: int,
    *,
    payload_format: protocol.PayloadFormat = protocol.PayloadFormat.NONE,
    flags: protocol.Flags = protocol.Flags(0),
    frame_id: int = 0,
    sample_offset: int = 0,
    sample_count: int = 0,
    sample_bytes: int = 0,
) -> protocol.Header:
    return protocol.Header(
        packet_type=packet_type,
        payload_format=payload_format,
        flags=flags,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        shot_id=SHOT_ID,
        packet_sequence=sequence,
        frame_id=frame_id,
        sample_offset=sample_offset,
        sample_count=sample_count,
        sample_bytes=sample_bytes,
    )


def build_shot() -> tuple[list[bytes], bytes]:
    info = protocol.StreamInfo(
        frame_samples=4,
        sample_rate_numerator=12_500_000,
        sample_rate_denominator=1,
        timestamp_rate_numerator=200_000_000,
        timestamp_rate_denominator=1,
        center_frequency_hz=1_000_000,
        channel_count=2,
        component_bits=16,
        fraction_bits=15,
        timestamp_clock_id=protocol.TimestampClock.QCRATE_TIMEBASE,
        config_id=0x55,
    )
    datagrams = [
        protocol.Packet(
            header(
                protocol.PacketType.STREAM_INFO,
                0,
                payload_format=protocol.PayloadFormat.IQ_S16_LE,
                sample_bytes=4,
            ),
            info.encode(),
        ).encode()
    ]
    words = list(range(8))
    complete_payload = struct.pack("<8I", *words)
    sequence = 1
    for frame_id in range(2):
        for sample_offset in (0, 2):
            flags = protocol.Flags.FRAME_START if sample_offset == 0 else protocol.Flags.FRAME_END
            payload = struct.pack(
                "<2I",
                words[frame_id * 4 + sample_offset],
                words[frame_id * 4 + sample_offset + 1],
            )
            datagrams.append(
                protocol.Packet(
                    header(
                        protocol.PacketType.DATA,
                        sequence,
                        payload_format=protocol.PayloadFormat.IQ_S16_LE,
                        flags=flags,
                        frame_id=frame_id,
                        sample_offset=sample_offset,
                        sample_count=2,
                        sample_bytes=4,
                    ),
                    payload,
                ).encode()
            )
            sequence += 1
    end = protocol.ShotEnd(
        status=int(protocol.ShotStatus.COMPLETE),
        end_flags=int(protocol.Flags.END_OF_STREAM | protocol.Flags.PAYLOAD_CRC_VALID),
        total_frames=2,
        total_samples=8,
        total_data_packets=4,
        first_packet_sequence=1,
        last_packet_sequence=4,
        payload_bytes=len(complete_payload),
        payload_crc32=binascii.crc32(complete_payload) & 0xFFFFFFFF,
    )
    datagrams.append(
        protocol.Packet(
            header(protocol.PacketType.SHOT_END, 5), end.encode()
        ).encode()
    )
    return datagrams, complete_payload


def assemble(datagrams: list[bytes]) -> tuple[bytes | None, dict[str, object]]:
    assembler = receiver.ShotAssembler()
    for index, datagram in enumerate(datagrams):
        assembler.ingest(receiver.JournalRecord(
            1_000_000 + index, SOURCE[0], SOURCE[1], datagram
        ))
    return assembler.finalize()


class AssemblyTests(unittest.TestCase):
    def test_complete_shot(self) -> None:
        datagrams, expected = build_shot()
        samples, manifest = assemble(datagrams)
        self.assertTrue(manifest["complete"])
        self.assertEqual(samples, expected)
        self.assertEqual(manifest["issues"], [])

    def test_reordering_is_reported_but_reassembled(self) -> None:
        datagrams, expected = build_shot()
        reordered = [datagrams[index] for index in (0, 2, 1, 4, 3, 5)]
        samples, manifest = assemble(reordered)
        self.assertTrue(manifest["complete"])
        self.assertEqual(samples, expected)
        self.assertEqual(manifest["arrival"]["reordered_packets"], 2)

    def test_data_arriving_before_metadata_is_retained(self) -> None:
        datagrams, expected = build_shot()
        samples, manifest = assemble([datagrams[1], datagrams[0], *datagrams[2:]])
        self.assertTrue(manifest["complete"])
        self.assertEqual(samples, expected)
        self.assertEqual(manifest["arrival"]["reordered_packets"], 1)

    def test_identical_duplicate_is_reported_but_harmless(self) -> None:
        datagrams, expected = build_shot()
        samples, manifest = assemble(datagrams[:3] + [datagrams[2]] + datagrams[3:])
        self.assertTrue(manifest["complete"])
        self.assertEqual(samples, expected)
        self.assertEqual(manifest["arrival"]["duplicate_packets"], 1)

    def test_drop_prevents_sample_publication(self) -> None:
        datagrams, _ = build_shot()
        samples, manifest = assemble(datagrams[:2] + datagrams[3:])
        self.assertFalse(manifest["complete"])
        self.assertIsNone(samples)
        self.assertEqual(
            manifest["arrival"]["missing_sequence_ranges"],
            [{"first": 2, "last": 2}],
        )

    def test_truncation_prevents_sample_publication(self) -> None:
        datagrams, _ = build_shot()
        datagrams[2] = datagrams[2][:-1]
        samples, manifest = assemble(datagrams)
        self.assertFalse(manifest["complete"])
        self.assertIsNone(samples)
        self.assertEqual(manifest["arrival"]["malformed_selected"], 1)

    def test_conflicting_duplicate_is_fatal(self) -> None:
        datagrams, _ = build_shot()
        changed = bytearray(datagrams[2])
        changed[-1] ^= 1
        samples, manifest = assemble(datagrams[:3] + [bytes(changed)] + datagrams[3:])
        self.assertFalse(manifest["complete"])
        self.assertIsNone(samples)
        self.assertEqual(manifest["arrival"]["conflicting_duplicates"], 1)


class JournalAndBundleTests(unittest.TestCase):
    def test_journal_round_trip_ipv4_and_ipv6(self) -> None:
        records = [
            receiver.JournalRecord(10, "192.0.2.1", 1000, b"abc"),
            receiver.JournalRecord(20, "2001:db8::1", 2000, b"defg"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packets.qcdp"
            with receiver.JournalWriter(path) as writer:
                for record in records:
                    writer.write(record)
            self.assertEqual(list(receiver.read_journal(path)), records)

    def test_bundle_publishes_samples_only_when_complete(self) -> None:
        datagrams, expected = build_shot()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, selected, should_pass in (
                ("complete", datagrams, True),
                ("incomplete", datagrams[:-2] + datagrams[-1:], False),
            ):
                output = root / name
                receiver.prepare_output(output)
                journal = output / "packets.qcdp"
                assembler = receiver.ShotAssembler()
                with receiver.JournalWriter(journal) as writer:
                    for index, datagram in enumerate(selected):
                        record = receiver.JournalRecord(index, SOURCE[0], SOURCE[1], datagram)
                        writer.write(record)
                        assembler.ingest(record)
                self.assertEqual(
                    receiver.write_bundle(output, assembler, journal), should_pass
                )
                manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(manifest["complete"], should_pass)
                self.assertEqual((output / "samples.bin").exists(), should_pass)
                if should_pass:
                    self.assertEqual((output / "samples.bin").read_bytes(), expected)


if __name__ == "__main__":
    unittest.main()
