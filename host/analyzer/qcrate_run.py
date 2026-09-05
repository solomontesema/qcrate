#!/usr/bin/env python3
"""Read and validate Q-Crate QCRUN/QIDX acquisition bundles."""
from __future__ import annotations

import enum
import json
import struct
from dataclasses import dataclass, replace
from pathlib import Path


QIDX_MAGIC = 0x51494458
QIDX_VERSION = 1
QIDX_HEADER_BYTES = 32
QIDX_RECORD_BYTES = 128
QIDX_HEADER = struct.Struct("!IHHH6xQI4x")
UINT64_MAX = (1 << 64) - 1


class ShotState(enum.IntEnum):
    COMPLETE = 1
    INCOMPLETE = 2


class ShotIssue(enum.IntFlag):
    PROTOCOL = 1 << 0
    MISSING = 1 << 1
    CONFLICT = 1 << 2
    FRAME = 1 << 3
    CRC = 1 << 4
    SENDER = 1 << 5
    TIMESTAMP = 1 << 6
    CONTINUITY = 1 << 7


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("!H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("!I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("!Q", data, offset)[0]


@dataclass(frozen=True)
class ShotRecord:
    shot_id: int
    state: ShotState
    issues: ShotIssue
    first_packet_sequence: int
    last_packet_sequence: int
    first_sample_timestamp: int
    first_receive_ns: int
    last_receive_ns: int
    sample_offset: int
    sample_bytes: int
    frame_count: int
    frame_samples: int
    data_packets: int
    datagrams: int
    duplicate_packets: int
    reordered_packets: int
    missing_packets: int
    payload_crc32: int
    source_flags: int
    payload_format: int
    sample_bytes_per_word: int
    config_id: int

    @property
    def complete(self) -> bool:
        return self.state is ShotState.COMPLETE

    @property
    def issue_names(self) -> tuple[str, ...]:
        return tuple(
            issue.name.lower()
            for issue in ShotIssue
            if self.issues & issue
        )

    @classmethod
    def decode(cls, data: bytes) -> "ShotRecord":
        if len(data) != QIDX_RECORD_BYTES:
            raise ValueError("QIDX record must contain 128 bytes")
        if any(data[120:128]):
            raise ValueError("QIDX record reserved bytes are nonzero")
        try:
            state = ShotState(_u32(data, 8))
        except ValueError as exc:
            raise ValueError(f"unsupported QIDX shot state {_u32(data, 8)}") from exc
        known_issues = sum(int(issue) for issue in ShotIssue)
        issue_value = _u32(data, 12)
        if issue_value & ~known_issues:
            raise ValueError(f"QIDX record has unknown issue bits 0x{issue_value:08x}")
        record = cls(
            shot_id=_u64(data, 0),
            state=state,
            issues=ShotIssue(issue_value),
            first_packet_sequence=_u64(data, 16),
            last_packet_sequence=_u64(data, 24),
            first_sample_timestamp=_u64(data, 32),
            first_receive_ns=_u64(data, 40),
            last_receive_ns=_u64(data, 48),
            sample_offset=_u64(data, 56),
            sample_bytes=_u64(data, 64),
            frame_count=_u32(data, 72),
            frame_samples=_u32(data, 76),
            data_packets=_u32(data, 80),
            datagrams=_u32(data, 84),
            duplicate_packets=_u32(data, 88),
            reordered_packets=_u32(data, 92),
            missing_packets=_u32(data, 96),
            payload_crc32=_u32(data, 100),
            source_flags=_u32(data, 104),
            payload_format=_u16(data, 108),
            sample_bytes_per_word=_u16(data, 110),
            config_id=_u64(data, 112),
        )
        if record.complete:
            if record.issues or record.sample_offset == UINT64_MAX or not record.sample_bytes:
                raise ValueError("COMPLETE QIDX record has invalid issue/sample fields")
        elif record.sample_offset != UINT64_MAX or record.sample_bytes:
            raise ValueError("INCOMPLETE QIDX record exposes sample bytes")
        if record.last_receive_ns < record.first_receive_ns:
            raise ValueError("QIDX receive times run backwards")
        return record


def _mapping_number(mapping: object, key: str, default: float = 0.0) -> float:
    if not isinstance(mapping, dict):
        return default
    value = mapping.get(key, default)
    return float(value) if isinstance(value, (int, float)) else default


def _manifest_integer(manifest: dict[str, object] | None, key: str) -> int:
    if manifest is None:
        return 0
    value = manifest.get(key, 0)
    return int(value) if isinstance(value, (int, float)) else 0


@dataclass(frozen=True)
class RunHealth:
    state: str
    duration_seconds: float
    duration_source: str
    complete_shots: int
    incomplete_shots: int
    skipped_shot_ids: int
    shot_rate_hz: float
    sample_payload_mbps: float
    udp_payload_mbps: float | None
    datagrams: int
    duplicate_packets: int
    reordered_packets: int
    missing_packets: int
    malformed_packets: int
    conflicting_packets: int
    foreign_packets: int
    late_packets: int
    preselection_overflow: int
    kernel_receive_drops: int
    continuity_errors: int
    token_queue_high_water: int | None
    dma_ready_high_water: int | None
    dma_stall_cycles: int | None
    missed_triggers: int | None
    starvation_events: int | None
    skipped_triggers: int | None
    dma_error_events: int | None
    recorder_cpu_percent: float | None
    sender_cpu_percent: float | None
    sender_complete: bool | None

    @property
    def record_integrity_ok(self) -> bool:
        return not any((
            self.incomplete_shots,
            self.skipped_shot_ids,
            self.missing_packets,
            self.malformed_packets,
            self.conflicting_packets,
            self.kernel_receive_drops,
            self.continuity_errors,
        ))

    @property
    def acquisition_integrity_ok(self) -> bool:
        return not any((
            self.skipped_triggers or 0,
            self.dma_error_events or 0,
            self.missed_triggers or 0,
        ))

    @property
    def integrity_ok(self) -> bool:
        return (
            self.record_integrity_ok
            and self.acquisition_integrity_ok
            and self.sender_complete is not False
        )


@dataclass(frozen=True)
class RunBundle:
    path: Path
    run_id: int
    stream_id: int
    shots: tuple[ShotRecord, ...]
    manifest: dict[str, object] | None
    sender_report: dict[str, object] | None
    samples_path: Path

    @property
    def complete_shots(self) -> tuple[ShotRecord, ...]:
        return tuple(shot for shot in self.shots if shot.complete)

    @property
    def incomplete_shots(self) -> tuple[ShotRecord, ...]:
        return tuple(shot for shot in self.shots if not shot.complete)

    @property
    def run_complete(self) -> bool | None:
        if self.manifest is None:
            return None
        return bool(self.manifest.get("complete", False))

    def sample_rate_hz(self, fallback: float | None = None) -> tuple[float, str]:
        stream = self.manifest.get("stream") if self.manifest else None
        if isinstance(stream, dict):
            numerator = stream.get("sample_rate_numerator")
            denominator = stream.get("sample_rate_denominator")
            if isinstance(numerator, int) and isinstance(denominator, int) and denominator:
                return numerator / denominator, "run metadata"
        if fallback is None or fallback <= 0:
            raise ValueError("run has no sample rate; provide a positive fallback")
        return float(fallback), "fallback"

    def read_samples(self, shot: ShotRecord) -> bytes:
        if not shot.complete:
            raise ValueError(f"shot {shot.shot_id} is incomplete")
        with self.samples_path.open("rb") as samples:
            samples.seek(shot.sample_offset)
            payload = samples.read(shot.sample_bytes)
        if len(payload) != shot.sample_bytes:
            raise ValueError(f"shot {shot.shot_id} sample extent is truncated")
        return payload

    def health(self) -> RunHealth:
        complete = self.complete_shots
        incomplete = self.incomplete_shots
        skipped = sum(
            max(0, right.shot_id - left.shot_id - 1)
            for left, right in zip(self.shots, self.shots[1:])
        )
        first_receive = min((shot.first_receive_ns for shot in self.shots), default=0)
        last_receive = max((shot.last_receive_ns for shot in self.shots), default=0)
        measured_duration = (
            (last_receive - first_receive) / 1.0e9
            if last_receive >= first_receive and first_receive else 0.0
        )
        timing = self.manifest.get("timing") if self.manifest else None
        manifest_duration = _mapping_number(timing, "duration_seconds")
        duration = manifest_duration or measured_duration
        duration_source = "recorder" if manifest_duration else "QIDX receive times"
        sample_bytes = sum(shot.sample_bytes for shot in complete)
        performance = self.manifest.get("performance") if self.manifest else None
        sample_mbps = _mapping_number(performance, "sample_payload_mbps")
        if not sample_mbps and duration > 0:
            sample_mbps = sample_bytes * 8.0 / duration / 1.0e6
        shot_rate = _mapping_number(performance, "shot_rate_hz")
        if not shot_rate and duration > 0:
            shot_rate = len(complete) / duration
        udp_mbps_value = _mapping_number(performance, "udp_payload_mbps", -1.0)
        udp_mbps = udp_mbps_value if udp_mbps_value >= 0 else None
        sender_health = (
            self.sender_report.get("health") if self.sender_report else None
        )
        sender_timing = (
            self.sender_report.get("timing") if self.sender_report else None
        )
        recorder_cpu = _mapping_number(timing, "process_cpu_percent", -1.0)
        sender_cpu = _mapping_number(sender_timing, "process_cpu_percent", -1.0)
        sender_complete = (
            bool(self.sender_report.get("complete")) if self.sender_report else None
        )
        health = RunHealth(
            state="recording",
            duration_seconds=duration,
            duration_source=duration_source,
            complete_shots=len(complete),
            incomplete_shots=len(incomplete),
            skipped_shot_ids=skipped,
            shot_rate_hz=shot_rate,
            sample_payload_mbps=sample_mbps,
            udp_payload_mbps=udp_mbps,
            datagrams=_manifest_integer(self.manifest, "datagrams_journaled")
            or sum(shot.datagrams for shot in self.shots),
            duplicate_packets=_manifest_integer(self.manifest, "duplicate_packets")
            or sum(shot.duplicate_packets for shot in self.shots),
            reordered_packets=_manifest_integer(self.manifest, "reordered_packets")
            or sum(shot.reordered_packets for shot in self.shots),
            missing_packets=sum(shot.missing_packets for shot in self.shots),
            malformed_packets=_manifest_integer(self.manifest, "malformed_packets"),
            conflicting_packets=_manifest_integer(self.manifest, "conflicting_packets"),
            foreign_packets=_manifest_integer(self.manifest, "foreign_packets"),
            late_packets=_manifest_integer(self.manifest, "late_packets"),
            preselection_overflow=_manifest_integer(
                self.manifest, "preselection_overflow"
            ),
            kernel_receive_drops=_manifest_integer(
                self.manifest, "kernel_receive_drops"
            ),
            continuity_errors=int(bool(
                self.manifest and self.manifest.get("run_continuity_error")
            )),
            token_queue_high_water=(
                int(_mapping_number(sender_health, "token_queue_high_water"))
                if isinstance(sender_health, dict) else None
            ),
            dma_ready_high_water=(
                int(_mapping_number(sender_health, "dma_ready_high_water"))
                if isinstance(sender_health, dict) else None
            ),
            dma_stall_cycles=(
                int(_mapping_number(sender_health, "dma_stall_cycles"))
                if isinstance(sender_health, dict) else None
            ),
            missed_triggers=(
                int(_mapping_number(sender_health, "missed_triggers"))
                if isinstance(sender_health, dict) else None
            ),
            starvation_events=(
                int(_mapping_number(sender_health, "starvation_events"))
                if isinstance(sender_health, dict) else None
            ),
            skipped_triggers=(
                int(_mapping_number(sender_health, "skipped_triggers"))
                if isinstance(sender_health, dict) else None
            ),
            dma_error_events=(
                int(_mapping_number(sender_health, "dma_error_events"))
                if isinstance(sender_health, dict) else None
            ),
            recorder_cpu_percent=recorder_cpu if recorder_cpu >= 0 else None,
            sender_cpu_percent=sender_cpu if sender_cpu >= 0 else None,
            sender_complete=sender_complete,
        )
        state = (
            "failed" if not health.integrity_ok
            else "recording" if self.run_complete is None
            else "accepted" if self.run_complete and sender_complete is not False
            else "failed"
        )
        return replace(health, state=state)

    @classmethod
    def open(cls, path: Path, *, allow_in_progress: bool = False) -> "RunBundle":
        root = path.resolve()
        index_path = root / "shots.qidx"
        samples_path = root / "samples.iq16"
        raw = index_path.read_bytes()
        if len(raw) < QIDX_HEADER_BYTES:
            if not allow_in_progress:
                raise ValueError("QIDX header is truncated")
            if not samples_path.exists():
                raise ValueError("run sample file is missing")
            return cls(root, 0, 0, (), None, None, samples_path)
        magic, version, header_bytes, record_bytes, run_id, stream_id = (
            QIDX_HEADER.unpack_from(raw)
        )
        if magic != QIDX_MAGIC or version != QIDX_VERSION:
            raise ValueError("unsupported QIDX magic or version")
        if header_bytes != QIDX_HEADER_BYTES or record_bytes != QIDX_RECORD_BYTES:
            raise ValueError("unsupported QIDX header or record size")
        if any(raw[10:16]) or any(raw[28:32]):
            raise ValueError("QIDX header reserved bytes are nonzero")
        records_raw = raw[QIDX_HEADER_BYTES:]
        remainder = len(records_raw) % QIDX_RECORD_BYTES
        if remainder:
            if not allow_in_progress:
                raise ValueError("QIDX final record is truncated")
            records_raw = records_raw[:-remainder]
        shots = tuple(
            ShotRecord.decode(records_raw[offset : offset + QIDX_RECORD_BYTES])
            for offset in range(0, len(records_raw), QIDX_RECORD_BYTES)
        )
        sample_file_bytes = samples_path.stat().st_size
        previous_end = 0
        previous_shot_id: int | None = None
        for shot in shots:
            if previous_shot_id is not None and shot.shot_id <= previous_shot_id:
                raise ValueError("QIDX shot IDs are not strictly increasing")
            previous_shot_id = shot.shot_id
            if not shot.complete:
                continue
            end = shot.sample_offset + shot.sample_bytes
            if end > sample_file_bytes:
                raise ValueError(f"shot {shot.shot_id} exceeds samples.iq16")
            if shot.sample_offset < previous_end:
                raise ValueError(f"shot {shot.shot_id} overlaps prior sample data")
            previous_end = end
        manifest_path = root / "run.json"
        manifest = None
        if manifest_path.exists():
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict) or parsed.get("format") != "qcrate-run-v1":
                raise ValueError("unsupported run.json format")
            if int(str(parsed.get("run_id", "0")), 0) != run_id:
                raise ValueError("run.json and QIDX run IDs disagree")
            if int(str(parsed.get("stream_id", "0")), 0) != stream_id:
                raise ValueError("run.json and QIDX stream IDs disagree")
            manifest = parsed
        sender_report = None
        sender_path = root / "sender.json"
        if sender_path.exists():
            parsed = json.loads(sender_path.read_text(encoding="utf-8"))
            if (
                not isinstance(parsed, dict)
                or parsed.get("format") != "qcrate-sender-report-v1"
            ):
                raise ValueError("unsupported sender.json format")
            if int(str(parsed.get("run_id", "0")), 0) != run_id:
                raise ValueError("sender.json and QIDX run IDs disagree")
            if int(str(parsed.get("stream_id", "0")), 0) != stream_id:
                raise ValueError("sender.json and QIDX stream IDs disagree")
            sender_report = parsed
        return cls(root, run_id, stream_id, shots, manifest, sender_report,
                   samples_path)
