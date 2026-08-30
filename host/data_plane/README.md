# Q-Crate Host Data Plane

## Objective

This directory implements the host side of Q-Crate Data Plane v1. It contains
both the finite-shot Python correctness reference and the compiled sustained
run recorder. Both preserve the original datagrams, reconstruct sample order,
and publish sample bytes only after protocol, continuity, frame, timestamp,
sender-status, and CRC checks succeed.

```text
KV260 qcrate-streamer
          |
          v
  Data Plane v1 UDP
          |
          v
   qcrate-recorder (C)
     |       |       |
     |       |       +--> run.json
     |       +----------> shots.qidx
     +------------------> packets.qcdp + samples.iq16
                              |
                              v
                   future qcrate-analyzer GUI
```

The compiled recorder uses Linux `recvmmsg()` to drain UDP in batches and
never waits for visualization. It is the DP-5C2 ingest boundary for repeated
triggered acquisition. The Python receiver remains the simpler one-shot
reference, deterministic journal replay tool, and independent implementation
against which format decisions can be checked.

The protocol itself is specified in
[`common/data_plane/README.md`](../../common/data_plane/README.md).

## Sustained Run Recorder

Build the dependency-free C11 executable with strict compiler warnings:

```bash
python3 host/data_plane/build_recorder.py
```

Start it on the host before the KV260 sender. The output directory must not
already exist:

```bash
build/host/qcrate-recorder \
  --bind 192.168.1.92 \
  --source 192.168.1.93 \
  --port 47000 \
  --output build/data_plane/dp5c2-run-001
```

Then transmit a finite repeated-trigger run from the board:

```bash
sudo qcrate-streamer \
  --destination 192.168.1.92 \
  --triggered-shots 100 \
  --banks 4
```

Unlike the DP-5C1 packet-count check, no `socat` process or parallel
`tcpdump` terminal is required. A successful sender terminates the run with an
`END_OF_STREAM` heartbeat; the recorder then waits a short reordering grace
period, closes every shot, writes `run.json` atomically, and exits.

One invocation creates this run directory:

```text
dp5c2-run-001/
  packets.qcdp     immutable arrival-order UDP journal
  samples.iq16     concatenated payloads from complete shots only
  shots.qidx       fixed-width per-shot publication and integrity index
  run.json         run outcome and socket/drop counters
```

`samples.iq16` is a derived publication, not the forensic source of truth. A
shot with a gap, collision, bad frame boundary, invalid timestamp, sender
error, or CRC mismatch gets an `INCOMPLETE` QIDX record and contributes no
sample bytes. Later valid shots may still be published at indexed offsets.
Reordered packets and byte-identical duplicates are audited but are not data
loss and do not invalidate an otherwise exact shot.

The binary contracts, durability ordering, and recovery rules are specified
in [`RUN_FORMAT.md`](RUN_FORMAT.md). Exit status is zero only for a terminal,
uninterrupted run with at least one complete shot, no incomplete shots, no
selected malformed packets, no sequence collision, and no kernel receive
queue drops. Exit status 2 means evidence was preserved but the run did not
meet that acceptance contract.

The planned `qcrate-analyzer` is deliberately a separate Python process. It
will use `shots.qidx` and `samples.iq16` for fast offline inspection and a
later Unix notification socket for live updates. GUI latency must never enter
the UDP receive or shot-ownership path, and the same analyzer contract can be
ported later to a PYNQ-Z2 host.

## Python Finite-Shot Bundle

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

The suite strictly compiles the recorder and verifies complete multi-shot UDP
reconstruction, a dropped-packet quarantine, reordered and duplicate packet
auditing, finite-shot reconstruction, malformed/truncated packets, IPv4/IPv6
journal replay, and cross-language protocol vectors. It is host-only and does
not run Vivado or PetaLinux.

## KV260 Integration

The compiled KV260 sender now requests the existing finite scatter-gather DMA
capture and uses the tracked C packetizer to emit `STREAM_INFO`, framed data,
and `SHOT_END`. Its architecture, PetaLinux recipe, and board procedure are in
[`kv260/linux/data_plane/README.md`](../../kv260/linux/data_plane/README.md).
End-to-end acceptance compares this receiver's `samples.bin` against the
bit-exact DSP model. The accepted 2026-08-29 capture reconstructed four frames
and 4096 IQ words with no packet-integrity faults, socket drops, or model
mismatches.

The sustained recorder was accepted on 2026-08-30 with 100 triggered shots.
It journaled all 1801 expected datagrams, published all 1,638,400 IQ payload
bytes, and created 100 COMPLETE QIDX records with no incomplete shots,
malformed packets, foreign traffic, duplicates, reordering, conflicts, late
packets, continuity faults, preselection overflow, or kernel receive drops.

The host requested a 16 MiB `SO_RCVBUF`, but Linux reported an actual value of
425,984 bytes because of the system receive-buffer limit. This accepted finite
run had zero drops, but later continuous-rate qualification must either tune
the host's socket-buffer limits deliberately or demonstrate sufficient margin
with the reported value. The manifest remains the authoritative evidence of
what the kernel granted.
