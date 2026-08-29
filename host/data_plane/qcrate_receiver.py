#!/usr/bin/env python3
"""Receive, journal, reassemble, and audit Q-Crate Data Plane v1 shots."""
from __future__ import annotations

import argparse
import binascii
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import struct
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

import qcrate_data_protocol as protocol


JOURNAL_MAGIC = 0x5143524A  # "QCRJ"
JOURNAL_VERSION = 1
JOURNAL_HEADER = struct.Struct("!IHHQ")
JOURNAL_RECORD = struct.Struct("!HBBHHQ16s")
JOURNAL_HEADER_BYTES = JOURNAL_HEADER.size
JOURNAL_RECORD_BYTES = JOURNAL_RECORD.size
DEFAULT_PORT = 47000
DEFAULT_RECEIVE_BUFFER = 16 * 1024 * 1024
MAX_PENDING_DATAGRAMS = 4096
SO_RXQ_OVFL = getattr(socket, "SO_RXQ_OVFL", 40)


@dataclass(frozen=True)
class JournalRecord:
    receive_monotonic_ns: int
    source_address: str
    source_port: int
    datagram: bytes


class JournalWriter:
    """Append-only Data Plane datagram journal."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None
        self.records = 0

    def __enter__(self) -> "JournalWriter":
        self._file = self.path.open("xb", buffering=1024 * 1024)
        self._file.write(JOURNAL_HEADER.pack(
            JOURNAL_MAGIC, JOURNAL_VERSION, JOURNAL_HEADER_BYTES, 0
        ))
        return self

    def write(self, record: JournalRecord) -> None:
        if self._file is None:
            raise RuntimeError("journal is not open")
        address = ipaddress.ip_address(record.source_address)
        family = 4 if address.version == 4 else 6
        packed_address = address.packed.ljust(16, b"\0")
        if len(record.datagram) > 0xFFFF:
            raise ValueError("journal datagram exceeds uint16 length")
        if not 0 <= record.source_port <= 0xFFFF:
            raise ValueError("journal source port exceeds uint16")
        self._file.write(JOURNAL_RECORD.pack(
            len(record.datagram),
            family,
            0,
            record.source_port,
            0,
            record.receive_monotonic_ns,
            packed_address,
        ))
        self._file.write(record.datagram)
        self.records += 1
        if self.records % 256 == 0:
            self._file.flush()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def read_journal(path: Path) -> Iterator[JournalRecord]:
    """Read one journal and reject truncation or unsupported fields."""
    with path.open("rb") as journal:
        raw_header = journal.read(JOURNAL_HEADER_BYTES)
        if len(raw_header) != JOURNAL_HEADER_BYTES:
            raise ValueError("journal header is truncated")
        magic, version, header_bytes, reserved = JOURNAL_HEADER.unpack(raw_header)
        if magic != JOURNAL_MAGIC:
            raise ValueError(f"bad journal magic 0x{magic:08x}")
        if version != JOURNAL_VERSION or header_bytes != JOURNAL_HEADER_BYTES:
            raise ValueError("unsupported journal version or header size")
        if reserved != 0:
            raise ValueError("journal header reserved field is nonzero")

        while True:
            raw_record = journal.read(JOURNAL_RECORD_BYTES)
            if not raw_record:
                return
            if len(raw_record) != JOURNAL_RECORD_BYTES:
                raise ValueError("journal record header is truncated")
            (
                datagram_bytes,
                family,
                flags,
                source_port,
                record_reserved,
                receive_ns,
                packed_address,
            ) = JOURNAL_RECORD.unpack(raw_record)
            if flags != 0 or record_reserved != 0:
                raise ValueError("journal record reserved field is nonzero")
            if family == 4:
                if any(packed_address[4:]):
                    raise ValueError("journal IPv4 padding is nonzero")
                address = ipaddress.ip_address(packed_address[:4])
            elif family == 6:
                address = ipaddress.ip_address(packed_address)
            else:
                raise ValueError(f"unsupported journal address family {family}")
            datagram = journal.read(datagram_bytes)
            if len(datagram) != datagram_bytes:
                raise ValueError("journal datagram is truncated")
            yield JournalRecord(receive_ns, str(address), source_port, datagram)


def source_key(record: JournalRecord) -> tuple[str, int]:
    return str(ipaddress.ip_address(record.source_address)), record.source_port


def packet_key(
    record: JournalRecord, header: protocol.Header
) -> tuple[str, int, int, int, int]:
    return (*source_key(record), header.run_id, header.stream_id, header.shot_id)


def find_missing_ranges(
    first: int, last: int, present: Iterable[int]
) -> tuple[int, list[dict[str, int]]]:
    """Find gaps without iterating over a potentially hostile sequence span."""
    if last < first:
        return 0, []
    cursor = first
    count = 0
    ranges: list[dict[str, int]] = []
    for value in sorted(set(present)):
        if value < first or value > last:
            continue
        if value > cursor:
            ranges.append({"first": cursor, "last": value - 1})
            count += value - cursor
        cursor = max(cursor, value + 1)
    if cursor <= last:
        ranges.append({"first": cursor, "last": last})
        count += last - cursor + 1
    return count, ranges


def hexadecimal(value: int, bits: int) -> str:
    return f"0x{value:0{bits // 4}x}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ShotAssembler:
    """Collect one finite shot while retaining loss and arrival diagnostics."""

    def __init__(self) -> None:
        self.identity: tuple[str, int, int, int, int] | None = None
        self.stream_info_header: protocol.Header | None = None
        self.stream_info: protocol.StreamInfo | None = None
        self.shot_end_header: protocol.Header | None = None
        self.shot_end: protocol.ShotEnd | None = None
        self.pending: list[
            tuple[JournalRecord, protocol.Header | None, protocol.Packet | None, str | None]
        ] = []
        self.seen_sequences: dict[int, bytes] = {}
        self.data_packets: dict[int, protocol.Packet] = {}
        self.packet_types: Counter[str] = Counter()
        self.total_datagrams = 0
        self.valid_selected_packets = 0
        self.foreign_packets = 0
        self.malformed_selected = 0
        self.malformed_unclassified = 0
        self.duplicate_packets = 0
        self.conflicting_duplicates = 0
        self.reordered_packets = 0
        self.highest_arrival_sequence: int | None = None
        self.protocol_errors: list[str] = []
        self.pending_overflow = 0

    @property
    def end_received(self) -> bool:
        return self.shot_end is not None

    def ingest(self, record: JournalRecord) -> None:
        self.total_datagrams += 1
        try:
            header = protocol.Header.decode(record.datagram)
        except ValueError as exc:
            if self.identity is None:
                self._save_pending((record, None, None, str(exc)))
            elif source_key(record) == self.identity[:2]:
                self.malformed_selected += 1
                self.protocol_errors.append(f"malformed datagram: {exc}")
            else:
                self.malformed_unclassified += 1
            return

        try:
            packet = protocol.Packet.decode(record.datagram)
            packet_error = None
        except ValueError as exc:
            packet = None
            packet_error = str(exc)

        if self.identity is None:
            if packet is not None and header.packet_type is protocol.PacketType.STREAM_INFO:
                self.identity = packet_key(record, header)
                saved = self.pending
                self.pending = []
                for pending in saved:
                    self._route(*pending)
                self._route(record, header, packet, packet_error)
            else:
                self._save_pending((record, header, packet, packet_error))
            return

        self._route(record, header, packet, packet_error)

    def _save_pending(
        self,
        item: tuple[
            JournalRecord, protocol.Header | None, protocol.Packet | None, str | None
        ],
    ) -> None:
        if len(self.pending) >= MAX_PENDING_DATAGRAMS:
            self.pending.pop(0)
            self.pending_overflow += 1
        self.pending.append(item)

    def _route(
        self,
        record: JournalRecord,
        header: protocol.Header | None,
        packet: protocol.Packet | None,
        packet_error: str | None,
    ) -> None:
        if header is None:
            if self.identity is not None and source_key(record) == self.identity[:2]:
                self.malformed_selected += 1
                self.protocol_errors.append(f"malformed datagram: {packet_error}")
            else:
                self.malformed_unclassified += 1
            return
        assert self.identity is not None
        if packet_key(record, header) != self.identity:
            self.foreign_packets += 1
            return
        if packet is None:
            self.malformed_selected += 1
            self.protocol_errors.append(
                f"packet sequence {header.packet_sequence}: {packet_error}"
            )
            return
        self._accept(packet, record.datagram)

    def _accept(self, packet: protocol.Packet, datagram: bytes) -> None:
        sequence = packet.header.packet_sequence
        digest = hashlib.sha256(datagram).digest()
        previous = self.seen_sequences.get(sequence)
        if previous is not None:
            if previous == digest:
                self.duplicate_packets += 1
            else:
                self.conflicting_duplicates += 1
                self.protocol_errors.append(
                    f"packet sequence {sequence} arrived with conflicting contents"
                )
            return
        if (
            self.highest_arrival_sequence is not None
            and sequence < self.highest_arrival_sequence
        ):
            self.reordered_packets += 1
        previous_highest = (
            self.highest_arrival_sequence
            if self.highest_arrival_sequence is not None
            else sequence
        )
        self.highest_arrival_sequence = max(sequence, previous_highest)
        self.seen_sequences[sequence] = digest
        self.valid_selected_packets += 1
        self.packet_types[packet.header.packet_type.name] += 1

        if packet.header.packet_type is protocol.PacketType.STREAM_INFO:
            try:
                info = protocol.StreamInfo.decode(packet.payload)
            except ValueError as exc:
                self.protocol_errors.append(f"invalid STREAM_INFO: {exc}")
                return
            if self.stream_info is None:
                self.stream_info_header = packet.header
                self.stream_info = info
            elif self.stream_info != info or self.stream_info_header is None:
                self.protocol_errors.append("conflicting STREAM_INFO packets")
            elif (
                self.stream_info_header.payload_format != packet.header.payload_format
                or self.stream_info_header.sample_bytes != packet.header.sample_bytes
            ):
                self.protocol_errors.append("STREAM_INFO header format changed")
        elif packet.header.packet_type is protocol.PacketType.DATA:
            self.data_packets[sequence] = packet
        elif packet.header.packet_type is protocol.PacketType.SHOT_END:
            try:
                end = protocol.ShotEnd.decode(packet.payload)
            except ValueError as exc:
                self.protocol_errors.append(f"invalid SHOT_END: {exc}")
                return
            if self.shot_end is None:
                self.shot_end_header = packet.header
                self.shot_end = end
            elif self.shot_end != end:
                self.protocol_errors.append("conflicting SHOT_END packets")

    def finalize(self) -> tuple[bytes | None, dict[str, object]]:
        """Return verified samples and a complete machine-readable audit."""
        issues = list(dict.fromkeys(self.protocol_errors))
        if self.identity is None:
            issues.append("no valid STREAM_INFO selected a shot")
        if self.stream_info is None or self.stream_info_header is None:
            issues.append("STREAM_INFO is missing or invalid")
        if self.shot_end is None or self.shot_end_header is None:
            issues.append("SHOT_END is missing or invalid")
        if self.malformed_selected:
            issues.append(f"{self.malformed_selected} selected datagram(s) were malformed")
        if self.conflicting_duplicates:
            issues.append(
                f"{self.conflicting_duplicates} packet-sequence collision(s) were observed"
            )
        if self.pending_overflow:
            issues.append(
                f"{self.pending_overflow} pre-metadata datagram(s) exceeded the pending limit"
            )

        missing_sequence_count = 0
        missing_sequence_ranges: list[dict[str, int]] = []
        if self.stream_info_header is not None and self.shot_end_header is not None:
            first = self.stream_info_header.packet_sequence
            last = self.shot_end_header.packet_sequence
            if last < first:
                issues.append("SHOT_END sequence precedes STREAM_INFO")
            else:
                missing_sequence_count, missing_sequence_ranges = find_missing_ranges(
                    first, last, self.seen_sequences
                )
                if missing_sequence_count:
                    issues.append(
                        f"{missing_sequence_count} packet sequence(s) are missing"
                    )
                outside = [
                    sequence for sequence in self.seen_sequences
                    if sequence < first or sequence > last
                ]
                if outside:
                    issues.append(
                        f"{len(outside)} selected packet(s) fall outside the shot interval"
                    )

        ordered_data = sorted(self.data_packets.items())
        if not ordered_data:
            issues.append("shot contains no DATA packets")
        frame_chunks: dict[int, list[tuple[int, int, protocol.Packet]]] = defaultdict(list)
        for sequence, packet in ordered_data:
            frame_chunks[packet.header.frame_id].append(
                (packet.header.sample_offset, sequence, packet)
            )

        reconstructed = bytearray()
        logical_order: list[tuple[int, int, int]] = []
        frame_ids = sorted(frame_chunks)
        if frame_ids and frame_ids != list(range(frame_ids[0], frame_ids[0] + len(frame_ids))):
            issues.append("frame IDs are not contiguous")
        for frame_id in frame_ids:
            chunks = sorted(frame_chunks[frame_id])
            expected_offset = 0
            if not chunks[0][2].header.flags & protocol.Flags.FRAME_START:
                issues.append(f"frame {frame_id} has no FRAME_START packet")
            for offset, sequence, packet in chunks:
                logical_order.append((frame_id, offset, sequence))
                if offset > expected_offset:
                    issues.append(
                        f"frame {frame_id} has a sample gap at offset {expected_offset}"
                    )
                elif offset < expected_offset:
                    issues.append(
                        f"frame {frame_id} has overlapping data at offset {offset}"
                    )
                expected_offset = max(expected_offset, offset + packet.header.sample_count)
                reconstructed.extend(packet.payload)
            if not chunks[-1][2].header.flags & protocol.Flags.FRAME_END:
                issues.append(f"frame {frame_id} has no FRAME_END packet")
            if self.stream_info is not None and expected_offset != self.stream_info.frame_samples:
                issues.append(
                    f"frame {frame_id} contains {expected_offset} samples; "
                    f"expected {self.stream_info.frame_samples}"
                )

        sequence_order = [
            (packet.header.frame_id, packet.header.sample_offset, sequence)
            for sequence, packet in ordered_data
        ]
        if sequence_order != logical_order:
            issues.append("packet sequence does not follow frame/sample order")

        source_flags = protocol.Flags(0)
        for _, packet in ordered_data:
            source_flags |= packet.header.flags & (
                protocol.Flags.DISCONTINUITY_BEFORE
                | protocol.Flags.SOURCE_OVERFLOW
                | protocol.Flags.SENDER_DROP
            )
        if source_flags:
            issues.append(f"sender reported data-integrity flags 0x{int(source_flags):08x}")

        payload_by_sequence = b"".join(packet.payload for _, packet in ordered_data)
        payload_crc32 = binascii.crc32(payload_by_sequence) & 0xFFFFFFFF
        payload_sha256 = hashlib.sha256(reconstructed).hexdigest()
        total_samples = sum(packet.header.sample_count for _, packet in ordered_data)

        if self.shot_end is not None:
            end = self.shot_end
            if end.status != int(protocol.ShotStatus.COMPLETE):
                issues.append(f"sender ended shot with status {end.status}")
            if not end.end_flags & int(protocol.Flags.END_OF_STREAM):
                issues.append("SHOT_END does not mark END_OF_STREAM")
            checks = (
                ("frames", len(frame_chunks), end.total_frames),
                ("samples", total_samples, end.total_samples),
                ("data packets", len(ordered_data), end.total_data_packets),
                ("payload bytes", len(payload_by_sequence), end.payload_bytes),
            )
            for name, actual, expected in checks:
                if actual != expected:
                    issues.append(f"received {actual} {name}; SHOT_END reports {expected}")
            if ordered_data:
                if ordered_data[0][0] != end.first_packet_sequence:
                    issues.append("first DATA packet sequence disagrees with SHOT_END")
                if ordered_data[-1][0] != end.last_packet_sequence:
                    issues.append("last DATA packet sequence disagrees with SHOT_END")
            if end.end_flags & int(protocol.Flags.PAYLOAD_CRC_VALID):
                if payload_crc32 != end.payload_crc32:
                    issues.append(
                        f"payload CRC is 0x{payload_crc32:08x}; "
                        f"SHOT_END reports 0x{end.payload_crc32:08x}"
                    )

        complete = not issues
        identity = self.identity
        manifest: dict[str, object] = {
            "format": "qcrate-capture-bundle-v1",
            "protocol_version": protocol.VERSION,
            "complete": complete,
            "issues": issues,
            "identity": None if identity is None else {
                "source_address": identity[0],
                "source_port": identity[1],
                "run_id": hexadecimal(identity[2], 64),
                "stream_id": hexadecimal(identity[3], 32),
                "shot_id": hexadecimal(identity[4], 64),
            },
            "arrival": {
                "datagrams": self.total_datagrams,
                "valid_selected_packets": self.valid_selected_packets,
                "foreign_packets": self.foreign_packets,
                "malformed_selected": self.malformed_selected,
                "malformed_unclassified": self.malformed_unclassified,
                "duplicate_packets": self.duplicate_packets,
                "conflicting_duplicates": self.conflicting_duplicates,
                "reordered_packets": self.reordered_packets,
                "pending_overflow": self.pending_overflow,
                "packet_types": dict(sorted(self.packet_types.items())),
                "missing_sequence_ranges": missing_sequence_ranges,
            },
            "samples": {
                "frames": len(frame_chunks),
                "count": total_samples,
                "bytes": len(reconstructed),
                "payload_crc32": f"0x{payload_crc32:08x}",
                "sha256": payload_sha256,
            },
            "stream_info": stream_info_json(self.stream_info_header, self.stream_info),
            "shot_end": shot_end_json(self.shot_end_header, self.shot_end),
        }
        return (bytes(reconstructed) if complete else None), manifest


def stream_info_json(
    header: protocol.Header | None, info: protocol.StreamInfo | None
) -> dict[str, object] | None:
    if header is None or info is None:
        return None
    return {
        "payload_format": header.payload_format.name,
        "sample_bytes": header.sample_bytes,
        "frame_samples": info.frame_samples,
        "sample_rate": {
            "numerator": info.sample_rate_numerator,
            "denominator": info.sample_rate_denominator,
        },
        "timestamp_rate": {
            "numerator": info.timestamp_rate_numerator,
            "denominator": info.timestamp_rate_denominator,
        },
        "center_frequency_hz": info.center_frequency_hz,
        "channel_count": info.channel_count,
        "component_bits": info.component_bits,
        "fraction_bits": info.fraction_bits,
        "timestamp_clock": info.timestamp_clock_id.name,
        "config_id": hexadecimal(info.config_id, 64),
    }


def shot_end_json(
    header: protocol.Header | None, end: protocol.ShotEnd | None
) -> dict[str, object] | None:
    if header is None or end is None:
        return None
    try:
        status = protocol.ShotStatus(end.status).name
    except ValueError:
        status = f"UNKNOWN_{end.status}"
    return {
        "packet_sequence": header.packet_sequence,
        "status": status,
        "end_flags": f"0x{end.end_flags:08x}",
        "total_frames": end.total_frames,
        "total_samples": end.total_samples,
        "total_data_packets": end.total_data_packets,
        "first_packet_sequence": end.first_packet_sequence,
        "last_packet_sequence": end.last_packet_sequence,
        "payload_bytes": end.payload_bytes,
        "payload_crc32": f"0x{end.payload_crc32:08x}",
    }


def prepare_output(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise FileExistsError(f"output path is not a directory: {path}")
        if any(path.iterdir()):
            raise FileExistsError(f"output directory is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def write_bundle(
    output: Path,
    assembler: ShotAssembler,
    journal_path: Path,
    socket_details: dict[str, object] | None = None,
) -> bool:
    samples, manifest = assembler.finalize()
    manifest["created_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["files"] = {
        "packet_journal": {
            "path": journal_path.name,
            "bytes": journal_path.stat().st_size,
            "sha256": file_sha256(journal_path),
        },
        "samples": None if samples is None else {
            "path": "samples.bin",
            "bytes": len(samples),
            "sha256": hashlib.sha256(samples).hexdigest(),
        },
    }
    if socket_details is not None:
        manifest["socket"] = socket_details
    if samples is not None:
        temporary_samples = output / ".samples.bin.tmp"
        temporary_samples.write_bytes(samples)
        os.replace(temporary_samples, output / "samples.bin")
    temporary_manifest = output / ".manifest.json.tmp"
    temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    os.replace(temporary_manifest, output / "manifest.json")
    return bool(manifest["complete"])


def receive(args: argparse.Namespace) -> int:
    prepare_output(args.output)
    journal_path = args.output / "packets.qcdp"
    family = socket.AF_INET6 if ipaddress.ip_address(args.bind).version == 6 else socket.AF_INET
    assembler = ShotAssembler()
    start = time.monotonic()
    last_datagram = start
    end_seen_at: float | None = None
    kernel_drop_counter = 0
    overflow_counter_enabled = False

    with socket.socket(family, socket.SOCK_DGRAM) as receiver:
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, args.receive_buffer)
        try:
            receiver.setsockopt(socket.SOL_SOCKET, SO_RXQ_OVFL, 1)
            overflow_counter_enabled = True
        except OSError:
            pass
        receiver.bind((args.bind, args.port))
        actual_receive_buffer = receiver.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        receiver.settimeout(0.1)
        with JournalWriter(journal_path) as journal:
            while True:
                now = time.monotonic()
                if assembler.end_received:
                    if end_seen_at is None:
                        end_seen_at = now
                    if now - end_seen_at >= args.end_grace_ms / 1000.0:
                        break
                elif assembler.total_datagrams == 0:
                    if now - start >= args.wait_timeout:
                        break
                elif now - last_datagram >= args.idle_timeout:
                    break

                try:
                    datagram, ancillary, message_flags, source = receiver.recvmsg(
                        65535, socket.CMSG_SPACE(4)
                    )
                except TimeoutError:
                    continue
                if message_flags & socket.MSG_TRUNC:
                    raise RuntimeError("UDP datagram was truncated by recvmsg")
                for level, kind, value in ancillary:
                    if level == socket.SOL_SOCKET and kind == SO_RXQ_OVFL and len(value) >= 4:
                        kernel_drop_counter = max(
                            kernel_drop_counter, struct.unpack_from("=I", value)[0]
                        )
                last_datagram = time.monotonic()
                source_address, source_port = source[:2]
                normalized_source = str(ipaddress.ip_address(source_address))
                if args.source is not None and normalized_source != args.source:
                    continue
                record = JournalRecord(
                    time.monotonic_ns(), normalized_source, int(source_port), datagram
                )
                journal.write(record)
                assembler.ingest(record)

    complete = write_bundle(
        args.output,
        assembler,
        journal_path,
        {
            "bind_address": args.bind,
            "port": args.port,
            "requested_receive_buffer_bytes": args.receive_buffer,
            "actual_receive_buffer_bytes": actual_receive_buffer,
            "overflow_counter_enabled": overflow_counter_enabled,
            "kernel_drop_counter": kernel_drop_counter,
        },
    )
    print_summary(args.output, assembler, complete)
    return 0 if complete else 2


def replay(args: argparse.Namespace) -> int:
    prepare_output(args.output)
    destination = args.output / "packets.qcdp"
    shutil.copyfile(args.journal, destination)
    assembler = ShotAssembler()
    for record in read_journal(destination):
        assembler.ingest(record)
    complete = write_bundle(args.output, assembler, destination)
    print_summary(args.output, assembler, complete)
    return 0 if complete else 2


def print_summary(output: Path, assembler: ShotAssembler, complete: bool) -> None:
    samples, manifest = assembler.finalize()
    arrival = manifest["arrival"]
    sample_info = manifest["samples"]
    print(f"{'PASS' if complete else 'FAIL'} Q-Crate Data Plane capture")
    print(f"bundle             : {output}")
    print(f"datagrams          : {arrival['datagrams']}")
    print(f"duplicate/reordered: {arrival['duplicate_packets']} / {arrival['reordered_packets']}")
    print(f"frames/samples     : {sample_info['frames']} / {sample_info['count']}")
    if samples is not None:
        print(f"sample SHA-256     : {sample_info['sha256']}")
    else:
        for issue in manifest["issues"]:
            print(f"error              : {issue}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    live = subparsers.add_parser("receive", help="receive and record one finite shot")
    live.add_argument("--bind", default="0.0.0.0")
    live.add_argument("--port", type=int, default=DEFAULT_PORT)
    live.add_argument("--source", type=str, help="accept datagrams only from this IP")
    live.add_argument("--output", type=Path, required=True)
    live.add_argument("--receive-buffer", type=int, default=DEFAULT_RECEIVE_BUFFER)
    live.add_argument("--wait-timeout", type=float, default=30.0)
    live.add_argument("--idle-timeout", type=float, default=2.0)
    live.add_argument("--end-grace-ms", type=int, default=250)

    journal = subparsers.add_parser("replay", help="reanalyze a packet journal")
    journal.add_argument("journal", type=Path)
    journal.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "receive":
        if args.source is not None:
            args.source = str(ipaddress.ip_address(args.source))
        return receive(args)
    if args.command == "replay":
        return replay(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
