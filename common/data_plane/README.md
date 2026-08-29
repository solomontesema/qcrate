# Q-Crate Data Plane v1

## Purpose

The Q-Crate data plane carries acquired sample data from an instrumentation
node to a host without coupling the receiver to KV260-specific software. The
same wire contract is intended for later FPGA platforms and host or PYNQ
receivers.

```text
ADC or deterministic source -> DSP -> AXI4-Stream -> DMA -> Linux sender
                                                              |
                                                              v
                                                     versioned UDP packets
                                                              |
                                                              v
                                            receiver -> record -> inspect
```

The data plane is deliberately separate from control. UDP carries sample data
and descriptive metadata only. Linux policy and R5 real-time control continue
to use their existing local interfaces; a future remote control service must
be reliable and authenticated rather than overloading this protocol.

## Design Decisions

| Property | Data Plane v1 decision |
|---|---|
| Transport | UDP, with no retransmission in the acquisition path |
| Datagram limit | 1400 bytes, below a standard 1500-byte Ethernet MTU |
| Header | Fixed 64 bytes, network byte order |
| Sample payload | Explicit little-endian words for direct DMA-buffer use |
| Continuity | Monotonic packet sequence plus run, stream, shot, frame, and sample identities |
| Time | A named clock plus integer ticks; validity is explicit |
| Completion | A terminal `SHOT_END` packet with totals and outcome |
| Evolution | Versioned objects, fixed v1 sizes, and zero reserved fields |

At the current 12.5 MS/s, four-byte IQ output, the sample payload is
400 Mb/s. A 64-byte header in a 1400-byte datagram raises the expected UDP
offered rate to approximately 419 Mb/s. The accepted KV260-to-host baseline
supports that rate with about 0.1 percent loss, so receivers must detect and
report loss rather than assume a perfect network.

## Datagram Header

Every datagram starts with this fixed 64-byte header. Multi-byte header and
metadata fields use network byte order (big endian).

| Offset | Bytes | Field | Meaning |
|---:|---:|---|---|
| 0 | 4 | `magic` | ASCII `QCDP`, `0x51434450` |
| 4 | 2 | `version` | Protocol version, currently 1 |
| 6 | 2 | `header_bytes` | Header size, 64 for v1 |
| 8 | 2 | `packet_type` | Metadata, data, termination, or heartbeat |
| 10 | 2 | `payload_format` | Announced or carried sample encoding |
| 12 | 4 | `flags` | Timestamp, frame-boundary, and error indicators |
| 16 | 4 | `stream_id` | Logical stream within one sender process |
| 20 | 8 | `run_id` | Nonzero random identity for one sender process |
| 28 | 8 | `shot_id` | Acquisition identity within the run |
| 36 | 8 | `packet_sequence` | Monotonic datagram sequence within the run |
| 44 | 4 | `frame_id` | Source frame identity for a data packet |
| 48 | 4 | `sample_offset` | First sample's offset within that frame |
| 52 | 2 | `sample_count` | Samples carried by this packet |
| 54 | 2 | `sample_bytes` | Bytes per sample |
| 56 | 8 | `timestamp_ticks` | First-sample timestamp when valid |

A stream instance is identified by sender network address, `run_id`, and
`stream_id`. The sender generates a new nonzero random `run_id` at every
process start. `shot_id` increments for each acquisition. `packet_sequence`
starts at zero and increments for every emitted datagram, including metadata,
heartbeats, and termination packets. It must never silently restart within a
run.

A `DATA` packet belongs to one frame and must not cross a frame boundary.
`FRAME_START` requires `sample_offset == 0`; `FRAME_END` marks the packet
containing that frame's final sample. Gaps in either packet sequence or sample
offset are observable discontinuities.

`STREAM_INFO` and `DATA` carry the stream's registered format and sample
width. `SHOT_END` and `HEARTBEAT` use format `NONE` and a zero sample width.
Non-data packets have zero frame ID, sample offset, and sample count.

## Packet Types

### `STREAM_INFO` (`1`)

`STREAM_INFO` describes the stream before its data packets. Its fixed 64-byte
payload contains:

- samples per frame;
- exact sample-rate numerator and denominator;
- exact timestamp-rate numerator and denominator;
- signed center frequency in hertz;
- channel count, component width, and fixed-point fraction width;
- timestamp clock identifier;
- a sender-defined configuration identity.

Rates are rational integers rather than floating-point approximations. The
current DSP stream reports two signed 16-bit Q1.15 components per sample at
12.5 MS/s, with the shared Q-Crate 200 MHz timebase when an exact hardware
timestamp is available.

| Offset | Bytes | Field |
|---:|---:|---|
| 0 | 2 | object version, 1 |
| 2 | 2 | object size, 64 |
| 4 | 4 | samples per frame |
| 8 | 8 | sample-rate numerator |
| 16 | 8 | sample-rate denominator |
| 24 | 8 | timestamp-rate numerator |
| 32 | 8 | timestamp-rate denominator |
| 40 | 8 | signed center frequency in hertz |
| 48 | 2 | channel count |
| 50 | 2 | bits per component |
| 52 | 2 | fractional bits |
| 54 | 2 | timestamp clock ID |
| 56 | 8 | configuration ID |

### `DATA` (`2`)

`DATA` carries one of the registered sample formats:

| Value | Name | Four-byte little-endian sample |
|---:|---|---|
| 1 | `COUNTER_U32_LE` | bits 31:16 frame ID, bits 15:0 sample index |
| 2 | `IQ_S16_LE` | bits 31:16 signed Q, bits 15:0 signed I, both Q1.15 |

The payload size must equal `sample_count * sample_bytes`. Current formats use
four bytes per sample. Explicit little endian preserves the DMA word layout on
the Zynq UltraScale+ A53 while remaining unambiguous to any receiver.

### `SHOT_END` (`3`)

`SHOT_END` has a fixed 64-byte payload containing status, end flags, total
frames, samples, data packets and payload bytes, plus the first and last data
packet sequence. Status distinguishes complete, aborted, DMA-error, timeout,
source-error, and sender-error outcomes.

| Offset | Bytes | Field |
|---:|---:|---|
| 0 | 4 | shot status |
| 4 | 4 | terminal flags |
| 8 | 4 | total completed frames |
| 12 | 4 | reserved, zero |
| 16 | 8 | total samples |
| 24 | 8 | total `DATA` packets |
| 32 | 8 | first `DATA` packet sequence |
| 40 | 8 | last `DATA` packet sequence |
| 48 | 8 | total sample-payload bytes |
| 56 | 4 | optional payload CRC-32 |
| 60 | 4 | reserved, zero |

Status values are `0` complete, `1` aborted, `2` DMA error, `3` timeout,
`4` source error, and `5` sender error.

The optional payload CRC is standard IEEE CRC-32 over all sample payload bytes
in transmission order. It is meaningful only when `PAYLOAD_CRC_VALID` is set.
Sequence and count checks remain mandatory even when a CRC is present.

### `HEARTBEAT` (`4`)

A heartbeat has no payload and uses payload format `NONE`. It keeps an idle
stream observable without pretending that samples were acquired. It still
consumes a packet-sequence value.

## Flags And Time

The header flags are:

- `TIMESTAMP_VALID`: `timestamp_ticks` identifies the first sample;
- `FRAME_START` and `FRAME_END`: explicit frame boundaries;
- `DISCONTINUITY_BEFORE`: the sender knows data was lost before this packet;
- `PAYLOAD_CRC_VALID`: the `SHOT_END` CRC is valid;
- `SOURCE_OVERFLOW`: acquisition hardware reported overflow;
- `SENDER_DROP`: Linux dropped data after acquisition;
- `END_OF_STREAM`: no more packets are expected for this stream lifecycle.

Timestamp clock IDs distinguish the shared Q-Crate timebase, TAI nanoseconds,
and monotonic nanoseconds. A sender must not manufacture precision: until the
DMA path captures the exact first-sample hardware timestamp, it transmits zero
and leaves `TIMESTAMP_VALID` clear. A later timestamp implementation can then
be added without changing the header.

## Loss And Receiver Behavior

UDP delivery can be lost, duplicated, delayed, or reordered. A conforming
receiver therefore:

1. keys state by sender, run, and stream identity;
2. classifies packet-sequence gaps, duplicates, and reordering;
3. checks frame IDs and sample offsets independently;
4. rejects malformed sizes, unknown v1 object sizes, and nonzero reserved fields;
5. records incomplete shots as incomplete rather than silently filling data;
6. compares observed totals with `SHOT_END` when it arrives.

Retransmission is intentionally absent from v1 because blocking acquisition on
a slow receiver hides overload and destroys deterministic behavior. Loss is a
measured result. Reliable capture is obtained by capacity planning, socket and
queue sizing, and eventually flow-controlled buffering at the acquisition
boundary.

## Compatibility Rules

- A v1 decoder accepts only the 64-byte v1 header.
- Reserved fields in fixed metadata objects must be zero.
- Unknown versions, packet types, formats, or clock IDs are rejected.
- New optional behavior uses previously reserved flag bits.
- A layout or semantic change that cannot be ignored requires a new version.

These rules keep accidental ABI drift visible in tests and packet recordings.

## Source And Tests

- `qcrate_data_protocol.h` and `.c`: portable C constants and explicit codecs
  for the future KV260 sender.
- `host/data_plane/qcrate_data_protocol.py`: Python codec for host tooling.
- `tests/`: C/Python round-trip, malformed-input, and shared golden-vector
  tests.

Run the focused, host-only protocol checks from the repository root:

```bash
python3 common/data_plane/run_tests.py
```

The test runner compiles the C codec with strict warnings and verifies that C
and Python produce the same exact 64-byte header. It does not require Vivado,
PetaLinux, a network connection, or a KV260.
