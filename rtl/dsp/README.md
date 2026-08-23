# Q-Crate portable DSP chain

DSP-1 is the first synthesizable implementation of the DSP-0 numerical
contract. It accepts signed ADC-like samples, generates a configurable local
oscillator (LO), and performs complex digital downconversion. It is kept under
the root `rtl/` tree because the arithmetic is independent of the KV260,
ZCU102, processor, DMA, and physical ADC interface.

DSP-2A adds the low-pass FIR and decimation boundary using a reproducibly
generated Xilinx FIR Compiler backend and a project-owned fixed-point
quantizer. AXI framing, register control, and `qcrate_core` integration remain
for DSP-2B. The accepted KV260 counter stream is unchanged during DSP-2A.

## Why the design needs a 217-tap decimator by 16

The DSP chain begins with signed samples at 200 Msamples/s. Complex mixing
translates a selected frequency band toward zero frequency, after which a
low-pass FIR removes unwanted mixer products and a decimator retains one of
every 16 filtered samples:

```text
ADC samples at 200 Msamples/s
             |
     complex NCO mixer
             |
    217-tap low-pass FIR
             |
      decimate by 16
             |
complex IQ at 12.5 Msamples/s
```

For example, mixing a 30 MHz input with a 29 MHz LO produces the wanted 1 MHz
difference component and an unwanted component around 59 MHz. The low-pass
filter preserves the baseband component and rejects the high-frequency mixer
product.

Decimation changes the sample rate to:

```text
200 Msamples/s / 16 = 12.5 Msamples/s
```

The resulting Nyquist frequency is 6.25 MHz. Discarding samples without first
filtering would fold content above 6.25 MHz into the retained baseband. This
aliasing permanently corrupts the output and cannot be removed by later
processing. The accepted filter therefore passes the 0 to 2 MHz instrument
band and reaches its stopband by the 6.25 MHz post-decimation Nyquist limit.

An FIR with 217 taps evaluates the weighted history

```text
y[n] = h[0]x[n] + h[1]x[n-1] + ... + h[216]x[n-216]
```

where the tracked Q1.17 coefficients are stored in
`tables/fir_decim16_q1_17.hex`. They are symmetric, so an optimized
implementation can pre-add matching samples and share one multiplication:

```text
h[k]x[n-k] + h[k]x[n-(216-k)]
    = h[k] * (x[n-k] + x[n-(216-k)])
```

The filter length follows from the numerical specification rather than an
arbitrary resource choice:

| Property | Requirement |
|---|---:|
| Input rate | 200 Msamples/s |
| Passband edge | 2 MHz |
| Stopband edge | 6.25 MHz |
| Maximum passband ripple | 0.1 dB |
| Minimum stopband attenuation | 70 dB |
| Coefficient format | signed Q1.17 |

A 205-tap Kaiser design met the floating-point estimate but fell to
approximately 69.32 dB attenuation after Q1.17 coefficient quantization. The
217-tap design measures approximately 0.0051 dB passband ripple and 71.62 dB
stopband attenuation after quantization, retaining margin in the actual fixed
point implementation.

The odd-length linear-phase filter has a group delay of 108 input samples, or
540 ns at 200 MHz. The frozen decimation phase emits results after input
samples 15, 31, 47, and so on. An optimized polyphase implementation may avoid
calculating discarded results, but it must remain mathematically identical to
filtering every input and then selecting those indices.

Each retained complex sample packs signed Q1.15 I and Q into one 32-bit word.
The nominal payload is therefore:

```text
12.5 Msamples/s * 4 bytes = 50 MB/s = 400 Mb/s
```

This is one eighth of the 400 MB/s raw 16-bit input payload and is practical
for the existing DMA path and a later Gigabit Ethernet data plane.

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
                          |
                          v
               +---------------------+
               | 217-tap FIR, /16    |
               | full precision      |
               +----------+----------+
                          |
                          v
                 signed Q1.15 I and Q,
                     valid/ready
```

| Module | Responsibility |
|---|---|
| `qcrate_nco.sv` | 32-bit phase accumulation and bit-exact Q1.15 sine/cosine generation |
| `qcrate_ddc_mixer.sv` | Signed products, ties-away rounding, and 18-bit Q1.17 saturation |
| `qcrate_dsp_frontend.sv` | ADC/LO pipeline alignment and ready/valid backpressure |
| `qcrate_fir_quantizer.sv` | Full-precision FIR accumulator narrowing to signed Q1.15 |
| `xilinx/qcrate_fir_decim16.sv` | Vendor boundary, two-path packing, and elastic output register |
| `xilinx/create_fir_compiler.tcl` | Reproducible FIR Compiler 7.2 configuration from the tracked coefficient table |

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

## FIR implementation decision

Three implementations were considered for DSP-2A:

| Option | Merit | Cost |
|---|---|---|
| Xilinx FIR Compiler behind a Q-Crate wrapper | Mature polyphase/symmetry optimization, AXI backpressure, and strong 200 MHz timing prospects | Vendor-specific generated backend |
| Handwritten folded/polyphase FIR | Portable and highly educational | Complex MAC scheduling, delay-memory banking, and substantially larger verification scope |
| Fully parallel handwritten FIR | Simple dataflow | Excessive DSP use for two 217-tap channels |

Q-Crate uses FIR Compiler behind `qcrate_fir_decim16`. Only the generated
backend is vendor-specific; the signed ready/valid I/Q interface and numerical
contract remain project-owned. The Tcl generator reads the canonical hex table
directly, validates its 217 entries, symmetry, and exact integer sum, and then
configures one two-path decimator. No generated `.xci`, simulation product, or
synthesis checkpoint is committed.

The generator appends three zero coefficients after the 217-tap impulse
response. These zeros do not change the transfer function. They make the
FIR Compiler's four-coefficient implementation padding explicit at the tail;
without them, the core places the padding at the front and shifts the first
decimated result from the contract's input index 15 to index 12. The exact
accumulator test protects this sampling-phase requirement.

FIR Compiler emits one full-precision 36-bit accumulator for each path. It is
not allowed to round or truncate. `qcrate_fir_quantizer` shifts each Q2.34
accumulator by 19 fractional bits, rounds exact ties away from zero, saturates
to signed 16-bit Q1.15, and registers the result behind a one-entry elastic
stage. This preserves Q-Crate semantics independently of vendor rounding-mode
names.

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
PASS: qcrate_fir_quantizer_tb
PASS: all portable DSP RTL tests
```

The NCO test inserts deterministic clock-enable gaps. The mixer test covers
positive full scale, saturation at both 18-bit limits, and positive/negative
half ties. The frontend test inserts deterministic source gaps and randomized
output backpressure while comparing every accepted output against Python. The
quantizer test covers both rounding signs, exact half ties, output limits, and
saturation without requiring Vivado.

Generate the stage vectors without running RTL simulation when inspecting the
contract:

```bash
python3 host/dsp_model/qcrate_dsp.py generate-rtl-vectors \
  host/dsp_model/configs/tone_1mhz.json \
  build/dsp/rtl_vectors \
  --samples 1024
```

The generated-IP test creates 4096 exact mixed I/Q inputs and 256 FIR outputs,
recreates FIR Compiler from Tcl, and runs a focused XSim simulation:

```bash
python3 rtl/dsp/xilinx/run_test.py
```

Its self-checking testbench compares every full-precision accumulator and every
narrowed output against Python while inserting source gaps and randomized
backpressure. Success ends with:

```text
PASS: qcrate_fir_decim16_tb verified 256 exact outputs
PASS: DSP-2A generated-IP XSim flow
```

The same test can retain its completed waveform in the Vivado GUI:

```bash
python3 rtl/dsp/xilinx/run_test.py --open-waveform
```

The GUI remains open until it is closed manually. Input vectors are streamed
from files rather than added as waveform arrays; relevant handshake, I/Q, and
FIR signals remain available for waveform inspection.

No board build is required for DSP-2A unit acceptance. DSP-2B will connect the
complete chain to AXI framing, select it with `STREAM_MODE=1`, and compare DMA
captures against the same model.

## KV260 synthesis checks

The portable DSP-1 frontend and generated DSP-2A FIR have separate OOC checks.
Keeping their reports separate prevents a successful mixer synthesis from
being mistaken for FIR synthesis. Neither command modifies the deployed KV260
design.

Run the portable NCO and mixer check with:

```bash
source /tools/Xilinx/Vivado/2024.2/settings64.sh
vivado -mode batch -nojournal -nolog \
  -source rtl/dsp/synth_ooc.tcl
```

Expected final output is:

```text
PASS: DSP-1 OOC synthesis and 200 MHz timing
```

Resource inference is recorded in `build/vivado/dsp_ooc/utilization.rpt`, and
setup timing is recorded in `build/vivado/dsp_ooc/timing_summary.rpt`.
This report does not include FIR Compiler.

Run the generated two-path FIR wrapper check with:

```bash
python3 rtl/dsp/xilinx/run_synthesis.py
```

The flow creates FIR Compiler from the tracked Tcl and coefficient table,
generates its OOC checkpoint with `synth_ip`, and then synthesizes
`qcrate_fir_decim16` against the K26 part. Expected final output is:

```text
PASS: DSP-2A FIR OOC synthesis and 200 MHz timing
```

FIR resource inference is recorded in
`build/vivado/dsp_fir_ooc/reports/utilization.rpt`; setup timing is recorded in
`build/vivado/dsp_fir_ooc/reports/timing_summary.rpt`. The generated checkpoint
is `build/vivado/dsp_fir_ooc/qcrate_fir_decim16.dcp`.

Both checks require no `ERROR` or relevant `CRITICAL WARNING`, nonnegative
setup slack, and no unresolved black boxes. DSP-1 must infer DSP48s for the
mixers and block memory for the NCO table. DSP-2A must infer DSP48s for the FIR;
its exact DSP and memory counts are recorded rather than frozen because Vivado
IP implementation choices can change with the target part and tool release.
