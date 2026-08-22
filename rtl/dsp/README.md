# Q-Crate DSP-1 portable frontend

DSP-1 is the first synthesizable implementation of the DSP-0 numerical
contract. It accepts signed ADC-like samples, generates a configurable local
oscillator (LO), and performs complex digital downconversion. It is kept under
the root `rtl/` tree because the arithmetic is independent of the KV260,
ZCU102, processor, DMA, and physical ADC interface.

DSP-1 does not yet contain the low-pass FIR, decimator, AXI framing, register
page, or `qcrate_core` integration. The accepted KV260 counter stream therefore
remains unchanged while this frontend is verified independently.

## Architecture

```text
signed Q1.15 sample, valid/ready
               |
               |             32-bit phase increment
               |                       |
               |               +-------v-------+
               |               | quarter-wave  |
               |               | LO NCO         |
               |               | Q1.15 sin/cos  |
               |               +-------+-------+
               |                       |
               +----------+------------+
                          v
                 +------------------+
                 | complex mixer    |
                 | I =  x * cos(LO) |
                 | Q = -x * sin(LO) |
                 +--------+---------+
                          |
                 signed Q1.17 I and Q,
                     valid/ready
```

| Module | Responsibility |
|---|---|
| `qcrate_nco.sv` | 32-bit phase accumulation and bit-exact Q1.15 sine/cosine generation |
| `qcrate_ddc_mixer.sv` | Signed products, ties-away rounding, and 18-bit Q1.17 saturation |
| `qcrate_dsp_frontend.sv` | ADC/LO pipeline alignment and ready/valid backpressure |

The NCO uses the top 12 phase bits and the tracked 1025-entry quarter-wave
table in `tables/sine_quarter_q1_15.hex`. Two synchronous ROM reads produce
sine and cosine with a two-cycle latency. The mixer uses one registered
full-precision product stage followed by one registered rounding and saturation
stage. The complete frontend therefore has a four-cycle accepted-input to
valid-output latency and sustains one sample per clock.
Latency does not alter sample order or phase: the accumulator advances exactly
once for each accepted input transfer.

The frontend uses a global pipeline clock enable. When its output is valid and
the receiver deasserts `m_ready_i`, the ADC delay, NCO phase, ROM pipeline,
mixer output, and all valid bits freeze. This guarantees stable output under
backpressure and prevents the LO from advancing without a corresponding ADC
sample.

`phase_load_i` loads the initial phase and flushes in-flight frontend samples.
Configuration must therefore be loaded while the stream is idle. The current
DSP-0 experiment uses LO increment `0x251eb852`, representing 29 MHz at a
200 MHz sample rate.

## Portable boundary

The frontend input is a normalized ready/valid sample stream. Board-specific
logic belongs outside this module:

```text
KV260 synthetic/test source ------+
                                  +--> qcrate_dsp_frontend
ZCU102 FMC ADC adapter + FIFO -----+
```

A physical free-running ADC cannot obey backpressure directly. Its adapter
must provide buffering, clock-domain crossing where required, and explicit
overflow reporting. Very fast converters may deliver several samples per PL
clock; that adapter can use parallel frontend lanes or a future multi-sample
interface without changing the scalar DSP-1 arithmetic contract.

The same distinction applies to vendor IP. Portable arithmetic and handshake
semantics stay here. Xilinx FIR Compiler, DMA, transceiver, and device-specific
clocking will sit behind wrappers selected by each board build.

## Verification

The host model generates intermediate ADC, LO, and mixer vectors into the
ignored `build/dsp/rtl_vectors` directory. Run all DSP-1 tests from the
repository root:

```bash
python3 rtl/dsp/run_tests.py
```

Expected final lines include:

```text
PASS: qcrate_nco_tb verified 1024 samples
PASS: qcrate_ddc_mixer_tb
PASS: qcrate_dsp_frontend_tb verified 1024 samples
PASS: all DSP-1 RTL tests
```

The NCO test inserts deterministic clock-enable gaps. The mixer test covers
positive full scale, saturation at both 18-bit limits, and positive/negative
half ties. The frontend test inserts deterministic source gaps and randomized
output backpressure while comparing every accepted output against Python.

Generate the stage vectors without running RTL simulation when inspecting the
contract:

```bash
python3 host/dsp_model/qcrate_dsp.py generate-rtl-vectors \
  host/dsp_model/configs/tone_1mhz.json \
  build/dsp/rtl_vectors \
  --samples 1024
```

No Vivado or board build is required for DSP-1 unit acceptance. The next
milestone adds the 217-tap decimating FIR, then compares complete framed IQ
words with DSP-0 before selecting `STREAM_MODE=1` in `qcrate_core`.

## KV260 synthesis check

After the host tests pass, run the optional out-of-context synthesis against
the K26 part. This takes longer than the unit tests and does not modify the
deployed KV260 design:

```bash
source /tools/Xilinx/Vivado/2024.2/settings64.sh
vivado -mode batch -nojournal -nolog \
  -source rtl/dsp/synth_ooc.tcl
```

Expected final output is:

```text
PASS: DSP-1 OOC synthesis and 200 MHz timing
```

Inspect `build/vivado/dsp_ooc/utilization.rpt` for DSP48 and block-memory
inference, and `build/vivado/dsp_ooc/timing_summary.rpt` for nonnegative worst
setup slack. Acceptance requires no `ERROR` or relevant `CRITICAL WARNING`,
nonnegative setup slack, DSP48 inference for the multipliers, and block-memory
inference for the NCO table. Unexpected mappings must be resolved before this
frontend is integrated into a platform build.
