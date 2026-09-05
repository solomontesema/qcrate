# Q-Crate

**A reproducible FPGA instrumentation platform for deterministic pulse control,
triggered IQ acquisition, heterogeneous processing, and integrity-preserving
network transport.**

Q-Crate combines an AMD Kria KV260, custom SystemVerilog, FreeRTOS on Cortex-R5,
PetaLinux on Cortex-A53, scatter-gather DMA, a versioned UDP data plane, and host
analysis software. The repository contains source and automation rather than a
checked-in Vivado workspace, so the hardware and software platform can be
rebuilt and audited from Git.

The first formal release is **Q-Crate v1.0.0**, which closes the first reference
application: **Networked Pulsed-IQ Analyzer v1**. See the
[v1.0.0 release notes](releases/v1.0.0.md).

## Accepted On KV260

Networked Pulsed-IQ Analyzer v1 is **ACCEPTED** on real KV260 hardware using the
deterministic synthetic sampled-signal source included in the RTL. A five-minute
run exercised hardware triggering, DSP, DMA-bank ownership, sustained UDP
transport, durable recording, exact reconstruction, and host analysis.

![Accepted Q-Crate Networked Pulsed-IQ Analyzer](host/acceptance/images/dp5d-kv260-accepted.png)

| Qualification result | Measured value |
|---|---:|
| Sustained acquisition | 300.001179 seconds |
| Complete / incomplete shots | 108,167 / 0 |
| IQ words checked against the bit-accurate model | 443,052,032 |
| Reference mismatches | 0 |
| Sample / UDP payload rate | 47.210 / 50.898 Mb/s |
| Missing, malformed, conflicting, or kernel-dropped packets | 0 |
| Missed/skipped triggers and DMA errors | 0 |
| Analyzer restart | PASS |
| Receiver restart | PASS |
| Ethernet disconnect and fresh-run recovery | PASS |

The tracked [machine-readable evidence](host/acceptance/evidence/dp5d-kv260-accepted.json)
and [acceptance procedure](host/acceptance/README.md) preserve the exact result
and its reproduction criteria. Interrupted acquisition is never promoted as a
complete measurement, and recovery starts a fresh run rather than concealing a
gap.

## Platform And Reference Application

**Q-Crate** is the reusable instrumentation platform. It supplies deterministic
timing, processor ownership boundaries, sample transport, persistent run
formats, integrity checks, and reproducible build/deployment flows.

**Networked Pulsed-IQ Analyzer** is the first application built on that
platform. It uses an R5-supervised pulse sequence to trigger coherent IQ shots,
moves them through Linux-owned DMA buffers, sends them over Data Plane v1, and
records and displays waveform, magnitude, phase, constellation, spectrum, and
instrument health on a host.

The distinction is deliberate. Future instruments can replace the signal
source or host interpretation while retaining the platform contracts.

## Architecture

```text
Host control
  sequence compiler and acceptance orchestration
                    |
                    | SSH + RPMsg control
                    v
+--------------------------- KV260 / K26 ---------------------------+
|                                                                   |
|  Cortex-A53 / PetaLinux              Cortex-R5 / FreeRTOS         |
|  DMA ownership, DDR, UDP  <---RPMsg-- sequence validation/control |
|              ^                                  |                 |
|              | AXI DMA S2MM                     | APB             |
|              |                                  v                 |
|  200 MHz PL: timebase + sequencer + synthetic source + DDC/FIR    |
|              |                                                    |
|              +---- framed 12.5 MS/s Q1.15 IQ stream --------------+
+-------------------------------------------------------------------+
                    |
                    | Q-Crate Data Plane v1 / UDP
                    v
Host data path
  compiled recorder -> immutable journal + QIDX -> analyzer/acceptance
```

The responsibility split is part of the design:

- **Programmable logic** owns clock-cycle timing, stream handshakes, and DSP.
- **R5-0 with FreeRTOS** owns bounded sequence validation and lifecycle control.
- **A53 Linux** owns DMA descriptors, coherent buffers, networking, and system
  policy.
- **The host** owns durable recording, replay, numerical verification, and
  visualization.

Linux never generates sample-level timing, the R5 never copies bulk sample
data, and the analyzer never participates in the UDP ingest critical path.

## Reusable Capabilities

| Area | Implemented and hardware-accepted capability |
|---|---|
| Reproducible FPGA build | Exported block-design Tcl, tracked RTL/XDC inputs, staged Vivado batch flow, bitstream and XSA export |
| Control plane | APB fabric with system, stream, interrupt, and sequencer pages behind the PS AXI path |
| Clocking and CDC | 100 MHz control and 200 MHz stream/timing domains with explicit command, status, and event crossings |
| Deterministic timing | Shared 64-bit 200 MHz timebase and two-channel event sequencer |
| Heterogeneous control | Versioned RPMsg protocol, Linux `remoteproc`, and R5-0 FreeRTOS/OpenAMP service |
| DSP | Deterministic 200 MS/s synthetic source, 29 MHz complex DDC, 217-tap decimate-by-16 FIR, and bit-accurate Python model |
| DMA ownership | Linux DMAEngine client, finite scatter-gather chains, and asynchronous finite-SG bank pool |
| Data integrity | `FREE -> FILLING -> READY -> USER_OWNED -> FREE`; unread measurements are never silently overwritten |
| Network data plane | Frozen Data Plane v1 header, direct DMA-buffer packetization, sequence/loss detection, and run identity |
| Durable acquisition | Compiled host recorder, immutable datagram journal, QIDX publication boundary, and atomic run manifests |
| Analysis and acceptance | Bounded-memory live GUI, headless reports, exact model comparison, soak tests, and disruption recovery |

## Scope Of v1.0.0

This release validates the complete **digital** instrumentation path. Its input
is a deterministic synthetic ADC/channel model implemented in RTL, not a
physical converter.

Q-Crate v1.0.0 does **not** claim:

- a completed ADC, DAC, RF, or analogue front end;
- measured ENOB, SNR, SFDR, clock jitter, input bandwidth, or calibrated volts;
- JESD204, LVDS converter, PCIe, or MicroTCA backplane integration;
- product safety, regulatory compliance, or production deployment support.

This boundary is important: the release proves that accepted digital samples
retain deterministic timing, ownership, identity, and integrity from PL to the
host. Physical measurement accuracy remains a future hardware-specific
milestone.

## Explore The Repository

| Start here | Contents |
|---|---|
| [Q-Crate Design Guide](documentation/Q-CRATE_DESIGN_GUIDE.md) | Architecture and DSP concepts; document edition 0.1 |
| [KV260 platform](kv260/README.md) | Hardware/software ownership, clocks, and deployment stages |
| [KV260 hardware](kv260/hw/README.md) | Block design, RTL tests, sequencing, reset, and ILA |
| [PetaLinux platform](kv260/linux/petalinux/README.md) | Fixed-platform configuration, build, packaging, SD deployment, and first boot |
| [DMA acquisition](kv260/linux/dma/README.md) | DMAEngine client, SG chains, triggered capture, and bank ownership |
| [R5/OpenAMP](kv260/linux/openamp/README.md) | Vitis firmware, remoteproc, RPMsg ABI, and R5 ownership |
| [DSP RTL](rtl/dsp/README.md) | NCO, synthetic source, DDC, FIR, framing, and verification |
| [DSP model](host/dsp_model/README.md) | Numerical contract, bit-accurate model, vectors, and capture viewer |
| [Data Plane v1](common/data_plane/README.md) | Frozen binary UDP contract and cross-language codecs |
| [Run format and recorder](host/data_plane/README.md) | Journal, QIDX, replay, and compiled sustained recorder |
| [Analyzer](host/analyzer/README.md) | Live/offline IQ analysis and bounded-memory long-run navigation |
| [Instrument acceptance](host/acceptance/README.md) | Five-minute soak, fault tests, evidence, and PASS criteria |

The repository layout follows those ownership boundaries:

```text
common/             shared C wire formats and userspace ABIs
config/             reproducible FPGA build configuration
rtl/                portable DSP RTL and self-checking testbenches
kv260/hw/           KV260 block design, integration RTL, and hardware tests
kv260/r5_freertos/  R5 real-time service
kv260/vitis/        reproducible Vitis platform/application flow
kv260/linux/        PetaLinux, kernel module, target tools, DMA, and networking
host/               compilers, models, receiver/recorder, analyzer, acceptance
documentation/      public design guide source and generated edition
scripts/            Vivado and packaging entry points
```

## Build And Test Entry Points

The accepted toolchain is Vivado, Vitis, and PetaLinux **2024.2**, targeting the
KV260/K26 part `xck26-sfvc784-2LV-c`. Python 3.10 or newer is used for host
automation and models. AMD tools and the KV260 BSP must be installed separately.

Run host-side tests without AMD tools:

```bash
python3 common/data_plane/run_tests.py
python3 -m unittest discover -s host/dsp_model/tests -v
python3 -m unittest discover -s host/analyzer/tests -v
python3 -m unittest discover -s host/acceptance/tests -v
```

Preview the hardware build command without starting Vivado:

```bash
python3 scripts/build.py --stage project --dry-run
```

Run the clean Vivado build through bitstream and XSA export:

```bash
python3 scripts/build.py --stage all
```

Build the R5 firmware after exporting the XSA:

```bash
python3 kv260/vitis/vitis_flow.py all
```

PetaLinux configuration, image creation, boot-firmware packaging, destructive
SD deployment, and first-boot acceptance are intentionally documented in the
[PetaLinux procedure](kv260/linux/petalinux/README.md) rather than duplicated
here.

## Reproducibility And Release Policy

Generated Vivado projects, Vitis workspaces, PetaLinux/Yocto build trees,
bitstreams, XSA files, ELF files, SD images, and multi-gigabyte acquisition runs
are not committed. They are rebuilt from tracked inputs or retained as external
test artifacts. The source tag, tool versions, accepted evidence hashes, and
artifact policy are recorded in the
[v1.0.0 release manifest](releases/v1.0.0-manifest.json).

Data Plane v1 and QIDX v1 are compatibility boundaries. Existing layouts,
endianness, sizes, and field meanings must not be reinterpreted silently; an
incompatible change requires a new protocol or format version.

Q-Crate v1.0.0 establishes the reusable digital foundation. The next focused
platform milestone is a hardware-independent acquisition-source contract,
followed by integration only after a concrete physical ADC and measurement
objective are selected.

## License

The Q-Crate repository as a whole is publicly source-visible but is not offered
as open-source software or open hardware. Copyright © 2026 Solomon Negussie
Tesema. All Rights Reserved. Public access grants no general right to
reproduce, modify, redistribute, sublicense, or commercially exploit the
project. Separately marked components retain their stated licenses. See the
[Q-Crate Copyright and Use Notice](LICENSE.md) for the governing terms.
