#!/usr/bin/env python3
"""Compile and inspect deterministic Q-Crate pulse-sequence images."""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


JSON_FORMAT = "qcrate-sequence-v1"
MAGIC = b"QSEQ"
FORMAT_VERSION = 1
TICK_HZ = 200_000_000
MIN_EVENTS = 2
MAX_EVENTS = 128
OUTPUT_MASK = 0x3
UINT64_MAX = (1 << 64) - 1

HEADER = struct.Struct("<4sHHIIIIQ")
EVENT = struct.Struct("<QII")


class SequenceError(ValueError):
    """Raised when a source document or compiled image violates the format."""


@dataclass(frozen=True)
class SequenceEvent:
    timestamp: int
    output_state: int
    flags: int = 0


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SequenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _check_keys(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str],
    context: str,
) -> None:
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing:
        raise SequenceError(f"{context} is missing: {', '.join(missing)}")
    if unknown:
        raise SequenceError(f"{context} has unknown keys: {', '.join(unknown)}")


def _uint(value: Any, *, maximum: int, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SequenceError(f"{context} must be an integer")
    if value < 0 or value > maximum:
        raise SequenceError(f"{context} must be between 0 and {maximum}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            document = json.load(stream, object_pairs_hook=_object_without_duplicate_keys)
    except json.JSONDecodeError as error:
        raise SequenceError(f"invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise SequenceError("sequence document must be a JSON object")
    return document


def compile_document(document: dict[str, Any]) -> list[SequenceEvent]:
    _check_keys(
        document,
        required={"format", "tick_hz", "pulses"},
        optional={"name"},
        context="sequence",
    )
    if document["format"] != JSON_FORMAT:
        raise SequenceError(f"sequence format must be {JSON_FORMAT!r}")
    if "name" in document and not isinstance(document["name"], str):
        raise SequenceError("sequence.name must be a string")
    if _uint(document["tick_hz"], maximum=UINT64_MAX, context="tick_hz") != TICK_HZ:
        raise SequenceError(f"tick_hz must be {TICK_HZ}")

    pulses = document["pulses"]
    if not isinstance(pulses, list) or not pulses:
        raise SequenceError("pulses must be a non-empty array")

    intervals: list[list[tuple[int, int, int]]] = [[], []]
    for index, pulse in enumerate(pulses):
        context = f"pulse[{index}]"
        if not isinstance(pulse, dict):
            raise SequenceError(f"{context} must be an object")
        _check_keys(
            pulse,
            required={"channel", "start_tick", "duration_ticks"},
            optional={"name"},
            context=context,
        )
        if "name" in pulse and not isinstance(pulse["name"], str):
            raise SequenceError(f"{context}.name must be a string")
        channel = _uint(pulse["channel"], maximum=1, context=f"{context}.channel")
        start = _uint(
            pulse["start_tick"], maximum=UINT64_MAX, context=f"{context}.start_tick"
        )
        duration = _uint(
            pulse["duration_ticks"],
            maximum=UINT64_MAX,
            context=f"{context}.duration_ticks",
        )
        if duration == 0:
            raise SequenceError(f"{context}.duration_ticks must be nonzero")
        if duration > UINT64_MAX - start:
            raise SequenceError(f"{context} end timestamp exceeds 64 bits")
        intervals[channel].append((start, start + duration, index))

    for channel, channel_intervals in enumerate(intervals):
        channel_intervals.sort()
        previous_end: int | None = None
        previous_index: int | None = None
        for start, end, index in channel_intervals:
            if previous_end is not None and start < previous_end:
                raise SequenceError(
                    f"pulse[{index}] overlaps pulse[{previous_index}] on channel {channel}"
                )
            previous_end = end
            previous_index = index

    changes: dict[int, tuple[int, int]] = {}
    for channel, channel_intervals in enumerate(intervals):
        bit = 1 << channel
        for start, end, _ in channel_intervals:
            clear_mask, set_mask = changes.get(start, (0, 0))
            changes[start] = (clear_mask, set_mask | bit)
            clear_mask, set_mask = changes.get(end, (0, 0))
            changes[end] = (clear_mask | bit, set_mask)

    events: list[SequenceEvent] = []
    state = 0
    for timestamp in sorted(changes):
        clear_mask, set_mask = changes[timestamp]
        next_state = (state & ~clear_mask) | set_mask
        if next_state != state:
            events.append(SequenceEvent(timestamp, next_state))
        state = next_state

    validate_events(events)
    return events


def validate_events(events: list[SequenceEvent]) -> None:
    if not MIN_EVENTS <= len(events) <= MAX_EVENTS:
        raise SequenceError(
            f"event count must be between {MIN_EVENTS} and {MAX_EVENTS}"
        )

    previous_timestamp: int | None = None
    previous_state = 0
    for index, event in enumerate(events):
        _uint(event.timestamp, maximum=UINT64_MAX, context=f"event[{index}].timestamp")
        _uint(event.output_state, maximum=UINT64_MAX, context=f"event[{index}].state")
        _uint(event.flags, maximum=UINT64_MAX, context=f"event[{index}].flags")
        if previous_timestamp is not None and event.timestamp <= previous_timestamp:
            raise SequenceError("event timestamps must be strictly increasing")
        if event.output_state & ~OUTPUT_MASK:
            raise SequenceError(f"event[{index}] uses reserved output-state bits")
        if event.flags != 0:
            raise SequenceError(f"event[{index}] flags must be zero in format version 1")
        if event.output_state == previous_state:
            raise SequenceError(f"event[{index}] does not change the output state")
        previous_timestamp = event.timestamp
        previous_state = event.output_state

    if previous_state != 0:
        raise SequenceError("final event must restore both outputs low")


def encode_sequence(events: list[SequenceEvent]) -> bytes:
    validate_events(events)
    payload = b"".join(
        EVENT.pack(event.timestamp, event.output_state, event.flags) for event in events
    )
    checksum = zlib.crc32(payload) & 0xFFFF_FFFF
    header = HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        HEADER.size,
        len(events),
        EVENT.size,
        TICK_HZ,
        checksum,
        0,
    )
    return header + payload


def decode_sequence(image: bytes) -> list[SequenceEvent]:
    if len(image) < HEADER.size:
        raise SequenceError("sequence image is shorter than its header")
    (
        magic,
        version,
        header_size,
        event_count,
        event_size,
        tick_hz,
        expected_crc,
        reserved,
    ) = HEADER.unpack_from(image)
    if magic != MAGIC:
        raise SequenceError("sequence image has the wrong magic")
    if version != FORMAT_VERSION:
        raise SequenceError(f"unsupported sequence format version: {version}")
    if header_size != HEADER.size or event_size != EVENT.size:
        raise SequenceError("sequence image has incompatible record sizes")
    if tick_hz != TICK_HZ:
        raise SequenceError(f"sequence tick rate must be {TICK_HZ}")
    if reserved != 0:
        raise SequenceError("sequence header reserved field must be zero")
    if not MIN_EVENTS <= event_count <= MAX_EVENTS:
        raise SequenceError(
            f"event count must be between {MIN_EVENTS} and {MAX_EVENTS}"
        )

    expected_size = header_size + event_count * event_size
    if len(image) != expected_size:
        raise SequenceError(
            f"sequence image is {len(image)} bytes; expected exactly {expected_size}"
        )
    payload = image[header_size:]
    actual_crc = zlib.crc32(payload) & 0xFFFF_FFFF
    if actual_crc != expected_crc:
        raise SequenceError(
            f"event CRC mismatch: expected 0x{expected_crc:08x}, got 0x{actual_crc:08x}"
        )

    events = [
        SequenceEvent(*EVENT.unpack_from(payload, index * EVENT.size))
        for index in range(event_count)
    ]
    validate_events(events)
    return events


def write_atomic(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def inspect_events(events: list[SequenceEvent]) -> None:
    print(f"tick rate   : {TICK_HZ} Hz (5 ns/tick)")
    print(f"event count : {len(events)}")
    print("index       tick       time_ns  ch1 ch0")
    for index, event in enumerate(events):
        print(
            f"{index:5d} {event.timestamp:10d} {event.timestamp * 5:13d}"
            f"    {(event.output_state >> 1) & 1}   {event.output_state & 1}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile", help="compile JSON to .qseq")
    compile_parser.add_argument("source", type=Path)
    compile_parser.add_argument("output", type=Path)

    inspect_parser = subparsers.add_parser("inspect", help="validate and list events")
    inspect_parser.add_argument("image", type=Path)

    validate_parser = subparsers.add_parser("validate", help="validate a .qseq image")
    validate_parser.add_argument("image", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "compile":
            events = compile_document(load_json(args.source))
            image = encode_sequence(events)
            write_atomic(args.output, image)
            print(f"Wrote {len(events)} events ({len(image)} bytes) to {args.output}")
        else:
            events = decode_sequence(args.image.read_bytes())
            if args.command == "inspect":
                inspect_events(events)
            else:
                print(f"PASS {args.image}: {len(events)} valid events")
    except (OSError, SequenceError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
