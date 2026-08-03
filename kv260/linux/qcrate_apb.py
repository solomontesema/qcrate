#!/usr/bin/env python3
"""Q-Crate APB register smoke tool for KV260 Linux."""
from __future__ import annotations

import argparse
import mmap
import os
import struct
from dataclasses import dataclass

APB_BASE = 0xA001_0000
APB_SIZE = 0x0001_0000

DEVICE_ID_QCRT = 0x5143_5254
VERSION_EXPECTED = 0x0000_0100
STREAM_CLOCK_HZ = 200_000_000
CONTROL_CLOCK_HZ = 100_000_000

SYS_DEVICE_ID = 0x0000
SYS_VERSION = 0x0004
SYS_BUILD_ID = 0x0008
SYS_CAPABILITIES = 0x000C
SYS_SCRATCH = 0x0010
SYS_STREAM_CLOCK_HZ = 0x0014
SYS_CONTROL_CLOCK_HZ = 0x0018

STREAM_BASE = 0x1000
STREAM_CONTROL = STREAM_BASE + 0x000
STREAM_FRAME_LENGTH = STREAM_BASE + 0x004
STREAM_FRAME_COUNT = STREAM_BASE + 0x008
STREAM_MODE = STREAM_BASE + 0x00C
STREAM_STATUS = STREAM_BASE + 0x010
STREAM_COMPLETED_FRAMES = STREAM_BASE + 0x014
STREAM_CURRENT_FRAME_ID = STREAM_BASE + 0x018
STREAM_CURRENT_SAMPLE_INDEX = STREAM_BASE + 0x01C
STREAM_STALL_CYCLES = STREAM_BASE + 0x020
STREAM_IRQ_STATUS = STREAM_BASE + 0x024
STREAM_IRQ_ENABLE = STREAM_BASE + 0x028
STREAM_IRQ_CLEAR = STREAM_BASE + 0x02C


@dataclass(frozen=True)
class Register:
    name: str
    offset: int


REGISTERS = [
    Register("SYS.DEVICE_ID", SYS_DEVICE_ID),
    Register("SYS.VERSION", SYS_VERSION),
    Register("SYS.BUILD_ID", SYS_BUILD_ID),
    Register("SYS.CAPABILITIES", SYS_CAPABILITIES),
    Register("SYS.SCRATCH", SYS_SCRATCH),
    Register("SYS.STREAM_CLOCK_HZ", SYS_STREAM_CLOCK_HZ),
    Register("SYS.CONTROL_CLOCK_HZ", SYS_CONTROL_CLOCK_HZ),
    Register("STREAM.CONTROL", STREAM_CONTROL),
    Register("STREAM.FRAME_LENGTH", STREAM_FRAME_LENGTH),
    Register("STREAM.FRAME_COUNT", STREAM_FRAME_COUNT),
    Register("STREAM.STREAM_MODE", STREAM_MODE),
    Register("STREAM.STATUS", STREAM_STATUS),
    Register("STREAM.COMPLETED_FRAMES", STREAM_COMPLETED_FRAMES),
    Register("STREAM.CURRENT_FRAME_ID", STREAM_CURRENT_FRAME_ID),
    Register("STREAM.CURRENT_SAMPLE_INDEX", STREAM_CURRENT_SAMPLE_INDEX),
    Register("STREAM.STALL_CYCLES", STREAM_STALL_CYCLES),
    Register("STREAM.IRQ_STATUS", STREAM_IRQ_STATUS),
    Register("STREAM.IRQ_ENABLE", STREAM_IRQ_ENABLE),
]


class DevMem32:
    def __init__(self, base: int, size: int) -> None:
        page_size = mmap.PAGESIZE
        page_base = base & ~(page_size - 1)
        page_offset = base - page_base
        map_size = page_offset + size

        self._page_offset = page_offset
        self._fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self._mem = mmap.mmap(
            self._fd,
            map_size,
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE,
            offset=page_base,
        )

    def close(self) -> None:
        self._mem.close()
        os.close(self._fd)

    def __enter__(self) -> "DevMem32":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def read32(self, offset: int) -> int:
        self._check_offset(offset)
        raw = self._mem[self._page_offset + offset:self._page_offset + offset + 4]
        return struct.unpack_from("<I", raw)[0]

    def write32(self, offset: int, value: int) -> None:
        self._check_offset(offset)
        self._mem[self._page_offset + offset:self._page_offset + offset + 4] = (
            struct.pack("<I", value & 0xFFFF_FFFF)
        )

    @staticmethod
    def _check_offset(offset: int) -> None:
        if offset < 0 or (offset % 4) != 0:
            raise ValueError(f"offset must be non-negative and 32-bit aligned: 0x{offset:x}")
        if offset > APB_SIZE - 4:
            raise ValueError(f"offset outside APB window: 0x{offset:x}")


def parse_int(text: str) -> int:
    return int(text, 0)


def expect(actual: int, expected: int, what: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{what}: expected 0x{expected:08x}, got 0x{actual:08x}")
    print(f"OK  {what:<28} 0x{actual:08x}")


def command_dump(regs: DevMem32) -> None:
    for reg in REGISTERS:
        value = regs.read32(reg.offset)
        print(f"0x{reg.offset:04x} {reg.name:<28} 0x{value:08x}")


def command_smoke(regs: DevMem32, skip_writes: bool) -> None:
    print("Q-Crate APB smoke test")
    expect(regs.read32(SYS_DEVICE_ID), DEVICE_ID_QCRT, "DEVICE_ID")
    expect(regs.read32(SYS_VERSION), VERSION_EXPECTED, "VERSION")
    print(f"OK  BUILD_ID                     0x{regs.read32(SYS_BUILD_ID):08x}")
    print(f"OK  CAPABILITIES                 0x{regs.read32(SYS_CAPABILITIES):08x}")
    expect(regs.read32(SYS_STREAM_CLOCK_HZ), STREAM_CLOCK_HZ, "STREAM_CLOCK_HZ")
    expect(regs.read32(SYS_CONTROL_CLOCK_HZ), CONTROL_CLOCK_HZ, "CONTROL_CLOCK_HZ")

    if skip_writes:
        print("SKIP writes requested; read-only smoke test complete")
        return

    original_scratch = regs.read32(SYS_SCRATCH)
    for pattern in (0x0000_0000, 0x5A5A_A5A5, 0x1234_5678):
        regs.write32(SYS_SCRATCH, pattern)
        expect(regs.read32(SYS_SCRATCH), pattern, f"SCRATCH write/read 0x{pattern:08x}")
    regs.write32(SYS_SCRATCH, original_scratch)

    regs.write32(STREAM_FRAME_LENGTH, 16)
    regs.write32(STREAM_FRAME_COUNT, 1)
    regs.write32(STREAM_MODE, 0)
    regs.write32(STREAM_CONTROL, 0)

    expect(regs.read32(STREAM_FRAME_LENGTH), 16, "STREAM.FRAME_LENGTH")
    expect(regs.read32(STREAM_FRAME_COUNT), 1, "STREAM.FRAME_COUNT")
    expect(regs.read32(STREAM_MODE), 0, "STREAM.STREAM_MODE")
    print(f"OK  STREAM.STATUS                0x{regs.read32(STREAM_STATUS):08x}")
    print("PASS qcrate_apb_smoke")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=parse_int, default=APB_BASE,
                        help="physical APB base address, default: 0xA0010000")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("dump", help="read the known Q-Crate APB registers")

    smoke_parser = subparsers.add_parser("smoke", help="run identity and scratch tests")
    smoke_parser.add_argument("--skip-writes", action="store_true",
                              help="only read identity registers")

    read_parser = subparsers.add_parser("read", help="read one 32-bit APB offset")
    read_parser.add_argument("offset", type=parse_int)

    write_parser = subparsers.add_parser("write", help="write one 32-bit APB offset")
    write_parser.add_argument("offset", type=parse_int)
    write_parser.add_argument("value", type=parse_int)

    args = parser.parse_args()

    with DevMem32(args.base, APB_SIZE) as regs:
        if args.command == "dump":
            command_dump(regs)
        elif args.command == "smoke":
            command_smoke(regs, args.skip_writes)
        elif args.command == "read":
            value = regs.read32(args.offset)
            print(f"0x{value:08x}")
        elif args.command == "write":
            regs.write32(args.offset, args.value)
        else:
            parser.error(f"unsupported command: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
