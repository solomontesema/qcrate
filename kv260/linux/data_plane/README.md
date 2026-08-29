# Q-Crate KV260 Data Plane Sender

## Objective And Status

This milestone connects the accepted finite scatter-gather DMA path to the
Q-Crate Data Plane v1 network protocol. One compiled A53 Linux process owns the
complete operation:

```text
qcrate_stream_engine / DSP
        |
        v
AXI DMA S2MM finite SG chain
        |
        v
qcrate_dma coherent mmap (read-only userspace view)
        |
        v
shared C packetizer -> paced UDP sendmsg() -> host receiver
```

Implementation, PetaLinux integration, and KV260-to-host acceptance are
complete. This is still a finite-shot sender: DMA completes before packet
transmission starts. It is the permanent triggered-acquisition and regression
path, not a claim of continuous real-time streaming.

## Ownership Model

The sender does not program AXI DMA registers and does not allocate physical
memory. Responsibilities remain separated:

| Owner | Responsibility |
|---|---|
| `xilinx_dma` | DMA channel, descriptors, interrupt, and reset |
| `qcrate_dma` | exclusive open, stream control, finite SG chain, coherent buffer, timeout |
| shared C packetizer | metadata, packet sequence, frame chunks, flags, CRC, termination |
| `qcrate-streamer` | capture request, read-only mmap, connected UDP socket, pacing |
| host receiver | immutable journal, reassembly, continuity audit, sample publication |

`qcrate_dma` permits one open file, so `qcrate-dma` and `qcrate-streamer` cannot
race over the same capture buffer. The mapping is read-only. Each UDP datagram
uses two `sendmsg()` iovecs: a 64-byte encoded header and a direct slice of the
coherent mmap. The kernel still copies normal UDP data into its socket buffer;
the design avoids an additional userspace packet-payload copy without claiming
Linux `MSG_ZEROCOPY` support.

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
- every data header currently carries timestamp zero with
  `TIMESTAMP_VALID` clear because the DMA ABI does not capture the exact first
  sample time.

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
    DMA and UDP platform adapter.

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

## PetaLinux Build And Deployment

No Vivado rebuild is needed because DP-3 does not change hardware or device
tree. Build the new recipe first:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux/qcrate-kv260
petalinux-build -c qcrate-streamer
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

## Accepted Result

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

Continuous acquisition must introduce explicit concurrent ownership between
DMA fill buffers and network drain buffers. It must be driven by measured queue
depth, CPU cost, socket drops, and sustained throughput rather than by
repeatedly invoking this finite command.
