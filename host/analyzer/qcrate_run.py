#!/usr/bin/env python3
"""Read and validate Q-Crate QCRUN/QIDX acquisition bundles."""
from __future__ import annotations

import enum
import json
import os
import struct
from collections import OrderedDict
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


def _load_json_report(
    path: Path,
    *,
    expected_format: str,
    run_id: int,
    stream_id: int,
) -> dict[str, object] | None:
    if not path.exists():
        return None
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or parsed.get("format") != expected_format:
        raise ValueError(f"unsupported {path.name} format")
    if int(str(parsed.get("run_id", "0")), 0) != run_id:
        raise ValueError(f"{path.name} and QIDX run IDs disagree")
    if int(str(parsed.get("stream_id", "0")), 0) != stream_id:
        raise ValueError(f"{path.name} and QIDX stream IDs disagree")
    return parsed


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


def _build_health(
    *,
    manifest: dict[str, object] | None,
    sender_report: dict[str, object] | None,
    measured_duration: float,
    complete_shots: int,
    incomplete_shots: int,
    skipped_shot_ids: int,
    sample_bytes: int,
    datagrams: int,
    duplicate_packets: int,
    reordered_packets: int,
    missing_packets: int,
) -> RunHealth:
    timing = manifest.get("timing") if manifest else None
    manifest_duration = _mapping_number(timing, "duration_seconds")
    duration = manifest_duration or measured_duration
    duration_source = "recorder" if manifest_duration else "QIDX receive times"
    performance = manifest.get("performance") if manifest else None
    sample_mbps = _mapping_number(performance, "sample_payload_mbps")
    if not sample_mbps and duration > 0:
        sample_mbps = sample_bytes * 8.0 / duration / 1.0e6
    shot_rate = _mapping_number(performance, "shot_rate_hz")
    if not shot_rate and duration > 0:
        shot_rate = complete_shots / duration
    udp_mbps_value = _mapping_number(performance, "udp_payload_mbps", -1.0)
    udp_mbps = udp_mbps_value if udp_mbps_value >= 0 else None
    sender_health = sender_report.get("health") if sender_report else None
    sender_timing = sender_report.get("timing") if sender_report else None
    recorder_cpu = _mapping_number(timing, "process_cpu_percent", -1.0)
    sender_cpu = _mapping_number(sender_timing, "process_cpu_percent", -1.0)
    sender_complete = (
        bool(sender_report.get("complete")) if sender_report else None
    )
    health = RunHealth(
        state="recording",
        duration_seconds=duration,
        duration_source=duration_source,
        complete_shots=complete_shots,
        incomplete_shots=incomplete_shots,
        skipped_shot_ids=skipped_shot_ids,
        shot_rate_hz=shot_rate,
        sample_payload_mbps=sample_mbps,
        udp_payload_mbps=udp_mbps,
        datagrams=_manifest_integer(manifest, "datagrams_journaled") or datagrams,
        duplicate_packets=(
            _manifest_integer(manifest, "duplicate_packets") or duplicate_packets
        ),
        reordered_packets=(
            _manifest_integer(manifest, "reordered_packets") or reordered_packets
        ),
        missing_packets=missing_packets,
        malformed_packets=_manifest_integer(manifest, "malformed_packets"),
        conflicting_packets=_manifest_integer(manifest, "conflicting_packets"),
        foreign_packets=_manifest_integer(manifest, "foreign_packets"),
        late_packets=_manifest_integer(manifest, "late_packets"),
        preselection_overflow=_manifest_integer(manifest, "preselection_overflow"),
        kernel_receive_drops=_manifest_integer(manifest, "kernel_receive_drops"),
        continuity_errors=int(bool(
            manifest and manifest.get("run_continuity_error")
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
    run_complete = None if manifest is None else bool(manifest.get("complete", False))
    state = (
        "failed" if not health.integrity_ok
        else "recording" if run_complete is None
        else "accepted" if run_complete and sender_complete is not False
        else "failed"
    )
    return replace(health, state=state)


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
        sample_bytes = sum(shot.sample_bytes for shot in complete)
        return _build_health(
            manifest=self.manifest,
            sender_report=self.sender_report,
            measured_duration=measured_duration,
            complete_shots=len(complete),
            incomplete_shots=len(incomplete),
            skipped_shot_ids=skipped,
            sample_bytes=sample_bytes,
            datagrams=sum(shot.datagrams for shot in self.shots),
            duplicate_packets=sum(shot.duplicate_packets for shot in self.shots),
            reordered_packets=sum(shot.reordered_packets for shot in self.shots),
            missing_packets=sum(shot.missing_packets for shot in self.shots),
        )

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
        manifest = _load_json_report(
            root / "run.json", expected_format="qcrate-run-v1",
            run_id=run_id, stream_id=stream_id,
        )
        sender_report = _load_json_report(
            root / "sender.json", expected_format="qcrate-sender-report-v1",
            run_id=run_id, stream_id=stream_id,
        )
        return cls(root, run_id, stream_id, shots, manifest, sender_report,
                   samples_path)


class RunIndex:
    """Bounded-memory, append-aware QIDX catalog for interactive clients.

    `RunBundle` remains the strict whole-run acceptance reader. This class
    keeps the QIDX and sample files open, scans each committed index record
    once for health accounting, and retains only a small random-access cache.
    """

    def __init__(self, root: Path, *, cache_records: int = 1024) -> None:
        if cache_records < 1:
            raise ValueError("QIDX cache must contain at least one record")
        self.path = root.resolve()
        self.index_path = self.path / "shots.qidx"
        self.samples_path = self.path / "samples.iq16"
        if not self.samples_path.exists():
            raise ValueError("run sample file is missing")
        self._index_fd = os.open(self.index_path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            self._samples_fd = os.open(
                self.samples_path, os.O_RDONLY | os.O_CLOEXEC
            )
        except Exception:
            os.close(self._index_fd)
            raise
        self._cache_limit = cache_records
        self._cache: OrderedDict[int, ShotRecord] = OrderedDict()
        self.run_id = 0
        self.stream_id = 0
        self.record_count = 0
        self._scanned_count = 0
        self.complete_count = 0
        self.incomplete_count = 0
        self.manifest: dict[str, object] | None = None
        self.sender_report: dict[str, object] | None = None
        self._report_signatures: dict[str, tuple[int, int, int] | None] = {}
        self._previous_shot_id: int | None = None
        self._previous_sample_end = 0
        self._first_receive_ns = 0
        self._last_receive_ns = 0
        self._skipped_shot_ids = 0
        self._sample_bytes = 0
        self._datagrams = 0
        self._duplicate_packets = 0
        self._reordered_packets = 0
        self._missing_packets = 0

    @classmethod
    def open(cls, path: Path, *, cache_records: int = 1024) -> "RunIndex":
        catalog = cls(path, cache_records=cache_records)
        try:
            catalog.refresh()
        except Exception:
            catalog.close()
            raise
        return catalog

    @property
    def cache_entries(self) -> int:
        return len(self._cache)

    @property
    def run_complete(self) -> bool | None:
        if self.manifest is None:
            return None
        return bool(self.manifest.get("complete", False))

    def close(self) -> None:
        for name in ("_index_fd", "_samples_fd"):
            descriptor = getattr(self, name, -1)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(self, name, -1)

    def __enter__(self) -> "RunIndex":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _cache_record(self, ordinal: int, shot: ShotRecord) -> None:
        self._cache[ordinal] = shot
        self._cache.move_to_end(ordinal)
        while len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)

    def _decode_range(self, start: int, stop: int) -> tuple[ShotRecord, ...]:
        if start < 0 or stop < start or stop > self.record_count:
            raise IndexError("QIDX record range is outside the catalog")
        count = stop - start
        if not count:
            return ()
        offset = QIDX_HEADER_BYTES + start * QIDX_RECORD_BYTES
        raw = os.pread(self._index_fd, count * QIDX_RECORD_BYTES, offset)
        if len(raw) != count * QIDX_RECORD_BYTES:
            raise ValueError("QIDX record range changed while being read")
        records = tuple(
            ShotRecord.decode(raw[index:index + QIDX_RECORD_BYTES])
            for index in range(0, len(raw), QIDX_RECORD_BYTES)
        )
        for ordinal, shot in enumerate(records, start):
            self._cache_record(ordinal, shot)
        return records

    def record_at(self, ordinal: int) -> ShotRecord:
        if ordinal < 0 or ordinal >= self.record_count:
            raise IndexError("QIDX record ordinal is outside the catalog")
        cached = self._cache.get(ordinal)
        if cached is not None:
            self._cache.move_to_end(ordinal)
            return cached
        return self._decode_range(ordinal, ordinal + 1)[0]

    def records(self, start: int, stop: int) -> tuple[ShotRecord, ...]:
        return self._decode_range(start, stop)

    def find_shot_id(self, shot_id: int) -> int | None:
        low = 0
        high = self.record_count
        while low < high:
            middle = (low + high) // 2
            current = self.record_at(middle).shot_id
            if current < shot_id:
                low = middle + 1
            else:
                high = middle
        if low < self.record_count and self.record_at(low).shot_id == shot_id:
            return low
        return None

    def _validate_header(self, raw: bytes) -> None:
        magic, version, header_bytes, record_bytes, run_id, stream_id = (
            QIDX_HEADER.unpack_from(raw)
        )
        if magic != QIDX_MAGIC or version != QIDX_VERSION:
            raise ValueError("unsupported QIDX magic or version")
        if header_bytes != QIDX_HEADER_BYTES or record_bytes != QIDX_RECORD_BYTES:
            raise ValueError("unsupported QIDX header or record size")
        if any(raw[10:16]) or any(raw[28:32]):
            raise ValueError("QIDX header reserved bytes are nonzero")
        if self.run_id and (run_id != self.run_id or stream_id != self.stream_id):
            raise ValueError("QIDX identity changed while the run was open")
        self.run_id = run_id
        self.stream_id = stream_id

    def _accumulate(self, shot: ShotRecord, sample_file_bytes: int) -> None:
        if self._previous_shot_id is not None:
            if shot.shot_id <= self._previous_shot_id:
                raise ValueError("QIDX shot IDs are not strictly increasing")
            self._skipped_shot_ids += max(
                0, shot.shot_id - self._previous_shot_id - 1
            )
        self._previous_shot_id = shot.shot_id
        if not self._first_receive_ns:
            self._first_receive_ns = shot.first_receive_ns
        self._last_receive_ns = max(self._last_receive_ns, shot.last_receive_ns)
        self._datagrams += shot.datagrams
        self._duplicate_packets += shot.duplicate_packets
        self._reordered_packets += shot.reordered_packets
        self._missing_packets += shot.missing_packets
        if shot.complete:
            end = shot.sample_offset + shot.sample_bytes
            if end > sample_file_bytes:
                raise ValueError(f"shot {shot.shot_id} exceeds samples.iq16")
            if shot.sample_offset < self._previous_sample_end:
                raise ValueError(f"shot {shot.shot_id} overlaps prior sample data")
            self._previous_sample_end = end
            self._sample_bytes += shot.sample_bytes
            self.complete_count += 1
        else:
            self.incomplete_count += 1

    @staticmethod
    def _signature(path: Path) -> tuple[int, int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_ino, stat.st_mtime_ns, stat.st_size

    def _refresh_report(self, name: str, expected_format: str) -> bool:
        path = self.path / name
        signature = self._signature(path)
        if self._report_signatures.get(name) == signature:
            return False
        report = _load_json_report(
            path, expected_format=expected_format,
            run_id=self.run_id, stream_id=self.stream_id,
        ) if signature is not None else None
        if name == "run.json":
            self.manifest = report
        else:
            self.sender_report = report
        self._report_signatures[name] = signature
        return True

    def refresh(self) -> bool:
        """Scan only records appended since the preceding refresh."""
        file_bytes = os.fstat(self._index_fd).st_size
        if file_bytes < QIDX_HEADER_BYTES:
            if self.run_id:
                raise ValueError("QIDX file shrank while the run was open")
            return False
        header = os.pread(self._index_fd, QIDX_HEADER_BYTES, 0)
        if len(header) != QIDX_HEADER_BYTES:
            raise ValueError("QIDX header changed while being read")
        self._validate_header(header)
        available = (file_bytes - QIDX_HEADER_BYTES) // QIDX_RECORD_BYTES
        trailing_bytes = (file_bytes - QIDX_HEADER_BYTES) % QIDX_RECORD_BYTES
        if available < self.record_count:
            raise ValueError("QIDX file shrank while the run was open")
        changed = available != self.record_count
        self.record_count = available
        sample_file_bytes = os.fstat(self._samples_fd).st_size
        while self._scanned_count < available:
            stop = min(available, self._scanned_count + 4096)
            records = self._decode_range(self._scanned_count, stop)
            for shot in records:
                self._accumulate(shot, sample_file_bytes)
            self._scanned_count = stop
        if self.run_id:
            changed |= self._refresh_report("run.json", "qcrate-run-v1")
            changed |= self._refresh_report(
                "sender.json", "qcrate-sender-report-v1"
            )
        if self.run_complete is True and trailing_bytes:
            raise ValueError("QIDX final record is truncated")
        return changed

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
        payload = os.pread(self._samples_fd, shot.sample_bytes, shot.sample_offset)
        if len(payload) != shot.sample_bytes:
            raise ValueError(f"shot {shot.shot_id} sample extent is truncated")
        return payload

    def health(self) -> RunHealth:
        measured_duration = (
            (self._last_receive_ns - self._first_receive_ns) / 1.0e9
            if self._last_receive_ns >= self._first_receive_ns
            and self._first_receive_ns else 0.0
        )
        return _build_health(
            manifest=self.manifest,
            sender_report=self.sender_report,
            measured_duration=measured_duration,
            complete_shots=self.complete_count,
            incomplete_shots=self.incomplete_count,
            skipped_shot_ids=self._skipped_shot_ids,
            sample_bytes=self._sample_bytes,
            datagrams=self._datagrams,
            duplicate_packets=self._duplicate_packets,
            reordered_packets=self._reordered_packets,
            missing_packets=self._missing_packets,
        )
