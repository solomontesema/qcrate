# Q-Crate Host Data Plane

## Objective

This directory implements the host side of Q-Crate Data Plane v1. It receives
one finite acquisition shot over UDP, preserves the original datagrams,
reconstructs sample order, and publishes a raw sample file only after every
protocol and continuity check succeeds.

```text
UDP socket
   |
   +--> packets.qcdp append-only journal
   |
   +--> Data Plane v1 decoder
          |
          +--> sequence, duplicate, reorder, and frame checks
          +--> STREAM_INFO and SHOT_END consistency checks
          +--> CRC and sender-error checks
                    |
                    v
          manifest.json + optional samples.bin
```

The implementation is intentionally a finite-shot correctness reference. It
is appropriate for the next KV260 DMA-to-UDP acceptance test. Continuous
operation will require measured queue ownership and likely a compiled receiver
using batched socket I/O; this Python implementation is not presented as a
validated sustained-rate endpoint.

The protocol itself is specified in
[`common/data_plane/README.md`](../../common/data_plane/README.md).

## Capture Bundle

One receiver invocation creates one directory:

```text
shot-0001/
  packets.qcdp
  manifest.json
  samples.bin       present only when manifest.complete is true
```

### `packets.qcdp`

The journal is written before protocol decoding. It preserves valid,
duplicate, malformed, and foreign datagrams together with source identity and
host monotonic receive time. This makes receiver decisions reproducible after
the board or network is no longer available.

All journal integers use network byte order. The file starts with a fixed
16-byte header:

| Offset | Bytes | Field |
|---:|---:|---|
| 0 | 4 | ASCII `QCRJ`, `0x5143524a` |
| 4 | 2 | journal version, 1 |
| 6 | 2 | file-header size, 16 |
| 8 | 8 | reserved, zero |

Each datagram then has a fixed 32-byte record header followed immediately by
the recorded UDP payload:

| Offset | Bytes | Field |
|---:|---:|---|
| 0 | 2 | datagram bytes |
| 2 | 1 | address family, 4 or 6 |
| 3 | 1 | flags, zero in v1 |
| 4 | 2 | UDP source port |
| 6 | 2 | reserved, zero |
| 8 | 8 | host monotonic receive time in nanoseconds |
| 16 | 16 | IPv6 address, or IPv4 followed by 12 zero bytes |

The journal is diagnostic evidence rather than a sample file. It may remain
useful when a shot fails and `samples.bin` is intentionally absent.

### `manifest.json`

The manifest is the machine-readable acceptance result. It includes:

- sender, run, stream, and shot identity;
- exact stream format, rates, center frequency, and timestamp clock;
- terminal sender status and declared totals;
- duplicate, reordered, malformed, foreign, and missing packet diagnostics;
- reconstructed frame/sample counts, CRC-32, and SHA-256;
- packet-journal and sample-file sizes and SHA-256 fingerprints;
- the actual socket receive-buffer size and Linux overflow counter;
- a `complete` Boolean and explicit issue list.

The 64-bit identities are encoded as hexadecimal strings so JSON consumers do
not lose precision through floating-point number handling.

### `samples.bin`

This is the reconstructed payload in frame and sample order, with the exact
little-endian sample encoding announced by `STREAM_INFO`. It is created by an
atomic rename only when all checks pass. An incomplete capture never produces
a plausibly valid-looking raw sample file.

For `IQ_S16_LE`, the output is directly compatible with the existing DSP
viewer:

```bash
python3 host/dsp_model/qcrate_capture_viewer.py \
  build/data_plane/shot-0001/samples.bin \
  --output build/data_plane/shot-0001/capture.png \
  --summary build/data_plane/shot-0001/dsp-report.json
```

## Receiving One Shot

Start the receiver on the development host before triggering the sender:

```bash
python3 host/data_plane/qcrate_receiver.py receive \
  --bind 0.0.0.0 \
  --port 47000 \
  --source 192.168.1.93 \
  --output build/data_plane/shot-0001
```

`--source` is optional but prevents another host from selecting the capture on
a shared network. The output directory must be new or empty. Defaults are:

| Option | Default | Purpose |
|---|---:|---|
| `--receive-buffer` | 16 MiB | Requested kernel UDP receive buffer |
| `--wait-timeout` | 30 s | Wait for the first accepted datagram |
| `--idle-timeout` | 2 s | Stop an unterminated shot after inactivity |
| `--end-grace-ms` | 250 ms | Continue receiving reordered data after `SHOT_END` |

Linux may clamp `SO_RCVBUF` to the configured system maximum and commonly
reports twice the requested accounting value. The manifest records both the
request and returned value. Socket tuning must be based on that evidence and
drop counters, not on an assumption that `setsockopt` granted the request. On
Linux the receiver also enables `SO_RXQ_OVFL`; when supported, the manifest
records the cumulative kernel receive-queue drop counter.

The command returns zero only for a complete shot. Exit status 2 means a
bundle was preserved but at least one integrity condition failed.

## Deterministic Replay

Re-run the exact same assembler against a saved journal without a board:

```bash
python3 host/data_plane/qcrate_receiver.py replay \
  build/data_plane/shot-0001/packets.qcdp \
  --output build/data_plane/shot-0001-replay
```

Replay copies the immutable packet journal into a new bundle and regenerates
the manifest and, when valid, `samples.bin`. This is the normal way to inspect
receiver changes against historical traffic.

## Integrity Policy

Reordered packets are reconstructed and reported. An identical duplicate is
also reported but does not invalidate the shot. Any of the following prevents
sample publication:

- missing or malformed `STREAM_INFO` or `SHOT_END`;
- a packet-sequence gap;
- a truncated or malformed selected datagram;
- conflicting contents using the same packet sequence;
- frame gaps, overlaps, missing boundaries, or noncontiguous IDs;
- disagreement with sender totals or optional payload CRC-32;
- sender-reported discontinuity, overflow, or drop;
- a non-complete terminal status.

Foreign traffic is counted but does not invalidate the selected shot. Before
the first valid `STREAM_INFO`, at most 4096 datagrams are retained in memory to
handle metadata reordering; the journal remains the complete receive record.

## Focused Tests

Run the shared C/Python protocol tests and receiver fault-injection suite:

```bash
python3 common/data_plane/run_tests.py
```

The suite verifies complete reconstruction, pre-metadata and in-shot
reordering, identical and conflicting duplicates, dropped and truncated
packets, IPv4/IPv6 journal replay, and the rule that incomplete bundles do not
contain `samples.bin`. It is host-only and does not run Vivado or PetaLinux.

## Next Integration

The next milestone adds a compiled KV260 sender that requests the existing
finite scatter-gather DMA capture, emits `STREAM_INFO`, packetizes each frame,
and terminates with `SHOT_END`. That sender must use the tracked C codec rather
than duplicate the wire layout. End-to-end acceptance will compare the
receiver's `samples.bin` against the bit-exact DSP model.
