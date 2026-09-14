# FPGA Implementation

> **Editorial status:** The following decomposition and register-map sketch
> are retained from the initial design proposal. They are not the current RTL
> inventory or register ABI. The implemented baseline is described in the
> System Architecture chapter and the source alongside `kv260/hw` and
> `rtl/dsp`; this chapter awaits a detailed implementation rewrite.

## Proposed datapath decomposition

The first reusable DSP chain should be divided into independently verifiable
blocks:

| Module | Responsibility | Initial verification focus |
|---|---|---|
| `qcrate_nco` | Phase accumulation and sine/cosine generation | Frequency and phase continuity |
| `qcrate_mixer` | Real-to-complex or complex mixing | Sign, scaling, pipeline alignment |
| `qcrate_fir` | Channel filtering | Impulse and frequency response |
| `qcrate_decimator` | Rate reduction | Output cadence and alias rejection |
| `qcrate_axis_pack` | Format samples for DMA | `TVALID/TREADY/TLAST` correctness |
| `qcrate_regs` | Configuration and status | Address and field semantics |

Each block should expose explicit sample validity. Configuration changes such
as a new FTW should have documented timing: immediate, sample-boundary, or
frame-boundary application.

## Initial register-map sketch

This table is a proposal, not a frozen interface.

| Offset | Name | Access | Purpose |
|---:|---|---|---|
| `0x00` | `CONTROL` | R/W | Core enable, soft reset, stream enable |
| `0x04` | `STATUS` | R/O | Running, overflow, error flags |
| `0x08` | `NCO_FTW` | R/W | NCO frequency tuning word |
| `0x0C` | `NCO_PHASE` | R/W | Initial or commanded phase offset |
| `0x10` | `DECIMATION` | R/W | Requested decimation factor |
| `0x14` | `FRAME_LENGTH` | R/W | Samples per DMA frame |
| `0x18` | `SAMPLE_COUNT` | R/O | Accepted output samples |
| `0x1C` | `ERROR_COUNT` | R/O | Overflow or protocol error count |

Reserved bits should read as zero and ignore writes. Pulse-like commands should
not rely on software returning a bit to zero unless that behaviour is deliberate.

## AXI DMA data movement

DMA prevents the CPU from copying every sample. The PL produces an AXI4-Stream;
the S2MM channel writes it into DDR. Software configures buffer addresses and
lengths, starts the transfer, and responds to completion or error events.

The practical design must address:

- physically contiguous or DMA-capable buffers;
- cache coherency and cache maintenance;
- frame length and `TLAST` semantics;
- buffer ownership between DMA and software;
- interrupt versus polling operation;
- continuous operation using multiple or cyclic buffers; and
- recovery from halted or overflow conditions.

The peak payload rate of a stream is approximately

$$
R = f_{axis}\times\frac{W_{data}}{8}\times\eta,
$$

where $\eta$ is the fraction of cycles on which a transfer occurs. A 64-bit
stream at 200 MHz has a theoretical interface payload of 1.6 GB/s at
$\eta=1$, but actual memory throughput will be lower and must be measured.

## Interrupts

The current block design provides two PL-to-PS interrupt lines. Candidate uses
include DMA completion/error and custom-core status. Interrupt sources should
remain asserted until software can identify and acknowledge them; a one-cycle
pulse may be missed by the processor-side interrupt controller.

## Debug observability

Every major integration stage should provide observability without requiring a
redesign. Useful probes and counters include:

- accepted input and output sample counts;
- stalled-cycle count;
- FIFO occupancy and overflow/underflow flags;
- current configuration snapshot;
- frame and interrupt counters; and
- sticky first-error status.

ILA is valuable for short, precisely triggered events, but architectural status
counters are better for long-running behaviour and field diagnostics.

## Verification levels

Q-Crate verification should progress through four levels.

1. **Mathematical reference:** floating- and fixed-point Python models.
2. **RTL unit tests:** self-checking SystemVerilog tests for individual blocks.
3. **RTL integration:** stream back-pressure, control updates, resets, and
   module interaction.
4. **Hardware validation:** ILA captures, DMA frames, spectral measurements,
   and long-duration stress tests.

Passing one level does not replace the next. A correct floating-point model
does not prove fixed-point widths, and a passing RTL simulation does not prove
timing closure or analogue signal integrity.

## Stream protocol assertions

Useful assertions include:

- payload remains stable while `TVALID && !TREADY`;
- `TLAST` appears at the intended frame position;
- accepted-sample count matches the reference model;
- no FIFO overflow or underflow occurs; and
- reset returns the interface to a defined idle state.

## Hardware acceptance measurements

Each integrated milestone should record:

| Category | Example measurement |
|---|---|
| Functional | Correct sample values and ordering |
| Throughput | Sustained MB/s and stall percentage |
| Timing | Worst negative slack and clock constraints |
| Resources | LUT, FF, BRAM, URAM, DSP utilisation |
| Signal quality | SNR, SFDR, amplitude and phase error |
| Reliability | Duration and data volume without error |
