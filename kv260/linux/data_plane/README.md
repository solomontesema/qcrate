# Q-Crate KV260 Data Plane Sender

## Objective And Status

This subsystem connects the accepted scatter-gather DMA path to the Q-Crate
Data Plane v1 network protocol. The default finite-shot path is accepted on
KV260. DP-5C1 adds a sustained triggered mode in which one compiled A53 Linux
process owns DMA, R5 RPMsg control, the coherent mapping, and UDP:

```text
qcrate_stream_engine / DSP
        |
        v
AXI DMA S2MM bank pool
        |
        v
qcrate_dma coherent mmap (read-only userspace view)
        |
        +--> acquisition thread --> bounded ownership-token queue
                                      |
                                      v
             sender thread --> shared C packetizer --> UDP sendmsg()
```

The no-option behavior remains the permanent one-shot regression: DMA finishes
before transmission starts. `--triggered-shots N` selects DP-5C1 and overlaps
acquisition of later banks with transmission of earlier banks. The sustained
path has been accepted on KV260 with 100 hardware-triggered shots and 1,801
datagrams received without kernel drops.

DP-5D adds `--duration-seconds N` as an alternative to
`--triggered-shots N`. The duration is measured around repeated acquisition,
not process startup; the last accepted shot is completed before the queue is
drained and END_OF_STREAM is sent. `--report-json PATH` atomically records
producer timing, CPU, queue high-water, DMA stalls/errors, backpressure, skipped
triggers, and packet totals. The report is operational evidence and does not
change Data Plane v1.

## Ownership Model

The sender does not program AXI DMA registers and does not allocate physical
memory. Responsibilities remain separated:

| Owner | Responsibility |
|---|---|
| `xilinx_dma` | DMA channel, descriptors, interrupt, and reset |
| `qcrate_dma` | exclusive open, stream control, finite SG chain, coherent buffer, timeout |
| R5 FreeRTOS | validates, arms, and starts each committed pulse sequence through RPMsg |
| acquisition thread | R5 command order, blocking bank dequeue, evidence validation |
| bounded SPSC queue | transfers ownership tokens only; never copies samples |
| sender thread | direct mmap packetization, monotonically increasing sequence, exact release |
| shared C packetizer | metadata, packet sequence, frame chunks, flags, CRC, termination |
| `qcrate-streamer` | capture request, read-only mmap, connected UDP socket, pacing |
| host receiver | immutable journal, reassembly, continuity audit, sample publication |

`qcrate_dma` permits one open file, so `qcrate-dma` and `qcrate-streamer` cannot
race over the same capture buffer. The mapping is read-only. Each UDP datagram
uses two `sendmsg()` iovecs: a 64-byte encoded header and a direct slice of the
coherent mmap. The kernel still copies normal UDP data into its socket buffer;
the design avoids an additional userspace packet-payload copy without claiming
Linux `MSG_ZEROCOPY` support.

## Sustained Triggered Ownership

Triggered mode uses the DP-5B invariant without weakening it:

```text
FREE -> FILLING -> READY -> USER_OWNED -> FREE
```

The acquisition thread may enqueue only a token returned by `POOL_DEQUEUE`.
The sender may read only the token's validated mmap extent and returns the bank
only after all of that shot's datagrams have been accepted by `sendmsg()`. A
network, RPMsg, DMA, protocol, or stale-token error fails the whole run; unread
measurement data is never silently overwritten or declared transmitted.

The queue capacity equals the configured bank count and contains fixed-size
DMA UAPI records, not payload bytes. The driver can therefore fill one bank
while userspace transmits another, but backpressure remains bounded and
observable through pool starvation and queue counters.

One random nonzero Data Plane run ID is generated per process. Hardware
`trigger_shot_id` values identify shots, and packet sequence numbers increase
across all shots in the run. Every `SHOT_END` closes one measurement without
ending the run. After the final bank has been returned successfully, a
zero-payload heartbeat sets `END_OF_STREAM`; a release or send failure therefore
cannot masquerade as a cleanly closed lifecycle.

## Finite Shot

The default command captures the accepted DSP profile:

| Property | Value |
|---|---:|
| Mode | DSP, `STREAM_MODE=1` |
| Frames | 4 |
| Samples per frame | 1024 complex IQ words |
| Sample encoding | little-endian Q[31:16], I[15:0], signed Q1.15 |
| Sample rate | 12.5 MS/s |
| DDC center/LO | 29 MHz |
| Sample payload | 16,384 bytes |
| Maximum datagram | 1400 bytes |
| Data packets | 16 |
| Total packets | 18: metadata, 16 data, termination |
| Default UDP payload pacing | 420 Mb/s |

Each frame becomes four data packets: three carry 334 samples and one carries
22. No packet crosses a frame boundary. `FRAME_START`, `FRAME_END`, frame ID,
and sample offset allow the host to reconstruct arrival reordering without
guessing.

The 420 Mb/s default paces the complete UDP application payload, including the
Q-Crate header. It corresponds to the approximately 419 Mb/s required by the
400 Mb/s IQ payload and was accepted by the KV260-to-host baseline around this
boundary. `--rate-mbps 0` deliberately disables pacing for diagnostics.

## Stream Profiles

The packet metadata is centralized in
[`qcrate_stream_profiles.h`](../../../common/data_plane/qcrate_stream_profiles.h):

- DSP mode identifies the tracked 200 MHz synthetic-ADC/DDC/FIR configuration;
- counter mode describes the 200 MHz frame/sample test pattern;
- both use the shared 200 MHz Q-Crate timebase as their timestamp clock;
- the one-shot compatibility path carries timestamp zero with
  `TIMESTAMP_VALID` clear;
- triggered pool mode places the DMA-captured `first_sample_time` in the first
  DATA packet and sets `TIMESTAMP_VALID` only there. Later packet timestamps
  remain invalid rather than manufacturing sub-shot precision.

The DSP `config_id` is the first 64 bits of SHA-256 over the canonical DSP JSON
followed by the table manifest. A focused test fails if either source changes
without updating the deployed identity. Reproduce the full digest with:

```bash
python3 - <<'PY'
import hashlib
from pathlib import Path

config = Path("host/dsp_model/configs/tone_1mhz.json").read_bytes()
tables = Path("rtl/dsp/tables/manifest.json").read_bytes()
print(hashlib.sha256(config + tables).hexdigest())
PY
```

Expected prefix: `0fd399e017ecb182`.

## Source Map

```text
common/dma/qcrate_dma_uapi.h
    Canonical fixed-width ABI shared by the kernel module and sender.

common/data_plane/qcrate_data_protocol.[ch]
    Explicit endian-safe wire codecs.

common/data_plane/qcrate_data_packetizer.[ch]
    Finite-shot metadata, frame splitting, sequence, CRC, and SHOT_END logic.

common/data_plane/qcrate_stream_profiles.h
    Counter and DSP metadata contracts.

kv260/linux/data_plane/qcrate_streamer.c
    One-shot adapter and sustained acquisition/sender pipeline.

kv260/linux/openamp/qcrate_rpmsg_client.[ch]
    Reusable validated Linux RPMsg transport shared with qcrate-control.

recipes-apps/qcrate-streamer/qcrate-streamer.bb
    Cross-compiles and installs /usr/bin/qcrate-streamer.
```

The UAPI was moved out of the kernel recipe so the driver and application
cannot silently acquire different private copies.

## Why This Is A Yocto Application

`qcrate-streamer` is a normal POSIX Linux executable using a stable kernel
UAPI, sockets, and the C standard library. Building it in a PetaLinux/Yocto
recipe is the native production path because the executable and its exact
runtime dependencies become part of the audited root filesystem and are
rebuilt with the platform SDK automatically.

Vitis remains necessary for the R5 FreeRTOS/OpenAMP firmware and is useful for
debugging Linux applications against an exported sysroot. It is not mandatory
for every A53 userspace executable. Creating a separate Vitis Linux application
here would add a second packaging path without improving DMA or network
ownership.

## Lightweight Host Verification

Run from the repository root:

```bash
python3 common/data_plane/run_tests.py
```

This performs strict `-Wall -Wextra -Werror` C builds for the protocol,
packetizer, and complete sender, then runs the C packet-splitting test and all
Python codec, receiver, journal, fault-injection, and profile-identity tests.
It does not access the DMA device or start AMD tools.

The DP-5D board command used by the host orchestrator is equivalent to:

```bash
sudo qcrate-streamer \
  --destination 192.168.1.92 \
  --duration-seconds 300 \
  --banks 4 \
  --rate-mbps 420 \
  --report-json /tmp/qcrate-dp5d-sender.json
```

See [DP-5D instrument acceptance](../../../host/acceptance/README.md) for the
complete host command, fault procedures, and verdict criteria.

## PetaLinux Build And Deployment

No Vivado rebuild is needed because DP-5C1 changes neither hardware nor device
tree. Stage the existing R5 ELF plus canonical Linux client sources; `stage`
does not rebuild the Vitis platform:

```bash
python3 kv260/vitis/vitis_flow.py stage
```

Build the two affected applications:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux/qcrate-kv260
petalinux-build -c qcrate-streamer
petalinux-build -c qcrate-openamp
```

Expected result: the recipe compiles with no warnings promoted to errors and
installs `qcrate-streamer`. Then rebuild and audit the image from the repository
root:

```bash
cd /tools/fpga_projects/qcrate
python3 kv260/linux/petalinux/scripts/petalinux_flow.py build
```

The flow now rejects a root filesystem missing `/usr/bin/qcrate-streamer`.
Package and deploy only after the build succeeds, replacing the device with the
confirmed whole SD-card device:

```bash
python3 kv260/linux/petalinux/scripts/petalinux_flow.py package
python3 kv260/linux/petalinux/scripts/petalinux_flow.py deploy \
  --device /dev/mmcblk0
```

These are expensive user-run steps. The accepted SD-card identification and
destructive-write safeguards remain documented in the PetaLinux README.

## Board Acceptance

Use the accepted direct Gigabit addresses from the network baseline when they
are still valid:

- development host: `192.168.1.92`;
- KV260: `192.168.1.93`;
- UDP port: `47000`.

First start the host receiver so no initial metadata is lost:

```bash
cd /tools/fpga_projects/qcrate
python3 host/data_plane/qcrate_receiver.py receive \
  --bind 192.168.1.92 \
  --source 192.168.1.93 \
  --port 47000 \
  --output build/data_plane/kv260-dsp-shot
```

Then, on the KV260, confirm the platform and send one default DSP shot:

```bash
sudo qcrate-first-boot test
sudo qcrate-dma info
sudo qcrate-streamer --destination 192.168.1.92
```

The board command should report:

- DSP mode, four frames, and 1024 samples per frame;
- 18 datagrams;
- 16,384 sample-payload bytes;
- the random nonzero run ID and CRC-32;
- requested and actual `SO_SNDBUF`;
- DMA stall cycles as diagnostic evidence, not automatically as an error.

The host must print `PASS Q-Crate Data Plane capture` and create all three
bundle files. Inspect the manifest:

```bash
python3 -m json.tool \
  build/data_plane/kv260-dsp-shot/manifest.json
```

Acceptance requires:

- `complete: true`;
- zero malformed, conflicting, and missing packets;
- four frames and 4096 samples;
- `SHOT_END` totals and CRC matching the receiver;
- `kernel_drop_counter: 0` when Linux exposes `SO_RXQ_OVFL`.

Finally compare every received IQ word with the existing model and generate the
visual report:

```bash
python3 host/dsp_model/qcrate_capture_viewer.py \
  build/data_plane/kv260-dsp-shot/samples.bin \
  --output build/data_plane/kv260-dsp-shot/capture.png \
  --summary build/data_plane/kv260-dsp-shot/dsp-report.json
```

Expected result: zero model mismatches and the same approximately 1 MHz complex
tone accepted during DSP-2B DMA testing.

### DP-5C1 Sustained Sender

DP-5C1 qualified sender lifecycle and packet count before the compiled
recorder existed. The `socat` plus `tcpdump` procedure below is retained as a
low-level network diagnostic, not as the normal acquisition workflow.
`tcpdump` observes traffic but does not bind the port; running it alone makes
the host return ICMP port-unreachable messages to the sender's connected UDP
socket.

In the first host terminal, bind a discard sink for the complete run:

```bash
socat -u UDP4-RECV:47000,bind=192.168.1.92,reuseaddr /dev/null
```

In a second host terminal, replace the interface if necessary and start packet
capture. Remove a previous capture first, or deliberately retain it and use the
per-source-port audit below:

```bash
rm -f build/data_plane/dp5c1-100-shots.pcap
sudo tcpdump -ni enp3s0f1 udp port 47000 \
  -w build/data_plane/dp5c1-100-shots.pcap
```

On KV260, load the committed sequence table once, then request 100 overlapping
shots:

```bash
sudo qcrate-sequence load ~/qcrate/two_channel_demo.qseq
sudo qcrate-streamer \
  --destination 192.168.1.92 \
  --triggered-shots 100 \
  --banks 4
```

Stop both host processes after the sender exits and count the captured packets:

```bash
tcpdump -nn -r build/data_plane/dp5c1-100-shots.pcap 2>/dev/null | wc -l
```

Every new sender socket chooses a new ephemeral source port. If one capture
contains retries, count each socket lifecycle separately:

```bash
tcpdump -nn -r build/data_plane/dp5c1-100-shots.pcap 2>/dev/null |
  awk '/47000:/ {split($3, part, "."); print part[length(part)]}' |
  sort | uniq -c
```

With the default four-frame profile, acceptance requires:

- `shots acquired/sent: 100 / 100`;
- 1,638,400 sample-payload bytes;
- packet sequence `0..1800` and exactly 1801 captured datagrams (18 per shot
  plus one terminal heartbeat);
- nonzero, increasing hardware shot IDs and first-sample timestamps;
- separate token-queue and DMA READY-queue high-water values;
- zero skipped triggers and DMA errors; pool starvation/backpressure is reported
  separately and does not imply loss in the software-triggered mode;
- no DMA, RCU-stall, remoteproc, or RPMsg errors in `dmesg`.

Finally rerun the one-shot receiver procedure above. It proves that adding the
sustained mode did not change the already accepted compatibility path.

### DP-5C2 Sustained Recorder

DP-5C2 replaces both host-side diagnostic processes with one compiled,
integrity-checking recorder. Build and start it on the development host:

```bash
python3 host/data_plane/build_recorder.py

RUN=build/data_plane/dp5c2-$(date -u +%Y%m%dT%H%M%SZ)
build/host/qcrate-recorder \
  --bind 192.168.1.92 \
  --source 192.168.1.93 \
  --output "$RUN"
```

In the KV260 serial or SSH terminal, load the sequence if needed and send the
same 100-shot acceptance run:

```bash
sudo qcrate-sequence load ~/qcrate/two_channel_demo.qseq
sudo qcrate-streamer \
  --destination 192.168.1.92 \
  --triggered-shots 100 \
  --banks 4
```

The host recorder must exit with:

```text
PASS: 100 complete, 0 incomplete shot(s), 1801 datagrams, 0 kernel drop(s)
```

Inspect the final machine-readable decision and file sizes:

```bash
python3 -m json.tool "$RUN/run.json"
stat -c '%n %s bytes' "$RUN/samples.iq16" "$RUN/shots.qidx"
```

Expected sizes are 1,638,400 sample bytes and 12,832 QIDX bytes. The latter is
one 32-byte header plus 100 fixed 128-byte records. The recorder also requires
monotonic run sequence, shot IDs, and first-sample timestamps; exact frame
coverage and sender totals; matching CRC-32; and no selected malformed,
conflicting, source-error, or receive-overflow evidence.

The host architecture and on-disk format are documented in
[`host/data_plane/README.md`](../../../host/data_plane/README.md) and
[`host/data_plane/RUN_FORMAT.md`](../../../host/data_plane/RUN_FORMAT.md).

## Accepted Result

The DP-5C2 sustained recorder was accepted on the KV260 and development host
on 2026-08-30. A 100-shot triggered run produced exactly 1801 datagrams, 100
COMPLETE QIDX records, zero incomplete shots, and 1,638,400 published IQ
payload bytes. There were no malformed, foreign, duplicate, reordered,
conflicting, late, missing, continuity-error, preselection-overflow, or kernel
drop reports. The resulting QIDX file was the expected 12,832 bytes.

Linux clamped the requested 16 MiB receive buffer to 425,984 bytes. That is an
accepted measured condition for this finite test, not proof of unlimited
continuous-rate margin; the recorder records both values so later throughput
qualification can make the host configuration explicit.

The finite DSP shot was accepted on the KV260 and development host on
2026-08-29. The receiver recorded 18 valid datagrams: one `STREAM_INFO`, 16
`DATA`, and one `SHOT_END`. It observed no missing, malformed, reordered,
duplicate, conflicting, foreign, or kernel-dropped packets.

The reconstructed capture contained four frames, 4096 IQ samples, and 16,384
payload bytes. Its CRC-32 was `0xe118cb1a`, and `SHOT_END` independently
reported the same frame, sample, packet, byte, and CRC totals. The sample file
SHA-256 was
`e645b9eeb7506178ac0de33d08a2d8d21b90f80289859c557be30e3b09583a4b`.

The bit-accurate DSP comparison reported zero mismatches. The first and last
words were `0x00010001` and `0x23c71f8d`; spectral inspection found the
expected approximately 1 MHz output tone. The generated bundle under
`build/data_plane/kv260-dsp-shot/` remains ignored because it is reproducible
test evidence rather than source.

## Additional Diagnostics

Counter mode exercises the same DMA and network path without the DSP model:

```bash
sudo qcrate-streamer \
  --destination 192.168.1.92 \
  --mode counter \
  --words 4095 \
  --frames 8
```

Use a new host output directory for every shot. Disable pacing only to measure
burst behavior explicitly:

```bash
sudo qcrate-streamer --destination 192.168.1.92 --rate-mbps 0
```

Replay any resulting journal without the board:

```bash
python3 host/data_plane/qcrate_receiver.py replay \
  build/data_plane/kv260-dsp-shot/packets.qcdp \
  --output build/data_plane/kv260-dsp-shot-replay
```

## Next Boundary

DP-4 Network Data Plane is complete, and the Data Plane v1 wire contract is
frozen. The next milestone is DP-5 Repeated Triggered IQ Acquisition for the
Networked Pulsed-IQ Analyzer reference application.

DP-5 preserves this finite-shot path and adds a separate asynchronous finite-SG
bank pool. Its invariant is explicit:

```text
FREE -> FILLING -> READY -> USER_OWNED -> FREE
```

No unread measurement bank may be overwritten. When no free bank exists, the
system stops arming new acquisition work and reports starvation or skipped
triggers. Queue depth, CPU cost, socket drops, throughput, receiver restart,
and cable-removal recovery must be measured before DP-5 is accepted.
