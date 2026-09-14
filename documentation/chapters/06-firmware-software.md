# Firmware and Software Architecture

Q-Crate's software connects a deterministic hardware experiment to a durable,
inspectable measurement. The implemented stack uses R5-0/FreeRTOS for bounded
control, A53/Linux for acquisition and transport, and a host computer for
recording, experiment automation and analysis. The separation follows timing
requirements and data ownership: pulse edges and sample timing remain in PL.

This chapter describes the software organisation at baseline `d2d62fc`.
It establishes the main responsibilities and lifecycle contracts; detailed
protocol fields and platform setup remain documented alongside their source.

## Execution domains and ownership

| Domain | Owned work | Interface to the next layer |
|---|---|---|
| R5-0 / FreeRTOS | Sequence upload and lifecycle; runtime DSP configuration validation and activation | Typed RPMsg requests; bounded APB transactions |
| A53 / Linux kernel | DMA channel client, coherent acquisition banks, stream control and exclusive capture ownership | DMAEngine and the Q-Crate DMA userspace ABI |
| A53 / Linux application | Acquisition orchestration, R5 requests, bank-token queue and UDP transmission | Read-only DMA mapping and Data Plane v1 |
| Host recorder | Datagram journal, shot integrity and publication | Sample file, QIDX shot index and run manifest |
| Host automation and analyzer | Profile resolution, experiments, sweeps, reference comparison and plots | Resolved profiles and published run bundles |

The R5 service owns the sequence register page and event RAM. Linux requests
sequence operations through RPMsg instead of competing for those registers.
DMA descriptors and acquisition buffers remain Linux-owned. R5 changes runtime
framing only at the idle boundary required by the configuration transaction.

## Firmware lifecycle and the Linux platform

Linux `remoteproc` loads the R5 firmware ELF. The PetaLinux services establish
the PL before starting the R5 service, which announces the Q-Crate control
endpoint. Linux applications access that endpoint through `rpmsg_char`.

The firmware is built through the tracked Vitis flow. The platform-specific
OpenAMP machine layer, resource table and linker configuration originate in the
matching AMD template; the Q-Crate service supplies the application commands.
The Linux device tree and generated firmware must agree on the reserved memory,
vrings and inter-processor interrupt connections. These are platform contracts,
not acquisition-buffer allocations.

The current unattended experiment path also depends on SSH-key authentication
and the target's Q-Crate device-access policy. Those facilities permit host
orchestration without embedding passwords in experiment descriptions.

## OpenAMP, RPMsg and the command protocol

OpenAMP supports communication between the different processor environments.
RPMsg carries logical command and response messages through shared-memory
VirtIO queues, with inter-processor interrupts providing notification. AXI DMA
serves a separate purpose: it moves the PL sample stream into DDR. Bulk IQ
payloads do not pass through RPMsg.

The Q-Crate application protocol uses fixed 64-byte messages. At this baseline
its wire ABI is version 3, including runtime configuration commands. The Linux
client validates responses, while the R5 endpoint validates requests and applies
its ownership and state checks. The endpoint exposes explicit operations,
including identity, diagnostics, sequence lifecycle and configuration control;
it is not a general remote memory-write service.

## Transactional sequence supervision

A sequence upload is committed as a complete object before it can be armed.
Linux first validates the compiled sequence. R5 then accepts an upload begin
request, receives ordered events, checks their format and safety constraints,
writes and reads back the event RAM, and checks the completed payload CRC.
Only a successful commit enables subsequent ARM and START operations.

R5 supervises the operation, but the PL executes the event times against its
hardware timebase. Abort and reset operations use bounded waits. A failed or
partial upload invalidates the transaction instead of leaving a partially
accepted experiment available for execution.

## Runtime profiles and atomic configuration

The host resolves an experiment profile into the exact integer parameters used
by the instrument: tuning words, phases, Q1.15 amplitudes, noise seed, framing
and compiled sequence identity. The DSP configuration ID identifies the resolved
DSP settings and numerical contract; the full profile SHA-256 additionally binds
framing and sequence content. Display names and file paths do not define the
measurement identity.

R5 stages the resolved bundle in its own memory and performs semantic validation.
A commit requires the sequencer and stream engine to be idle and unlocked.
The service writes and reads back framing and DSP shadow fields, requests the
PL atomic commit, and verifies active identity, generation and readback before
reporting success. The PL transfers the configuration coherently across the
control-to-stream clock boundary.

A timeout is an unknown hardware outcome. The service does not attempt a
rollback that could race a pending hardware transaction. Status inspection and
explicit recovery handle that case. Definite rejection and verified successful
activation are distinct outcomes.

The current experiment workflow uses one active configuration per recorded run.
An LO sweep closes each run, commits the next resolved profile, and starts a
new run. Captured configuration identity binds each run to the numerical model
used for its verification.

## Linux DMA and coherent memory

The Q-Crate kernel driver is a Linux DMAEngine client. The underlying Xilinx
DMA driver owns the DMA channel machinery; Q-Crate supplies finite
scatter-gather capture chains and manages coherent acquisition banks. One
pre-armed descriptor corresponds to one frame. DMA is ready before the
acquisition trigger is issued.

The capture device permits one userspace owner. Its bank lifecycle is:

1. **FREE:** available for acquisition.
2. **FILLING:** owned by the active DMA capture.
3. **READY:** completed and available for dequeue.
4. **USER_OWNED:** held by userspace until explicit release.
5. **FREE:** reusable only after release.

Unread banks are never silently reused. Exhaustion is exposed through flow
control and counters. The pool supports repeated triggered acquisition using
finite SG operations; it is not an unowned, indefinitely wrapping cyclic ring.

Coherency and ownership solve different problems. The DMA API provides coherent
memory and a read-only userspace mapping, so the application does not perform
ad hoc cache flushes or invalidations. It must still wait for completion and
hold a valid bank token before reading. Coherent memory alone cannot prevent
reading a bank that is still being filled or has already been released.

## Acquisition and network transmission

The compiled `qcrate-streamer` process coordinates acquisition and UDP sending.
In sustained triggered operation, its acquisition thread handles R5 command
ordering and bank dequeue. A bounded queue transfers ownership tokens to the
sender thread. The queue holds metadata, not a second copy of every sample.

The sender packetizes directly from the validated read-only DMA extent. Each
`sendmsg()` combines an encoded header with a payload slice. This removes an
extra userspace payload copy; ordinary kernel UDP copying still occurs.
The sender releases the bank after its datagrams have been accepted by the
socket operation. Socket acceptance does not establish delivery to the host.

Data Plane v1 carries run, stream, shot, frame, packet-sequence, timestamp,
configuration and integrity information. Shot termination closes each
measurement; end-of-stream closes the run after the outstanding work is drained.
Network, control or DMA failures are reported as failed runs instead of being
presented as clean completion.

## Durable recording and publication

The host recorder separates received evidence from published measurements.
It journals datagrams before protocol decisions, validates and reassembles
shots, and exposes sample bytes only for complete acquisitions.

| Run file | Meaning |
|---|---|
| `packets.qcdp` | Arrival journal, including datagrams later rejected by protocol checks |
| `samples.iq16` | Published complete-shot payloads, with little-endian signed 16-bit I followed by Q |
| `shots.qidx` | Fixed-size shot records, integrity state and sample-file extents |
| `run.json` | Run metadata and final reporting, published by atomic replacement |

Sample bytes are flushed before the COMPLETE index record that exposes them.
Readers use the QIDX extent, not the apparent sample-file length, as their
publication boundary. An incomplete shot remains visible as an integrity event
but exposes no samples for ordinary measurement analysis. An unindexed tail
following a crash is not a published acquisition.

This design preserves evidence of loss or corruption without silently filling
holes or presenting partial data as a valid waveform.

## Host analysis, experiments and sweeps

The analyzer runs independently of the recorder. Closing or slowing the GUI
does not change the ingest path. It reads published QIDX entries and selected
sample extents for I/Q, magnitude, phase, constellation and complex-spectrum
views. Its bounded index cache and shot window avoid loading an entire long
acquisition into memory.

Host Python also compiles profiles, orchestrates acquisitions and compares
captured words with the matching fixed-point model. The single-experiment
workflow bundles the resolved profile with the run and checks its identity.
The sweep workflow applies that same acquisition boundary independently at
each LO setting, then estimates the coherent complex response. It characterises
the digital source/DDC/FIR path; it is not evidence of an analogue sensor or
converter response.

## Validation and measured scope

Verification crosses several boundaries: command semantics, DMA ownership,
packet integrity, publication ordering and numerical agreement. A GUI plot
alone establishes none of these. The recorded acceptance procedure combines
bit-exact sample comparison with a repeated-shot run and analyzer, receiver
and cable-disconnect recovery checks.

The v1.0.0 acceptance record reports a 300.001179-second run with 108,167 complete
shots, zero incomplete shots, and zero mismatches across 443,052,032 IQ words.
Those are measurements of that accepted synthetic-source KV260 setup, not a
new qualification performed for this documentation revision. Runtime-profile
and sweep evidence is documented separately alongside the experiment tools.
Physical analogue performance remains unmeasured by this digital acceptance.

## Source references

The following repository documents define the detailed contracts behind this
overview. Links are pinned to the source baseline used for this chapter.

- [R5 service and OpenAMP](https://github.com/solomontesema/qcrate/blob/d2d62fc50ec8e7b4e3643d8f6ea9c9cadcb83e86/kv260/linux/openamp/README.md)
- [Linux DMA ownership](https://github.com/solomontesema/qcrate/blob/d2d62fc50ec8e7b4e3643d8f6ea9c9cadcb83e86/kv260/linux/dma/README.md)
- [KV260 data-plane sender](https://github.com/solomontesema/qcrate/blob/d2d62fc50ec8e7b4e3643d8f6ea9c9cadcb83e86/kv260/linux/data_plane/README.md)
- [Host run format](https://github.com/solomontesema/qcrate/blob/d2d62fc50ec8e7b4e3643d8f6ea9c9cadcb83e86/host/data_plane/RUN_FORMAT.md)
- [Host analyzer](https://github.com/solomontesema/qcrate/blob/d2d62fc50ec8e7b4e3643d8f6ea9c9cadcb83e86/host/analyzer/README.md)
- [Experiment profiles and response sweeps](https://github.com/solomontesema/qcrate/blob/d2d62fc50ec8e7b4e3643d8f6ea9c9cadcb83e86/host/experiment_profiles/README.md)
- [Application acceptance](https://github.com/solomontesema/qcrate/blob/d2d62fc50ec8e7b4e3643d8f6ea9c9cadcb83e86/host/acceptance/README.md)
- [v1.0.0 release and measured results](https://github.com/solomontesema/qcrate/blob/d2d62fc50ec8e7b4e3643d8f6ea9c9cadcb83e86/releases/v1.0.0.md)
