# Q-Crate sequence format

The Q-Crate sequence image is the versioned contract between the Python
compiler, Linux userspace, R5 FreeRTOS firmware, and the PL event memory. It is
not a memory dump of an implementation-specific C object.

All integers are unsigned and little-endian. The file contains one 32-byte
header followed immediately by `event_count` 16-byte event records. No trailing
bytes are permitted.

## Header

| Offset | Width | Field | Required value |
|---:|---:|---|---|
| `0x00` | 4 | magic | ASCII `QSEQ` |
| `0x04` | 2 | format version | 1 |
| `0x06` | 2 | header size | 32 |
| `0x08` | 4 | event count | 2 through 128 |
| `0x0c` | 4 | event size | 16 |
| `0x10` | 4 | tick rate | 200000000 Hz |
| `0x14` | 4 | payload CRC-32 | CRC-32 of all event bytes |
| `0x18` | 8 | reserved | zero |

The CRC uses the standard reflected CRC-32 implemented by Python's
`zlib.crc32()`. It protects only the event payload; all other header fields are
validated independently.

## Event record

| Word | Field | Rule |
|---:|---|---|
| 0 | timestamp `[31:0]` | 64-bit tick offset, low word |
| 1 | timestamp `[63:32]` | 64-bit tick offset, high word |
| 2 | output state | bits `[1:0]` are channels 1 and 0; upper bits are zero |
| 3 | flags | zero in format version 1 |

Events describe the complete two-channel output state, not set/clear commands.
Timestamps are relative to the trigger accepted by the PL scheduler and must be
strictly increasing. The initial state is zero, every event must change it, and
the final event must restore zero. One event therefore updates both channels
simultaneously without duplicate timestamps or hardware arbitration.

At 200 MHz one tick is 5 ns. The sequence compiler accepts pulse intervals as
`[start_tick, start_tick + duration_ticks)`, then converts them into this
canonical state-transition table.

The C declarations in `qcrate_sequence_format.h` define the matching Linux/R5
layout. Format changes require an explicit version change; never reinterpret an
existing version silently.

The development-PC compiler is `host/sequence_compiler/qcrate_sequence.py`.
The PetaLinux image installs an independent target validator and APB loader as
`qcrate-sequence`; its build and board commands are documented in
`kv260/linux/petalinux/README.md`.
