#!/usr/bin/env python3
"""Q-Crate Data Plane v1 wire codec; contains no socket or receiver state."""
from __future__ import annotations

import enum
import struct
from dataclasses import dataclass


MAGIC = 0x51434450
VERSION = 1
HEADER_BYTES = 64
MAX_DATAGRAM_BYTES = 1400
MAX_PAYLOAD_BYTES = MAX_DATAGRAM_BYTES - HEADER_BYTES
STREAM_INFO_BYTES = 64
SHOT_END_BYTES = 64

HEADER_STRUCT = struct.Struct("!IHHHHIIQQQIIHHQ")
STREAM_INFO_STRUCT = struct.Struct("!HHIQQQQqHHHHQ")
SHOT_END_STRUCT = struct.Struct("!IIIIQQQQQII")


class PacketType(enum.IntEnum):
    STREAM_INFO = 1
    DATA = 2
    SHOT_END = 3
    HEARTBEAT = 4


class PayloadFormat(enum.IntEnum):
    NONE = 0
    COUNTER_U32_LE = 1
    IQ_S16_LE = 2


class TimestampClock(enum.IntEnum):
    UNSPECIFIED = 0
    QCRATE_TIMEBASE = 1
    TAI_NS = 2
    MONOTONIC_NS = 3


class Flags(enum.IntFlag):
    TIMESTAMP_VALID = 1 << 0
    FRAME_START = 1 << 1
    FRAME_END = 1 << 2
    DISCONTINUITY_BEFORE = 1 << 3
    PAYLOAD_CRC_VALID = 1 << 4
    SOURCE_OVERFLOW = 1 << 5
    SENDER_DROP = 1 << 6
    END_OF_STREAM = 1 << 7


class ShotStatus(enum.IntEnum):
    COMPLETE = 0
    ABORTED = 1
    DMA_ERROR = 2
    TIMEOUT = 3
    SOURCE_ERROR = 4
    SENDER_ERROR = 5


SAMPLE_BYTES = {
    PayloadFormat.NONE: 0,
    PayloadFormat.COUNTER_U32_LE: 4,
    PayloadFormat.IQ_S16_LE: 4,
}


def unsigned(value: int, bits: int, name: str) -> int:
    """Validate one unsigned wire integer."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 1 << bits:
        raise ValueError(f"{name} does not fit uint{bits}")
    return value


@dataclass(frozen=True)
class Header:
    packet_type: PacketType
    payload_format: PayloadFormat
    flags: Flags
    stream_id: int
    run_id: int
    shot_id: int
    packet_sequence: int
    frame_id: int = 0
    sample_offset: int = 0
    sample_count: int = 0
    sample_bytes: int = 0
    timestamp_ticks: int = 0
    header_bytes: int = HEADER_BYTES

    def encode(self) -> bytes:
        """Encode the fixed v1 header in network byte order."""
        fields = (
            unsigned(MAGIC, 32, "magic"),
            unsigned(VERSION, 16, "version"),
            unsigned(self.header_bytes, 16, "header_bytes"),
            unsigned(int(self.packet_type), 16, "packet_type"),
            unsigned(int(self.payload_format), 16, "payload_format"),
            unsigned(int(self.flags), 32, "flags"),
            unsigned(self.stream_id, 32, "stream_id"),
            unsigned(self.run_id, 64, "run_id"),
            unsigned(self.shot_id, 64, "shot_id"),
            unsigned(self.packet_sequence, 64, "packet_sequence"),
            unsigned(self.frame_id, 32, "frame_id"),
            unsigned(self.sample_offset, 32, "sample_offset"),
            unsigned(self.sample_count, 16, "sample_count"),
            unsigned(self.sample_bytes, 16, "sample_bytes"),
            unsigned(self.timestamp_ticks, 64, "timestamp_ticks"),
        )
        if self.header_bytes != HEADER_BYTES:
            raise ValueError(f"v1 header_bytes must be {HEADER_BYTES}")
        return HEADER_STRUCT.pack(*fields)

    @classmethod
    def decode(cls, data: bytes) -> "Header":
        """Decode and validate the fixed v1 header."""
        if len(data) < HEADER_BYTES:
            raise ValueError("datagram is shorter than the v1 header")
        unpacked = HEADER_STRUCT.unpack_from(data)
        magic, version, header_bytes = unpacked[:3]
        if magic != MAGIC:
            raise ValueError(f"bad data-plane magic 0x{magic:08x}")
        if version != VERSION:
            raise ValueError(f"unsupported data-plane version {version}")
        if header_bytes != HEADER_BYTES:
            raise ValueError(f"v1 header_bytes must be {HEADER_BYTES}")
        try:
            packet_type = PacketType(unpacked[3])
            payload_format = PayloadFormat(unpacked[4])
        except ValueError as exc:
            raise ValueError(f"unsupported packet or payload type: {exc}") from exc
        return cls(
            packet_type=packet_type,
            payload_format=payload_format,
            flags=Flags(unpacked[5]),
            stream_id=unpacked[6],
            run_id=unpacked[7],
            shot_id=unpacked[8],
            packet_sequence=unpacked[9],
            frame_id=unpacked[10],
            sample_offset=unpacked[11],
            sample_count=unpacked[12],
            sample_bytes=unpacked[13],
            timestamp_ticks=unpacked[14],
            header_bytes=header_bytes,
        )


@dataclass(frozen=True)
class StreamInfo:
    frame_samples: int
    sample_rate_numerator: int
    sample_rate_denominator: int
    timestamp_rate_numerator: int
    timestamp_rate_denominator: int
    center_frequency_hz: int
    channel_count: int
    component_bits: int
    fraction_bits: int
    timestamp_clock_id: TimestampClock
    config_id: int

    def encode(self) -> bytes:
        """Encode fixed stream metadata in network byte order."""
        if self.sample_rate_denominator == 0 or self.timestamp_rate_denominator == 0:
            raise ValueError("rate denominators must be nonzero")
        if not -(1 << 63) <= self.center_frequency_hz < 1 << 63:
            raise ValueError("center_frequency_hz does not fit int64")
        return STREAM_INFO_STRUCT.pack(
            VERSION,
            STREAM_INFO_BYTES,
            unsigned(self.frame_samples, 32, "frame_samples"),
            unsigned(self.sample_rate_numerator, 64, "sample_rate_numerator"),
            unsigned(self.sample_rate_denominator, 64, "sample_rate_denominator"),
            unsigned(self.timestamp_rate_numerator, 64, "timestamp_rate_numerator"),
            unsigned(self.timestamp_rate_denominator, 64, "timestamp_rate_denominator"),
            self.center_frequency_hz,
            unsigned(self.channel_count, 16, "channel_count"),
            unsigned(self.component_bits, 16, "component_bits"),
            unsigned(self.fraction_bits, 16, "fraction_bits"),
            unsigned(int(self.timestamp_clock_id), 16, "timestamp_clock_id"),
            unsigned(self.config_id, 64, "config_id"),
        )

    @classmethod
    def decode(cls, payload: bytes) -> "StreamInfo":
        if len(payload) != STREAM_INFO_BYTES:
            raise ValueError(f"STREAM_INFO payload must contain {STREAM_INFO_BYTES} bytes")
        values = STREAM_INFO_STRUCT.unpack(payload)
        if values[0] != VERSION or values[1] != STREAM_INFO_BYTES:
            raise ValueError("unsupported STREAM_INFO version or size")
        if values[4] == 0 or values[6] == 0:
            raise ValueError("rate denominators must be nonzero")
        try:
            clock_id = TimestampClock(values[11])
        except ValueError as exc:
            raise ValueError(f"unsupported timestamp clock {values[11]}") from exc
        return cls(*values[2:11], timestamp_clock_id=clock_id, config_id=values[12])


@dataclass(frozen=True)
class ShotEnd:
    status: int
    end_flags: int
    total_frames: int
    total_samples: int
    total_data_packets: int
    first_packet_sequence: int
    last_packet_sequence: int
    payload_bytes: int
    payload_crc32: int = 0

    def encode(self) -> bytes:
        return SHOT_END_STRUCT.pack(
            unsigned(self.status, 32, "status"),
            unsigned(self.end_flags, 32, "end_flags"),
            unsigned(self.total_frames, 32, "total_frames"),
            0,
            unsigned(self.total_samples, 64, "total_samples"),
            unsigned(self.total_data_packets, 64, "total_data_packets"),
            unsigned(self.first_packet_sequence, 64, "first_packet_sequence"),
            unsigned(self.last_packet_sequence, 64, "last_packet_sequence"),
            unsigned(self.payload_bytes, 64, "payload_bytes"),
            unsigned(self.payload_crc32, 32, "payload_crc32"),
            0,
        )

    @classmethod
    def decode(cls, payload: bytes) -> "ShotEnd":
        if len(payload) != SHOT_END_BYTES:
            raise ValueError(f"SHOT_END payload must contain {SHOT_END_BYTES} bytes")
        values = SHOT_END_STRUCT.unpack(payload)
        if values[3] != 0 or values[10] != 0:
            raise ValueError("SHOT_END reserved fields must be zero")
        return cls(*values[:3], *values[4:10])


@dataclass(frozen=True)
class Packet:
    header: Header
    payload: bytes = b""

    def encode(self) -> bytes:
        validate_payload(self.header, self.payload)
        encoded = self.header.encode() + self.payload
        if len(encoded) > MAX_DATAGRAM_BYTES:
            raise ValueError(f"datagram exceeds {MAX_DATAGRAM_BYTES} bytes")
        return encoded

    @classmethod
    def decode(cls, datagram: bytes) -> "Packet":
        if len(datagram) > MAX_DATAGRAM_BYTES:
            raise ValueError(f"datagram exceeds {MAX_DATAGRAM_BYTES} bytes")
        header = Header.decode(datagram)
        payload = datagram[header.header_bytes :]
        validate_payload(header, payload)
        return cls(header, payload)


def validate_payload(header: Header, payload: bytes) -> None:
    """Validate packet-type size and current sample-format invariants."""
    expected_sample_bytes = SAMPLE_BYTES[header.payload_format]
    if header.packet_type in (PacketType.STREAM_INFO, PacketType.DATA):
        if header.payload_format is PayloadFormat.NONE:
            raise ValueError(f"{header.packet_type.name} packet requires a sample format")
    elif header.payload_format is not PayloadFormat.NONE:
        raise ValueError(f"{header.packet_type.name} packet requires payload format NONE")

    if header.sample_bytes != expected_sample_bytes:
        raise ValueError(
            f"{header.payload_format.name} requires {expected_sample_bytes} bytes per sample"
        )
    if not header.flags & Flags.TIMESTAMP_VALID and header.timestamp_ticks != 0:
        raise ValueError("timestamp_ticks is nonzero without TIMESTAMP_VALID")

    if header.packet_type is PacketType.DATA:
        if header.sample_count == 0:
            raise ValueError("DATA packet contains zero samples")
        expected = header.sample_count * header.sample_bytes
        if len(payload) != expected:
            raise ValueError(f"DATA payload contains {len(payload)} bytes; expected {expected}")
        if header.flags & Flags.FRAME_START and header.sample_offset != 0:
            raise ValueError("FRAME_START packet has a nonzero sample offset")
        return

    if header.sample_count != 0:
        raise ValueError(f"{header.packet_type.name} packet must contain zero sample_count")
    if header.frame_id != 0 or header.sample_offset != 0:
        raise ValueError(f"{header.packet_type.name} packet must not identify a data frame")
    expected_payload = {
        PacketType.STREAM_INFO: STREAM_INFO_BYTES,
        PacketType.SHOT_END: SHOT_END_BYTES,
        PacketType.HEARTBEAT: 0,
    }[header.packet_type]
    if len(payload) != expected_payload:
        raise ValueError(
            f"{header.packet_type.name} payload contains {len(payload)} bytes; "
            f"expected {expected_payload}"
        )
