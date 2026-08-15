#!/usr/bin/env python3
"""Host tests for the target-side Q-Crate sequencer utility."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import inspect
import mmap
import os
import sys
import unittest
from unittest import mock
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


class FakeRegisters:
    def __init__(self) -> None:
        self.values = {
            target.SEQ_STATUS: target.STATUS_IDLE,
            target.SEQ_EVENT_COUNT: 0,
            target.SEQ_ACTIVE_EVENT: 0,
            target.SEQ_COMPLETED_SHOTS: 0,
            target.SEQ_FAULT_INFO: 0,
            target.SEQ_TIMEBASE_LO: 1,
            target.SEQ_TIMEBASE_HI: 0,
            target.SEQ_START_TIME_LO: 0,
            target.SEQ_START_TIME_HI: 0,
            target.SEQ_ELAPSED_LO: 0,
            target.SEQ_ELAPSED_HI: 0,
        }

    def read32(self, offset: int) -> int:
        return self.values.get(offset, 0)

    def write32(self, offset: int, value: int) -> None:
        self.values[offset] = value & 0xFFFF_FFFF


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
        regs = FakeRegisters()
        target.load_image(regs, image)

        expected_words = [
            target.WORD.unpack_from(image.payload, offset)[0]
            for offset in range(0, len(image.payload), target.WORD.size)
        ]
        actual_words = [
            regs.read32(target.SEQ_EVENT_MEMORY + index * target.WORD.size)
            for index in range(len(expected_words))
        ]
        self.assertEqual(actual_words, expected_words)
        self.assertEqual(regs.read32(target.SEQ_EVENT_COUNT), len(self.events))

    def test_upload_rejects_locked_memory(self) -> None:
        image = target.decode_image(self.encoded)
        regs = FakeRegisters()
        regs.values[target.SEQ_STATUS] = (
            target.STATUS_ARMED | target.STATUS_MEMORY_LOCKED
        )
        with self.assertRaisesRegex(RuntimeError, "not writable"):
            target.load_image(regs, image)


class MmioAccessTests(unittest.TestCase):
    def test_devmem_uses_typed_words_instead_of_mmap_slices(self) -> None:
        mapping = mmap.mmap(-1, target.APB_SIZE)
        fd = os.open(os.devnull, os.O_RDWR)

        with mock.patch.object(target.os, "open", return_value=fd), \
             mock.patch.object(target.mmap, "mmap", return_value=mapping):
            with target.DevMem32(0, target.APB_SIZE) as regs:
                regs.write32(0, 0xA5A5_5A5A)
                self.assertEqual(regs.read32(0), 0xA5A5_5A5A)
                self.assertEqual(mapping[:4], target.WORD.pack(0xA5A5_5A5A))

        implementation = inspect.getsource(target.DevMem32)
        self.assertNotIn("self._mem[", implementation)


if __name__ == "__main__":
    unittest.main()
