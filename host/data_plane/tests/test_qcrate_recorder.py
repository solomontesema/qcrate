#!/usr/bin/env python3
"""Socket-level acceptance and fault-injection tests for qcrate-recorder."""
from __future__ import annotations

import binascii
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


DATA_PLANE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DATA_PLANE_DIR))

import qcrate_data_protocol as protocol  # noqa: E402
import qcrate_receiver as receiver  # noqa: E402


RUN_ID = 0x1020304050607080
STREAM_ID = 3
QIDX_HEADER = struct.Struct("!IHHH6xQI4x")
QIDX_RECORD = struct.Struct("!QIIQQQQQQQIIIIIIIIIHHQ8x")
QIDX_MAGIC = 0x51494458
QIDX_COMPLETE = 1
QIDX_INCOMPLETE = 2


def packet_header(
    packet_type: protocol.PacketType,
    shot_id: int,
    sequence: int,
    *,
    payload_format: protocol.PayloadFormat = protocol.PayloadFormat.NONE,
    flags: protocol.Flags = protocol.Flags(0),
    frame_id: int = 0,
    sample_offset: int = 0,
    sample_count: int = 0,
    sample_bytes: int = 0,
    timestamp_ticks: int = 0,
) -> protocol.Header:
    return protocol.Header(
        packet_type=packet_type,
        payload_format=payload_format,
        flags=flags,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        shot_id=shot_id,
        packet_sequence=sequence,
        frame_id=frame_id,
        sample_offset=sample_offset,
        sample_count=sample_count,
        sample_bytes=sample_bytes,
        timestamp_ticks=timestamp_ticks,
    )


def build_run(shot_count: int = 3) -> tuple[list[list[bytes]], list[bytes], bytes]:
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
    sequence = 0
    shots: list[list[bytes]] = []
    payloads: list[bytes] = []
    for shot_id in range(1, shot_count + 1):
        shot: list[bytes] = []
        shot.append(protocol.Packet(
            packet_header(
                protocol.PacketType.STREAM_INFO,
                shot_id,
                sequence,
                payload_format=protocol.PayloadFormat.IQ_S16_LE,
                sample_bytes=4,
            ),
            info.encode(),
        ).encode())
        sequence += 1
        words = [shot_id * 100 + value for value in range(8)]
        payload = struct.pack("<8I", *words)
        payloads.append(payload)
        first_data_sequence = sequence
        for frame_id in range(2):
            for sample_offset in (0, 2):
                first = frame_id == 0 and sample_offset == 0
                flags = (
                    (protocol.Flags.FRAME_START if sample_offset == 0 else 0)
                    | (protocol.Flags.FRAME_END if sample_offset == 2 else 0)
                    | (protocol.Flags.TIMESTAMP_VALID if first else 0)
                )
                chunk = struct.pack(
                    "<2I",
                    words[frame_id * 4 + sample_offset],
                    words[frame_id * 4 + sample_offset + 1],
                )
                shot.append(protocol.Packet(
                    packet_header(
                        protocol.PacketType.DATA,
                        shot_id,
                        sequence,
                        payload_format=protocol.PayloadFormat.IQ_S16_LE,
                        flags=protocol.Flags(flags),
                        frame_id=frame_id,
                        sample_offset=sample_offset,
                        sample_count=2,
                        sample_bytes=4,
                        timestamp_ticks=shot_id * 10_000 if first else 0,
                    ),
                    chunk,
                ).encode())
                sequence += 1
        end = protocol.ShotEnd(
            status=int(protocol.ShotStatus.COMPLETE),
            end_flags=int(protocol.Flags.PAYLOAD_CRC_VALID),
            total_frames=2,
            total_samples=8,
            total_data_packets=4,
            first_packet_sequence=first_data_sequence,
            last_packet_sequence=sequence - 1,
            payload_bytes=len(payload),
            payload_crc32=binascii.crc32(payload) & 0xFFFFFFFF,
        )
        shot.append(protocol.Packet(
            packet_header(protocol.PacketType.SHOT_END, shot_id, sequence),
            end.encode(),
        ).encode())
        sequence += 1
        shots.append(shot)
    terminal = protocol.Packet(
        packet_header(
            protocol.PacketType.HEARTBEAT,
            shot_count,
            sequence,
            flags=protocol.Flags.END_OF_STREAM,
        )
    ).encode()
    return shots, payloads, terminal


def read_index(path: Path) -> tuple[tuple[int, ...], list[tuple[int, ...]]]:
    raw = path.read_bytes()
    if len(raw) < QIDX_HEADER.size:
        raise AssertionError("QIDX header is truncated")
    header = QIDX_HEADER.unpack_from(raw)
    records_raw = raw[QIDX_HEADER.size :]
    if len(records_raw) % QIDX_RECORD.size:
        raise AssertionError("QIDX record stream is truncated")
    records = [
        QIDX_RECORD.unpack_from(records_raw, offset)
        for offset in range(0, len(records_raw), QIDX_RECORD.size)
    ]
    return header, records


@unittest.skipUnless(os.environ.get("QCRATE_RECORDER"), "recorder binary not supplied")
class RecorderIntegrationTests(unittest.TestCase):
    def run_capture(
        self, shots: list[list[bytes]], terminal: bytes
    ) -> tuple[subprocess.CompletedProcess[str], Path, tempfile.TemporaryDirectory[str]]:
        temporary = tempfile.TemporaryDirectory()
        output = Path(temporary.name) / "run"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen(
            [
                os.environ["QCRATE_RECORDER"],
                "--bind", "127.0.0.1",
                "--port", str(port),
                "--source", "127.0.0.1",
                "--output", str(output),
                "--wait-timeout-ms", "2000",
                "--idle-timeout-ms", "500",
                "--end-grace-ms", "20",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.05)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for shot in shots:
                for datagram in shot:
                    sender.sendto(datagram, ("127.0.0.1", port))
            sender.sendto(terminal, ("127.0.0.1", port))
        stdout, stderr = process.communicate(timeout=5)
        completed = subprocess.CompletedProcess(
            process.args, process.returncode, stdout, stderr
        )
        return completed, output, temporary

    def test_complete_multishot_run(self) -> None:
        shots, payloads, terminal = build_run()
        completed, output, temporary = self.run_capture(shots, terminal)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual((output / "samples.iq16").read_bytes(), b"".join(payloads))
        self.assertEqual(len(list(receiver.read_journal(output / "packets.qcdp"))), 19)
        header, records = read_index(output / "shots.qidx")
        self.assertEqual(header, (QIDX_MAGIC, 1, 32, 128, RUN_ID, STREAM_ID))
        self.assertEqual([record[0] for record in records], [1, 2, 3])
        self.assertEqual([record[1] for record in records], [QIDX_COMPLETE] * 3)
        manifest = json.loads((output / "run.json").read_text())
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["shots_complete"], 3)
        self.assertEqual(manifest["shots_incomplete"], 0)
        self.assertEqual(manifest["stream"]["payload_format"], 2)
        self.assertEqual(manifest["stream"]["sample_rate_numerator"], 12_500_000)
        self.assertEqual(manifest["stream"]["timestamp_rate_numerator"], 200_000_000)
        self.assertGreater(manifest["timing"]["duration_seconds"], 0)
        self.assertGreaterEqual(manifest["timing"]["process_cpu_percent"], 0)
        self.assertGreater(manifest["performance"]["shot_rate_hz"], 0)
        self.assertEqual(
            manifest["datagram_bytes_journaled"],
            sum(len(datagram) for shot in shots for datagram in shot) + len(terminal),
        )

    def test_missing_packet_is_indexed_without_sample_publication(self) -> None:
        shots, payloads, terminal = build_run()
        del shots[1][2]
        completed, output, temporary = self.run_capture(shots, terminal)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertEqual((output / "samples.iq16").read_bytes(), payloads[0] + payloads[2])
        _, records = read_index(output / "shots.qidx")
        self.assertEqual([record[1] for record in records], [
            QIDX_COMPLETE, QIDX_INCOMPLETE, QIDX_COMPLETE
        ])
        self.assertEqual(records[1][8], 0xFFFFFFFFFFFFFFFF)
        self.assertEqual(records[1][9], 0)
        manifest = json.loads((output / "run.json").read_text())
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["shots_incomplete"], 1)

    def test_reordering_and_identical_duplicate_are_audited(self) -> None:
        shots, payloads, terminal = build_run(shot_count=2)
        shots[1] = [
            shots[1][0], shots[1][2], shots[1][1], shots[1][2],
            *shots[1][3:],
        ]
        completed, output, temporary = self.run_capture(shots, terminal)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual((output / "samples.iq16").read_bytes(), b"".join(payloads))
        _, records = read_index(output / "shots.qidx")
        self.assertEqual(records[1][1], QIDX_COMPLETE)
        self.assertEqual(records[1][14], 1)
        self.assertGreaterEqual(records[1][15], 1)
        manifest = json.loads((output / "run.json").read_text())
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["duplicate_packets"], 1)
        self.assertGreaterEqual(manifest["reordered_packets"], 1)

    def test_first_data_before_stream_info_is_recovered(self) -> None:
        shots, payloads, terminal = build_run(shot_count=1)
        shots[0][0], shots[0][1] = shots[0][1], shots[0][0]
        completed, output, temporary = self.run_capture(shots, terminal)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual((output / "samples.iq16").read_bytes(), payloads[0])
        _, records = read_index(output / "shots.qidx")
        self.assertEqual(records[0][1], QIDX_COMPLETE)
        self.assertGreaterEqual(records[0][15], 1)

    def test_conflicting_duplicate_quarantines_the_shot(self) -> None:
        shots, _, terminal = build_run(shot_count=1)
        changed = bytearray(shots[0][2])
        changed[-1] ^= 1
        shots[0] = shots[0][:3] + [bytes(changed)] + shots[0][3:]
        completed, output, temporary = self.run_capture(shots, terminal)
        self.addCleanup(temporary.cleanup)
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertEqual((output / "samples.iq16").read_bytes(), b"")
        _, records = read_index(output / "shots.qidx")
        self.assertEqual(records[0][1], QIDX_INCOMPLETE)
        self.assertNotEqual(records[0][2] & (1 << 2), 0)
        manifest = json.loads((output / "run.json").read_text())
        self.assertEqual(manifest["conflicting_packets"], 1)


if __name__ == "__main__":
    unittest.main()
