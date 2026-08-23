# Q-Crate DSP-0 reference model

## Objective

Q-Crate ultimately needs to acquire a high-rate digitized waveform, select a
narrower band of interest, and move that useful data through DMA and a network
without transporting every raw ADC sample. DSP-0 defines the first receive
chain for that job: a digital downconverter that translates one selected band
to complex baseband, removes frequencies that would alias, and reduces the
sample rate from 200 Msamples/s to 12.5 Msamples/s.

This milestone deliberately happens in Python before RTL. Fixed-point DSP can
produce plausible-looking waveforms while still differing by one least
significant bit because of coefficient order, rounding, saturation, or
decimation phase. Resolving those choices in a fast host model gives the future
RTL an executable specification and known-good output vectors. It also lets us
evaluate bandwidth and numerical precision without rebuilding the FPGA.

DSP-0 therefore provides two models driven by one strict JSON configuration:

- a floating-point signal-processing reference;
- a bit-accurate integer model intended to define the future RTL boundary.

The bit-accurate model, generated tables, packed words, and tests are the
normative contract. The floating model is an independent accuracy reference;
RTL is expected to match the integer model exactly, not approximately.

DSP-0 does not yet contain synthesizable DSP RTL, interface a physical ADC, or
perform UDP transport. The synthetic signal generator stands in for an ADC so
the mathematical and binary contracts can be completed independently of a
particular converter or carrier board.

## Terminology

| Term | Meaning in this project |
|---|---|
| ADC | Analog-to-digital converter; the future physical source of signed waveform samples |
| DSP | Digital signal processing performed numerically on sampled data |
| DDC | Digital downconverter; mixer, low-pass filter, and decimator used to select a frequency band |
| LO | Local oscillator; the reference frequency mixed with the input to move the selected band toward 0 Hz |
| NCO | Numerically controlled oscillator; a phase accumulator and lookup table that generate digital sine/cosine samples |
| I/Q | In-phase and quadrature components; one complex sample represented as `I + jQ` |
| FIR | Finite impulse response filter; a weighted sum of current and previous samples |
| Decimation | Keeping one filtered sample out of every `D` samples to reduce sample rate by factor `D` |
| Baseband | Frequencies near 0 Hz after mixing; here the desired signal appears at `signal frequency - LO frequency` |
| Q1.15 | Signed fixed-point value with one sign/integer bit and 15 fractional bits |
| AXI4-Stream | Ready/valid streaming interface used to transfer samples into AXI DMA |
| `TDATA` / `TLAST` | AXI4-Stream payload and end-of-frame marker |
| Backpressure | A downstream receiver deasserting `TREADY` because it cannot accept another word |

For the reference experiment, a synthetic 30 MHz real cosine is mixed with a
29 MHz LO. The desired result is therefore a 1 MHz complex baseband tone. The
signal NCO exists only to generate repeatable ADC-like test stimulus; the LO
NCO is part of the actual downconverter and remains when a physical ADC is
connected.

The low-pass FIR must run before decimation. Simply discarding 15 of every 16
samples would fold frequencies above the new 6.25 MHz Nyquist limit into the
retained baseband and permanently corrupt it.

## Signal chain

```text
Q1.15 synthetic ADC source at 200 MHz
                  |
                  v
       32-bit signal NCO + noise
                  |
                  v
  complex mixer with 32-bit LO NCO
        I = x*cos(LO), Q = -x*sin(LO)
                  |
                  v
  217-tap Q1.17 low-pass FIR, decimate by 16
                  |
                  v
 Q1.15 complex output at 12.5 Msamples/s
        TDATA[15:0]  = signed I
        TDATA[31:16] = signed Q
```

The nominal payload rate is 50 MB/s, or 400 Mb/s, before transport overhead.
A real input cosine of amplitude `A` produces a desired complex baseband tone
of amplitude approximately `A/2`; DSP-0 intentionally applies no compensating
factor of two in the mixer.

## What changes for another instrument

The JSON configuration describes one experiment: frequencies, phases,
amplitudes, deterministic noise, and frame dimensions. The architectural
constants such as sample rate, decimation, widths, and filter specification
are intentionally frozen in `qcrate_dsp.py`. Changing those constants creates
a new numerical contract and requires regenerated tables, vectors, and tests.

| Requirement change | Engineering work required |
|---|---|
| Different input sample rate | Change the model sample rate; recompute NCO tuning words and FIR normalized frequencies; regenerate and reverify coefficients; update RTL clocks, timing constraints, and throughput calculations |
| Wider or narrower signal bandwidth | Re-select passband and stopband edges, reconsider decimation, redesign the FIR, and verify ripple, attenuation, transition width, and resource cost |
| Different decimation factor | Recalculate output rate and Nyquist limit, redesign the anti-alias filter, define the new output sampling phase, and check DMA/network bandwidth |
| More ADC channels | Decide whether each channel has an independent LO/filter or shares settings; replicate or time-multiplex processing; define channel ordering and framing; resize DMA and transport throughput |
| Different sample or coefficient widths | Re-run overflow and quantization analysis, select accumulator width, regenerate tables and exact vectors, and update AXI packing and software decoding |
| Real free-running ADC | Replace the synthetic source with the converter interface and an elastic FIFO; define clock-domain crossing, overflow detection, timestamping, and loss reporting |

For example, increasing only the ADC rate is not a one-line clock change. A
400 MHz input with the same absolute passband has half the normalized filter
transition width and can require more FIR resources. Conversely, adding four
channels at the current 12.5 Msamples/s complex output produces a 200 MB/s
payload and changes the DMA and network architecture even if the per-channel
DSP remains identical.

## Frozen numerical contract

| Stage | Representation and operation |
|---|---|
| ADC/source | signed 16-bit Q1.15, saturating addition of tone and noise |
| NCO phase | unsigned 32-bit accumulator, lookup current phase then increment |
| NCO table | top 12 phase bits, 1025-value quarter-wave signed Q1.15 table |
| Mixer | signed 18-bit Q1.17 I and Q after rounded, saturated products |
| FIR coefficients | signed 18-bit Q1.17, symmetric, exact integer sum `131072` |
| FIR accumulator | signed 48-bit contract; model raises on overflow |
| FIR output | signed 16-bit Q1.15 after rounding and saturation |
| Narrowing | round to nearest, exact ties away from zero, then saturate |
| AXI stream | I in low half, Q in high half, all `TKEEP` bits set |

The tuning word is

```text
round(frequency_hz * 2^32 / 200000000) modulo 2^32
```

Initial signal and LO phases are loaded on `START`. Their accumulators run
continuously across AXI frame boundaries. Under downstream backpressure the
synthetic source, both NCOs, FIR, and decimation phase must all freeze so the
output word remains stable. A future free-running ADC source instead requires
an input FIFO and explicit overflow accounting; silently stalling an ADC is
not an acceptable hardware assumption.

The deterministic noise source is a 16-bit right-shifting Galois LFSR with
feedback mask `0xb400`. Its nonzero seed is advanced before each signed Q1.15
sample is used.

## Filter decision

The low-pass contract is:

- passband edge: 2 MHz;
- stopband edge: 6.25 MHz, the post-decimation Nyquist limit;
- passband ripple: at most 0.1 dB;
- stopband attenuation: at least 70 dB;
- Kaiser beta: 6.75526;
- design cutoff: 4.125 MHz.

The textbook Kaiser estimate is 205 taps. That floating-point filter reaches
about -70.14 dB, but Q1.17 coefficient quantization reduces it to about
-69.32 dB and violates the contract. DSP-0 therefore uses 217 taps. The
tracked quantized filter measures about 0.0051 dB passband ripple and
-71.62 dB worst-case stopband response.

The odd-length linear-phase FIR has a 108-input-sample group delay, 540 ns at
200 MHz. It is causal and starts with zero prehistory. Decimated outputs are
taken after input samples 15, 31, 47, and so on; output index 13 is the first
whose complete 217-sample window is present.

## Frames and stream mode

The JSON fields `frame_length` and `frame_count` correspond to the existing
stream register semantics. `TLAST` is asserted on every `frame_length`-th IQ
word. A frame boundary does not reset DSP state.

Future RTL keeps `STREAM_MODE=0` as the legacy counter generator and assigns
`STREAM_MODE=1` to this DSP IQ chain. `FRAME_LENGTH` always counts output IQ
words, not 200 MHz input samples.

## Reproducible setup

The model requires Python 3.10 or newer and the pinned NumPy version used to
generate the tracked tables and acceptance vector:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r host/dsp_model/requirements.txt
```

The canonical tables live in `rtl/dsp/tables` because Python and synthesizable
RTL consume the same numerical artifacts. Do not regenerate them as part of an
FPGA build. They are reviewed source artifacts and change only after an
intentional numerical-contract revision:

```bash
python3 host/dsp_model/generate_coeffs.py
```

Check that tracked files still match the generator without rewriting them:

```bash
python3 host/dsp_model/generate_coeffs.py --check
```

Run and summarize the reference experiment:

```bash
python3 host/dsp_model/qcrate_dsp.py inspect \
  host/dsp_model/configs/tone_1mhz.json
```

The accepted packed-word SHA-256 is
`e645b9eeb7506178ac0de33d08a2d8d21b90f80289859c557be30e3b09583a4b`.
Generate an ignored compressed vector for offline inspection when needed:

```bash
python3 host/dsp_model/qcrate_dsp.py generate \
  host/dsp_model/configs/tone_1mhz.json \
  build/dsp/tone_1mhz.npz
```

Run all focused contract tests:

```bash
python3 -m unittest discover -s host/dsp_model/tests -v
```

## KV260 capture viewer

The self-checking DMA tool proves every captured word, but a visual report is
useful for understanding what the DDC does and for recognizing spectral or
I/Q defects. `qcrate_capture_viewer.py` reads the raw little-endian DMA file,
decodes `TDATA[31:16]` as signed Q and `TDATA[15:0]` as signed I, regenerates
the exact experiment, and creates:

- the modeled 200 MS/s synthetic ADC input;
- captured 12.5 MS/s I and Q waveforms;
- the captured IQ constellation;
- the captured complex spectrum in dBFS;
- a JSON summary containing the SHA-256, exact mismatch count, and dominant
  frequency.

The input panel is explicitly labeled as modeled because mode-1 DMA receives
only the post-decimation IQ stream. The output, constellation, spectrum, hash,
and comparison results come from the board capture.

### Accepted KV260 result

![KV260 DSP-2B DMA capture](images/kv260_dsp2b_capture.png)

This image was generated from an actual KV260 mode-1 DMA capture after the
DSP-2B bitstream and PetaLinux image passed board acceptance. The capture
contains four frames of 1024 packed IQ words, or 4096 complex samples and
16,384 bytes. It matched the deployed standard-library reference model at
every word:

```text
first/last         : 0x00010001 / 0x23c71f8d
reference mismatch : 0
capture SHA-256    : e645b9eeb7506178ac0de33d08a2d8d21b90f80289859c557be30e3b09583a4b
```

The four panels show different parts of the numerical contract:

1. **Modeled ADC input:** the deterministic 30 MHz real Q1.15 source at
   200 MS/s. This panel is regenerated from the accepted configuration because
   the DMA interface is downstream of the DDC and does not capture raw ADC
   samples.
2. **Captured I/Q output:** the actual 12.5 MS/s complex DMA stream. The short
   startup transient is the expected response of the 217-tap FIR beginning
   with zero sample history. The settled I and Q waveforms are approximately
   one quarter-cycle apart.
3. **Captured constellation:** startup samples move outward from the origin,
   then settle on a ring with magnitude near 0.375 full scale. The 25 primary
   phase positions follow from the rational output tone ratio
   `1 MHz / 12.5 MHz = 2/25`; deterministic source noise and fixed-point
   quantization broaden those positions slightly.
4. **Captured spectrum:** the DDC translates the 30 MHz input by the 29 MHz LO
   to the expected positive 1 MHz complex tone. With 4096 output samples, FFT
   bins are 3051.7578125 Hz apart, so the strongest sampled bin is
   1.0009765625 MHz. That bin location is FFT resolution, not NCO frequency
   error. The surrounding shaped floor includes deterministic source noise,
   FIR response, finite capture length, and fixed-point effects.

The visual report complements rather than replaces exact verification. A
plausible waveform can hide one-bit arithmetic, packing, or phase errors; the
zero mismatch count and accepted SHA-256 establish correctness, while the
plots make the signal transformation understandable by inspection.

Install the optional plotting dependency in the host Python environment:

```bash
python3 -m pip install -r host/dsp_model/requirements-viewer.txt
```

Create the raw file on KV260:

```bash
sudo qcrate-dma capture-dsp --output /tmp/qcrate-dsp.bin
```

Transfer it to the development PC without committing the binary payload:

```bash
mkdir -p build/dsp/captures
scp petalinux@<board-ip>:/tmp/qcrate-dsp.bin \
  build/dsp/captures/qcrate-dsp.bin
```

Generate an ignored JSON audit and a trackable board-result image:

```bash
python3 host/dsp_model/qcrate_capture_viewer.py \
  build/dsp/captures/qcrate-dsp.bin \
  --output host/dsp_model/images/kv260_dsp2b_capture.png \
  --summary build/dsp/captures/qcrate-dsp.json
```

Add `--show` to open the completed figure interactively. A valid default
capture reports zero reference mismatches and a dominant complex tone near
`+1 MHz`. Review the PNG before committing it: the public image must represent
an actual KV260 DMA capture, not a model-only vector.

Generate the intermediate ADC, LO, and mixer vectors used by DSP-1 RTL:

```bash
python3 host/dsp_model/qcrate_dsp.py generate-rtl-vectors \
  host/dsp_model/configs/tone_1mhz.json \
  build/dsp/rtl_vectors \
  --samples 1024
```

Generate exact mixed inputs, 36-bit accumulators, and Q1.15 outputs for the
DSP-2A FIR Compiler test:

```bash
python3 host/dsp_model/qcrate_dsp.py generate-fir-vectors \
  host/dsp_model/configs/tone_1mhz.json \
  build/dsp/fir_vectors \
  --outputs 256
```

These are host-only commands. DSP-0 does not require Vivado, Vitis, a
PetaLinux build, or the KV260 board.

## RTL handoff

The next DSP milestone may implement the NCO/mixer and choose Xilinx FIR
Compiler for the decimating FIR. Vendor IP is an implementation choice, not a
replacement for this contract. RTL acceptance must compare every packed word
and `TLAST` position against the bit-accurate model while applying randomized
AXI backpressure. Any change to widths, rounding, saturation, LUT addressing,
coefficient ordering, decimation phase, or reset behavior requires an explicit
contract revision and regenerated vectors.
