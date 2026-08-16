#!/usr/bin/env python3
"""Host tests for the target-side Q-Crate sequencer utility."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
COMPILER_DIR = REPO_ROOT / "host" / "sequence_compiler"
TARGET_TOOL = (
    REPO_ROOT
    / "kv260/linux/petalinux/qcrate-kv260/project-spec/meta-user"
    / "recipes-apps/qcrate-tools/files/qcrate-sequence"
)

sys.path.insert(0, str(COMPILER_DIR))
import qcrate_sequence as compiler  # noqa: E402


def load_target_tool():
    name = "qcrate_target_sequence"
    loader = importlib.machinery.SourceFileLoader(name, str(TARGET_TOOL))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise RuntimeError("cannot create import specification for target tool")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


target = load_target_tool()


class FakeTransport:
    def __init__(self) -> None:
        self.raw_status = target.STATUS_IDLE
        self.event_count = 0
        self.expected_count = 0
        self.expected_crc = 0
        self.events: list[tuple[int, ...]] = []
        self.calls: list[tuple[int, tuple[int, ...]]] = []

    def _status(self) -> tuple[int, ...]:
        return (
            self.raw_status,
            self.event_count,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
        )

    def exchange(
        self, command: int, payload: tuple[int, ...] = ()
    ) -> tuple[int, ...]:
        self.calls.append((command, payload))
        if command == target.CMD_SEQ_GET_STATUS:
            return self._status()
        if command == target.CMD_SEQ_LOAD_BEGIN:
            self.expected_count, self.expected_crc = payload
            self.events = []
            self.raw_status = target.STATUS_IDLE | target.STATUS_LOAD_ACTIVE
            return payload
        if command == target.CMD_SEQ_LOAD_EVENT:
            self.events.append(payload)
            return (payload[0],)
        if command == target.CMD_SEQ_LOAD_COMMIT:
            self.event_count = self.expected_count
            self.raw_status = target.STATUS_IDLE | target.STATUS_COMMITTED
            return (self.expected_count, self.expected_crc)
        raise AssertionError(f"unexpected command {command}")


class TargetFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            compiler.SequenceEvent(0, 0b01),
            compiler.SequenceEvent(4, 0b11),
            compiler.SequenceEvent(8, 0b10),
            compiler.SequenceEvent(12, 0b00),
        ]
        self.encoded = compiler.encode_sequence(self.events)

    def test_target_accepts_compiler_image(self) -> None:
        image = target.decode_image(self.encoded)
        self.assertEqual(len(image.events), len(self.events))
        self.assertEqual(
            [(event.timestamp, event.output_state, event.flags)
             for event in image.events],
            [(event.timestamp, event.output_state, event.flags)
             for event in self.events],
        )

    def test_target_rejects_crc_corruption(self) -> None:
        corrupted = bytearray(self.encoded)
        corrupted[-1] ^= 0x80
        with self.assertRaisesRegex(target.SequenceError, "CRC mismatch"):
            target.decode_image(bytes(corrupted))

    def test_upload_preserves_little_endian_event_words(self) -> None:
        image = target.decode_image(self.encoded)
        transport = FakeTransport()
        target.load_image(transport, image)

        expected_requests = [
            (
                index,
                event.timestamp & 0xFFFF_FFFF,
                event.timestamp >> 32,
                event.output_state,
                event.flags,
            )
            for index, event in enumerate(image.events)
        ]
        self.assertEqual(transport.events, expected_requests)
        self.assertEqual(transport.event_count, len(self.events))
        self.assertEqual(transport.expected_crc, image.crc32)
        self.assertNotIn("DevMem32", vars(target))

    def test_upload_rejects_locked_memory(self) -> None:
        image = target.decode_image(self.encoded)
        transport = FakeTransport()
        transport.raw_status = (
            target.STATUS_ARMED | target.STATUS_MEMORY_LOCKED
        )
        with self.assertRaisesRegex(RuntimeError, "not writable"):
            target.load_image(transport, image)


class ProtocolTests(unittest.TestCase):
    def test_rpmsg_message_remains_fixed_size(self) -> None:
        self.assertEqual(target.RPMSG_ABI_VERSION, 2)
        self.assertEqual(target.RPMSG_MESSAGE.size, 64)

    def test_r5_validation_error_names_event_and_fault(self) -> None:
        error = target.R5ServiceError(-9, (3, 6))
        self.assertIn("event 3", str(error))
        self.assertIn("safe state", str(error))

    def test_r5_hardware_fault_decodes_status_snapshot(self) -> None:
        payload = (0, 4, 2, 0, (2 << 16) | 9, 0, 0, 0, 0, 0, 0)
        error = target.R5ServiceError(-12, payload)
        self.assertIn("illegal command", str(error))
        self.assertIn("event 2", str(error))


if __name__ == "__main__":
    unittest.main()
