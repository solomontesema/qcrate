#!/usr/bin/env python3
"""Focused tests for the Q-Crate sequence compiler and binary contract."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import qcrate_sequence as sequence  # noqa: E402


def document(pulses: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format": sequence.JSON_FORMAT,
        "tick_hz": sequence.TICK_HZ,
        "pulses": pulses,
    }


class CompileTests(unittest.TestCase):
    def test_simultaneous_edges_share_one_event(self) -> None:
        events = sequence.compile_document(
            document(
                [
                    {"channel": 0, "start_tick": 0, "duration_ticks": 10},
                    {"channel": 1, "start_tick": 0, "duration_ticks": 5},
                ]
            )
        )
        self.assertEqual(
            events,
            [
                sequence.SequenceEvent(0, 0b11),
                sequence.SequenceEvent(5, 0b01),
                sequence.SequenceEvent(10, 0b00),
            ],
        )

    def test_relative_channel_timing_is_preserved(self) -> None:
        events = sequence.compile_document(
            document(
                [
                    {"channel": 0, "start_tick": 2, "duration_ticks": 4},
                    {"channel": 1, "start_tick": 4, "duration_ticks": 3},
                ]
            )
        )
        self.assertEqual(
            events,
            [
                sequence.SequenceEvent(2, 0b01),
                sequence.SequenceEvent(4, 0b11),
                sequence.SequenceEvent(6, 0b10),
                sequence.SequenceEvent(7, 0b00),
            ],
        )

    def test_adjacent_pulses_form_one_continuous_interval(self) -> None:
        events = sequence.compile_document(
            document(
                [
                    {"channel": 0, "start_tick": 0, "duration_ticks": 5},
                    {"channel": 0, "start_tick": 5, "duration_ticks": 4},
                ]
            )
        )
        self.assertEqual(
            events,
            [sequence.SequenceEvent(0, 0b01), sequence.SequenceEvent(9, 0b00)],
        )

    def test_same_channel_overlap_is_rejected(self) -> None:
        with self.assertRaisesRegex(sequence.SequenceError, "overlaps"):
            sequence.compile_document(
                document(
                    [
                        {"channel": 0, "start_tick": 2, "duration_ticks": 5},
                        {"channel": 0, "start_tick": 6, "duration_ticks": 2},
                    ]
                )
            )

    def test_unknown_source_key_is_rejected(self) -> None:
        source = document(
            [{"channel": 0, "start_tick": 0, "duration_ticks": 1}]
        )
        source["clock_hz"] = sequence.TICK_HZ
        with self.assertRaisesRegex(sequence.SequenceError, "unknown keys"):
            sequence.compile_document(source)


class BinaryFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            sequence.SequenceEvent(0, 0b01),
            sequence.SequenceEvent(10, 0b00),
        ]

    def test_binary_round_trip(self) -> None:
        image = sequence.encode_sequence(self.events)
        self.assertEqual(len(image), sequence.HEADER.size + 2 * sequence.EVENT.size)
        self.assertEqual(sequence.decode_sequence(image), self.events)

    def test_payload_corruption_is_rejected(self) -> None:
        image = bytearray(sequence.encode_sequence(self.events))
        image[-1] ^= 0x80
        with self.assertRaisesRegex(sequence.SequenceError, "CRC mismatch"):
            sequence.decode_sequence(bytes(image))

    def test_trailing_bytes_are_rejected(self) -> None:
        image = sequence.encode_sequence(self.events) + b"\x00"
        with self.assertRaisesRegex(sequence.SequenceError, "expected exactly"):
            sequence.decode_sequence(image)

    def test_noncanonical_event_table_is_rejected(self) -> None:
        invalid_tables = (
            [sequence.SequenceEvent(1, 1), sequence.SequenceEvent(1, 0)],
            [sequence.SequenceEvent(1, 1)],
            [sequence.SequenceEvent(1, 4), sequence.SequenceEvent(2, 0)],
            [sequence.SequenceEvent(1, 1, 1), sequence.SequenceEvent(2, 0)],
        )
        for events in invalid_tables:
            with self.subTest(events=events):
                with self.assertRaises(sequence.SequenceError):
                    sequence.validate_events(events)

    def test_duplicate_json_key_is_rejected(self) -> None:
        source = (
            '{"format":"qcrate-sequence-v1","tick_hz":200000000,'
            '"tick_hz":200000000,"pulses":[]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(sequence.SequenceError, "duplicate JSON key"):
                sequence.load_json(path)


if __name__ == "__main__":
    unittest.main()
