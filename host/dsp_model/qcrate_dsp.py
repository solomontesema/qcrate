#!/usr/bin/env python3
"""Floating-point and bit-accurate reference model for the Q-Crate DSP chain."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import numpy as np


SAMPLE_RATE_HZ = 200_000_000
DECIMATION = 16
PHASE_BITS = 32
PHASE_MODULUS = 1 << PHASE_BITS
QUARTER_PHASE = PHASE_MODULUS >> 2
LUT_ADDR_BITS = 12
QUARTER_LUT_BITS = LUT_ADDR_BITS - 2
QUARTER_LUT_SIZE = (1 << QUARTER_LUT_BITS) + 1

ADC_BITS = 16
ADC_FRAC_BITS = 15
MIXER_BITS = 18
MIXER_FRAC_BITS = 17
COEFFICIENT_BITS = 18
COEFFICIENT_FRAC_BITS = 17
FIR_ACCUMULATOR_BITS = 48
OUTPUT_BITS = 16
OUTPUT_FRAC_BITS = 15

PASSBAND_HZ = 2_000_000
STOPBAND_HZ = 6_250_000
FIR_CUTOFF_HZ = (PASSBAND_HZ + STOPBAND_HZ) // 2
FIR_ESTIMATED_TAPS = 205
FIR_TAPS = 217
KAISER_BETA = 6.75526

FORMAT_NAME = "qcrate-dsp-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
TABLE_DIR = REPO_ROOT / "rtl" / "dsp" / "tables"
SINE_TABLE = TABLE_DIR / "sine_quarter_q1_15.hex"
FIR_TABLE = TABLE_DIR / "fir_decim16_q1_17.hex"


class DspConfigurationError(ValueError):
    """Raised when a DSP model configuration violates the frozen contract."""


def saturate(value: int, bits: int) -> int:
    """Clamp an integer to a signed two's-complement width."""
    minimum = -(1 << (bits - 1))
    maximum = (1 << (bits - 1)) - 1
    return min(maximum, max(minimum, int(value)))


def round_shift(value: int, shift: int) -> int:
    """Right shift with round-to-nearest and ties away from zero."""
    if shift < 0:
        return int(value) << -shift
    if shift == 0:
        return int(value)
    magnitude = abs(int(value))
    rounded = (magnitude + (1 << (shift - 1))) >> shift
    return rounded if value >= 0 else -rounded


def decimal_to_fixed(value: Decimal | int | float | str, bits: int,
                     fractional_bits: int) -> int:
    """Quantize a decimal value without relying on Python binary rounding."""
    scaled = Decimal(str(value)) * (1 << fractional_bits)
    integer = int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
    return saturate(integer, bits)


def frequency_to_phase_word(frequency_hz: Decimal | int | float | str,
                            sample_rate_hz: int = SAMPLE_RATE_HZ) -> int:
    """Convert hertz to a modulo-2^32 NCO tuning word."""
    scaled = Decimal(str(frequency_hz)) * PHASE_MODULUS / sample_rate_hz
    word = int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
    return word & (PHASE_MODULUS - 1)


def turns_to_phase_word(turns: Decimal | int | float | str) -> int:
    """Convert phase in turns to a modulo-2^32 phase word."""
    scaled = Decimal(str(turns)) * PHASE_MODULUS
    word = int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
    return word & (PHASE_MODULUS - 1)


def _round_float_away(values: np.ndarray) -> np.ndarray:
    magnitude = np.floor(np.abs(values) + 0.5)
    return np.copysign(magnitude, values).astype(np.int64)


def generate_quarter_sine_lut() -> np.ndarray:
    """Generate quadrant-zero sine samples, including both endpoints."""
    angles = np.arange(QUARTER_LUT_SIZE, dtype=np.float64)
    angles *= (math.pi / 2.0) / (QUARTER_LUT_SIZE - 1)
    values = _round_float_away(np.sin(angles) * (1 << ADC_FRAC_BITS))
    return np.clip(values, -(1 << 15), (1 << 15) - 1).astype(np.int16)


def nco_lookup(phase_word: int, quarter_lut: np.ndarray) -> int:
    """Return one Q1.15 sine sample using a 12-bit full-wave phase index."""
    if len(quarter_lut) != QUARTER_LUT_SIZE:
        raise ValueError(f"quarter-wave LUT must contain {QUARTER_LUT_SIZE} values")
    full_index = (int(phase_word) & (PHASE_MODULUS - 1)) >> (
        PHASE_BITS - LUT_ADDR_BITS
    )
    quadrant = full_index >> QUARTER_LUT_BITS
    offset = full_index & ((1 << QUARTER_LUT_BITS) - 1)
    if quadrant == 0:
        return int(quarter_lut[offset])
    if quadrant == 1:
        return int(quarter_lut[(1 << QUARTER_LUT_BITS) - offset])
    if quadrant == 2:
        return -int(quarter_lut[offset])
    return -int(quarter_lut[(1 << QUARTER_LUT_BITS) - offset])


def generate_float_fir_coefficients() -> np.ndarray:
    """Design the frozen Kaiser-windowed low-pass decimation filter."""
    sample_offsets = np.arange(FIR_TAPS, dtype=np.float64) - (FIR_TAPS - 1) / 2
    normalized_cutoff = FIR_CUTOFF_HZ / SAMPLE_RATE_HZ
    coefficients = (
        2.0
        * normalized_cutoff
        * np.sinc(2.0 * normalized_cutoff * sample_offsets)
        * np.kaiser(FIR_TAPS, KAISER_BETA)
    )
    coefficients /= np.sum(coefficients)
    return coefficients


def quantize_fir_coefficients(coefficients: np.ndarray) -> np.ndarray:
    """Quantize a symmetric FIR to Q1.17 and force exact unity DC gain."""
    if coefficients.shape != (FIR_TAPS,):
        raise ValueError(f"FIR must contain exactly {FIR_TAPS} coefficients")
    quantized = _round_float_away(
        coefficients * (1 << COEFFICIENT_FRAC_BITS)
    )
    quantized = np.clip(
        quantized,
        -(1 << (COEFFICIENT_BITS - 1)),
        (1 << (COEFFICIENT_BITS - 1)) - 1,
    ).astype(np.int64)
    quantized[FIR_TAPS // 2] += (1 << COEFFICIENT_FRAC_BITS) - int(
        np.sum(quantized)
    )
    if not np.array_equal(quantized, quantized[::-1]):
        raise RuntimeError("quantized FIR lost coefficient symmetry")
    if np.any(quantized < -(1 << 17)) or np.any(quantized > (1 << 17) - 1):
        raise RuntimeError("unity-gain correction overflowed Q1.17")
    return quantized.astype(np.int32)


def render_hex(values: np.ndarray, bits: int) -> str:
    """Render signed integers as fixed-width two's-complement readmemh input."""
    digits = (bits + 3) // 4
    mask = (1 << bits) - 1
    return "".join(f"{int(value) & mask:0{digits}x}\n" for value in values)


def load_hex(path: Path, bits: int, expected_length: int) -> np.ndarray:
    """Load a tracked two's-complement table and validate its dimensions."""
    values: list[int] = []
    sign = 1 << (bits - 1)
    modulus = 1 << bits
    with path.open("r", encoding="ascii") as stream:
        for line_number, line in enumerate(stream, start=1):
            text = line.strip()
            if not text:
                continue
            raw = int(text, 16)
            if raw >= modulus:
                raise ValueError(f"{path}:{line_number}: value exceeds {bits} bits")
            values.append(raw - modulus if raw & sign else raw)
    if len(values) != expected_length:
        raise ValueError(
            f"{path} contains {len(values)} values; expected {expected_length}"
        )
    return np.asarray(values, dtype=np.int32)


def load_default_tables() -> tuple[np.ndarray, np.ndarray]:
    sine = load_hex(SINE_TABLE, ADC_BITS, QUARTER_LUT_SIZE).astype(np.int16)
    fir = load_hex(FIR_TABLE, COEFFICIENT_BITS, FIR_TAPS)
    return sine, fir


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DspConfigurationError(f"duplicate configuration key {key!r}")
        result[key] = value
    return result


@dataclass(frozen=True)
class DspConfig:
    sample_rate_hz: int
    decimation: int
    frame_length: int
    frame_count: int
    signal_frequency_hz: Decimal
    lo_frequency_hz: Decimal
    signal_amplitude: Decimal
    signal_phase_turns: Decimal
    lo_phase_turns: Decimal
    noise_amplitude: Decimal
    noise_seed: int

    @property
    def output_samples(self) -> int:
        return self.frame_length * self.frame_count


def load_config(path: Path) -> DspConfig:
    """Load and strictly validate one readable DSP experiment configuration."""
    with path.open("r", encoding="utf-8") as stream:
        document = json.load(
            stream,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=Decimal,
        )
    required = {
        "format",
        "sample_rate_hz",
        "decimation",
        "frame_length",
        "frame_count",
        "signal_frequency_hz",
        "lo_frequency_hz",
        "signal_amplitude",
        "signal_phase_turns",
        "lo_phase_turns",
        "noise_amplitude",
        "noise_seed",
    }
    if not isinstance(document, dict):
        raise DspConfigurationError("configuration root must be an object")
    unknown = set(document) - required
    missing = required - set(document)
    if unknown or missing:
        raise DspConfigurationError(
            f"configuration keys differ: missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )
    if document["format"] != FORMAT_NAME:
        raise DspConfigurationError(f"format must be {FORMAT_NAME!r}")

    integer_fields = (
        "sample_rate_hz",
        "decimation",
        "frame_length",
        "frame_count",
        "noise_seed",
    )
    for field in integer_fields:
        if isinstance(document[field], bool) or not isinstance(document[field], int):
            raise DspConfigurationError(f"{field} must be an integer")

    config = DspConfig(
        sample_rate_hz=document["sample_rate_hz"],
        decimation=document["decimation"],
        frame_length=document["frame_length"],
        frame_count=document["frame_count"],
        signal_frequency_hz=Decimal(document["signal_frequency_hz"]),
        lo_frequency_hz=Decimal(document["lo_frequency_hz"]),
        signal_amplitude=Decimal(document["signal_amplitude"]),
        signal_phase_turns=Decimal(document["signal_phase_turns"]),
        lo_phase_turns=Decimal(document["lo_phase_turns"]),
        noise_amplitude=Decimal(document["noise_amplitude"]),
        noise_seed=document["noise_seed"],
    )
    if config.sample_rate_hz != SAMPLE_RATE_HZ or config.decimation != DECIMATION:
        raise DspConfigurationError(
            f"DSP v1 requires {SAMPLE_RATE_HZ} Hz and decimation {DECIMATION}"
        )
    if not 1 <= config.frame_length <= 262_144:
        raise DspConfigurationError("frame_length must be between 1 and 262144")
    if not 1 <= config.frame_count <= 255:
        raise DspConfigurationError("frame_count must be between 1 and 255")
    if config.output_samples > 1_000_000:
        raise DspConfigurationError("total output words must not exceed 1000000")
    nyquist = Decimal(config.sample_rate_hz) / 2
    for name, frequency in (
        ("signal_frequency_hz", config.signal_frequency_hz),
        ("lo_frequency_hz", config.lo_frequency_hz),
    ):
        if frequency < 0 or frequency >= nyquist:
            raise DspConfigurationError(f"{name} must be in [0, Nyquist)")
    for name, value in (
        ("signal_amplitude", config.signal_amplitude),
        ("noise_amplitude", config.noise_amplitude),
    ):
        if value < 0 or value > 1:
            raise DspConfigurationError(f"{name} must be between 0 and 1")
    if config.signal_amplitude + config.noise_amplitude > 1:
        raise DspConfigurationError("signal plus noise amplitude must not exceed 1")
    if not 1 <= config.noise_seed <= 0xFFFF:
        raise DspConfigurationError("noise_seed must be a nonzero 16-bit value")
    return config


def lfsr16_noise(count: int, seed: int) -> np.ndarray:
    """Generate the signed Q1.15 sequence from a maximal 16-bit Galois LFSR."""
    if not 1 <= seed <= 0xFFFF:
        raise ValueError("LFSR seed must be a nonzero 16-bit value")
    result = np.empty(count, dtype=np.int32)
    state = seed
    for index in range(count):
        lsb = state & 1
        state >>= 1
        if lsb:
            state ^= 0xB400
        result[index] = state - 0x8000
    return result


@dataclass(frozen=True)
class FixedFrontendTrace:
    """Bit-accurate samples at the future ADC-to-FIR RTL boundary."""

    adc: np.ndarray
    lo_sin: np.ndarray
    lo_cos: np.ndarray
    mixed_i: np.ndarray
    mixed_q: np.ndarray
    signal_phase_word: int
    signal_phase_initial: int
    lo_phase_word: int
    lo_phase_initial: int


def _run_fixed_frontend(config: DspConfig, sine_lut: np.ndarray,
                        noise_q15: np.ndarray) -> FixedFrontendTrace:
    sample_count = len(noise_q15)
    signal_word = frequency_to_phase_word(config.signal_frequency_hz)
    lo_word = frequency_to_phase_word(config.lo_frequency_hz)
    signal_initial = turns_to_phase_word(config.signal_phase_turns)
    lo_initial = turns_to_phase_word(config.lo_phase_turns)
    signal_phase = signal_initial
    lo_phase = lo_initial
    amplitude = decimal_to_fixed(config.signal_amplitude, ADC_BITS, ADC_FRAC_BITS)
    noise_amplitude = decimal_to_fixed(
        config.noise_amplitude, ADC_BITS, ADC_FRAC_BITS
    )

    adc_samples = np.empty(sample_count, dtype=np.int16)
    lo_sine = np.empty(sample_count, dtype=np.int16)
    lo_cosine = np.empty(sample_count, dtype=np.int16)
    mixed_i = np.empty(sample_count, dtype=np.int32)
    mixed_q = np.empty(sample_count, dtype=np.int32)
    for index in range(sample_count):
        source_cos = nco_lookup(signal_phase + QUARTER_PHASE, sine_lut)
        lo_cos = nco_lookup(lo_phase + QUARTER_PHASE, sine_lut)
        lo_sin = nco_lookup(lo_phase, sine_lut)
        source = saturate(round_shift(source_cos * amplitude, ADC_FRAC_BITS), ADC_BITS)
        noise = saturate(
            round_shift(int(noise_q15[index]) * noise_amplitude, ADC_FRAC_BITS),
            ADC_BITS,
        )
        adc = saturate(source + noise, ADC_BITS)
        adc_samples[index] = adc
        lo_sine[index] = lo_sin
        lo_cosine[index] = lo_cos
        mixed_i[index] = saturate(
            round_shift(adc * lo_cos, 2 * ADC_FRAC_BITS - MIXER_FRAC_BITS),
            MIXER_BITS,
        )
        mixed_q[index] = saturate(
            round_shift(-adc * lo_sin, 2 * ADC_FRAC_BITS - MIXER_FRAC_BITS),
            MIXER_BITS,
        )
        signal_phase = (signal_phase + signal_word) & (PHASE_MODULUS - 1)
        lo_phase = (lo_phase + lo_word) & (PHASE_MODULUS - 1)

    return FixedFrontendTrace(
        adc=adc_samples,
        lo_sin=lo_sine,
        lo_cos=lo_cosine,
        mixed_i=mixed_i,
        mixed_q=mixed_q,
        signal_phase_word=signal_word,
        signal_phase_initial=signal_initial,
        lo_phase_word=lo_word,
        lo_phase_initial=lo_initial,
    )


def run_fixed_frontend(config: DspConfig, sample_count: int,
                       sine_lut: np.ndarray | None = None) -> FixedFrontendTrace:
    """Generate exact ADC, LO, and mixer samples for RTL verification."""
    if sample_count < 1:
        raise ValueError("sample_count must be positive")
    if sample_count > config.output_samples * config.decimation:
        raise ValueError("sample_count exceeds the configured input capture")
    if sine_lut is None:
        sine_lut = load_hex(
            SINE_TABLE, ADC_BITS, QUARTER_LUT_SIZE
        ).astype(np.int16)
    noise = lfsr16_noise(sample_count, config.noise_seed)
    return _run_fixed_frontend(config, sine_lut, noise)


@dataclass(frozen=True)
class DspResult:
    float_i: np.ndarray
    float_q: np.ndarray
    fixed_i: np.ndarray
    fixed_q: np.ndarray
    packed_words: np.ndarray
    tlast: np.ndarray
    signal_phase_word: int
    lo_phase_word: int

    @property
    def sha256(self) -> str:
        little_endian = self.packed_words.astype("<u4", copy=False)
        return hashlib.sha256(little_endian.tobytes()).hexdigest()


def _run_float(config: DspConfig, float_coefficients: np.ndarray,
               noise_q15: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    input_samples = config.output_samples * config.decimation
    indexes = np.arange(input_samples, dtype=np.float64)
    sample_rate = float(config.sample_rate_hz)
    signal_phase = 2.0 * math.pi * (
        float(config.signal_phase_turns)
        + float(config.signal_frequency_hz) * indexes / sample_rate
    )
    lo_phase = 2.0 * math.pi * (
        float(config.lo_phase_turns)
        + float(config.lo_frequency_hz) * indexes / sample_rate
    )
    adc = float(config.signal_amplitude) * np.cos(signal_phase)
    adc += float(config.noise_amplitude) * noise_q15 / (1 << ADC_FRAC_BITS)
    adc = np.clip(adc, -1.0, 1.0 - 1.0 / (1 << ADC_FRAC_BITS))
    mixed_i = adc * np.cos(lo_phase)
    mixed_q = -adc * np.sin(lo_phase)
    filtered_i = np.convolve(mixed_i, float_coefficients, mode="full")[:input_samples]
    filtered_q = np.convolve(mixed_q, float_coefficients, mode="full")[:input_samples]
    return (
        filtered_i[config.decimation - 1::config.decimation],
        filtered_q[config.decimation - 1::config.decimation],
    )


def _run_fixed(config: DspConfig, sine_lut: np.ndarray,
               fir_coefficients: np.ndarray,
               noise_q15: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    input_samples = config.output_samples * config.decimation
    frontend = _run_fixed_frontend(config, sine_lut, noise_q15)
    mixed_i = frontend.mixed_i
    mixed_q = frontend.mixed_q

    output_i = np.empty(config.output_samples, dtype=np.int16)
    output_q = np.empty(config.output_samples, dtype=np.int16)
    output_shift = MIXER_FRAC_BITS + COEFFICIENT_FRAC_BITS - OUTPUT_FRAC_BITS
    accumulator_limit = 1 << (FIR_ACCUMULATOR_BITS - 1)
    for output_index in range(config.output_samples):
        input_index = (output_index + 1) * config.decimation - 1
        valid_taps = min(FIR_TAPS, input_index + 1)
        coefficients = fir_coefficients[:valid_taps].astype(np.int64)
        samples_i = mixed_i[input_index - valid_taps + 1:input_index + 1][::-1]
        samples_q = mixed_q[input_index - valid_taps + 1:input_index + 1][::-1]
        accumulator_i = int(np.dot(samples_i.astype(np.int64), coefficients))
        accumulator_q = int(np.dot(samples_q.astype(np.int64), coefficients))
        if not (-accumulator_limit <= accumulator_i < accumulator_limit):
            raise OverflowError("I FIR accumulator exceeded 48 signed bits")
        if not (-accumulator_limit <= accumulator_q < accumulator_limit):
            raise OverflowError("Q FIR accumulator exceeded 48 signed bits")
        output_i[output_index] = saturate(
            round_shift(accumulator_i, output_shift), OUTPUT_BITS
        )
        output_q[output_index] = saturate(
            round_shift(accumulator_q, output_shift), OUTPUT_BITS
        )
    return output_i, output_q


def pack_iq_words(i_samples: np.ndarray, q_samples: np.ndarray) -> np.ndarray:
    """Pack signed I into bits 15:0 and signed Q into bits 31:16."""
    if i_samples.shape != q_samples.shape:
        raise ValueError("I and Q arrays must have equal shapes")
    i_words = i_samples.astype(np.int64) & 0xFFFF
    q_words = q_samples.astype(np.int64) & 0xFFFF
    return ((q_words << 16) | i_words).astype(np.uint32)


def run_model(config: DspConfig, *, sine_lut: np.ndarray | None = None,
              fir_coefficients: np.ndarray | None = None) -> DspResult:
    """Run both reference models from identical deterministic configuration."""
    if sine_lut is None or fir_coefficients is None:
        loaded_sine, loaded_fir = load_default_tables()
        sine_lut = loaded_sine if sine_lut is None else sine_lut
        fir_coefficients = loaded_fir if fir_coefficients is None else fir_coefficients
    float_coefficients = generate_float_fir_coefficients()
    noise = lfsr16_noise(config.output_samples * config.decimation, config.noise_seed)
    float_i, float_q = _run_float(config, float_coefficients, noise)
    fixed_i, fixed_q = _run_fixed(config, sine_lut, fir_coefficients, noise)
    return DspResult(
        float_i=float_i,
        float_q=float_q,
        fixed_i=fixed_i,
        fixed_q=fixed_q,
        packed_words=pack_iq_words(fixed_i, fixed_q),
        tlast=(
            np.arange(config.output_samples, dtype=np.uint32)
            % config.frame_length
            == config.frame_length - 1
        ),
        signal_phase_word=frequency_to_phase_word(config.signal_frequency_hz),
        lo_phase_word=frequency_to_phase_word(config.lo_frequency_hz),
    )


def result_metrics(result: DspResult) -> dict[str, float | int | str]:
    fixed_i = result.fixed_i.astype(np.float64) / (1 << OUTPUT_FRAC_BITS)
    fixed_q = result.fixed_q.astype(np.float64) / (1 << OUTPUT_FRAC_BITS)
    error = np.concatenate((fixed_i - result.float_i, fixed_q - result.float_q))
    return {
        "outputs": len(result.fixed_i),
        "frames": int(np.count_nonzero(result.tlast)),
        "signal_phase_word": result.signal_phase_word,
        "lo_phase_word": result.lo_phase_word,
        "max_abs_error": float(np.max(np.abs(error))),
        "rms_error": float(np.sqrt(np.mean(error * error))),
        "sha256": result.sha256,
    }


def _print_metrics(metrics: dict[str, float | int | str]) -> None:
    print(f"output samples    : {metrics['outputs']}")
    print(f"frames            : {metrics['frames']}")
    print(f"signal phase word : 0x{metrics['signal_phase_word']:08x}")
    print(f"LO phase word     : 0x{metrics['lo_phase_word']:08x}")
    print(f"maximum abs error : {metrics['max_abs_error']:.9e}")
    print(f"RMS error         : {metrics['rms_error']:.9e}")
    print(f"packed SHA-256    : {metrics['sha256']}")


def write_rtl_vectors(config: DspConfig, output_dir: Path,
                      sample_count: int) -> None:
    """Write deterministic readmemh vectors for DSP-1 RTL tests."""
    trace = run_fixed_frontend(config, sample_count)
    vectors = {
        "adc_q1_15.hex": render_hex(trace.adc, ADC_BITS),
        "lo_sin_q1_15.hex": render_hex(trace.lo_sin, ADC_BITS),
        "lo_cos_q1_15.hex": render_hex(trace.lo_cos, ADC_BITS),
        "mixed_i_q1_17.hex": render_hex(trace.mixed_i, MIXER_BITS),
        "mixed_q_q1_17.hex": render_hex(trace.mixed_q, MIXER_BITS),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name, contents in vectors.items():
        (output_dir / name).write_text(contents, encoding="ascii", newline="\n")
        hashes[name] = hashlib.sha256(contents.encode("ascii")).hexdigest()
    metadata = {
        "format": "qcrate-dsp-rtl-vectors-v1",
        "sample_count": sample_count,
        "adc_bits": ADC_BITS,
        "mixer_bits": MIXER_BITS,
        "signal_phase_word": trace.signal_phase_word,
        "signal_phase_initial": trace.signal_phase_initial,
        "lo_phase_word": trace.lo_phase_word,
        "lo_phase_initial": trace.lo_phase_initial,
        "vectors_sha256": hashes,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
        newline="\n",
    )
    print(f"wrote {sample_count} DSP-1 samples to {output_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="run and summarize a model")
    inspect_parser.add_argument("config", type=Path)
    generate_parser = subparsers.add_parser("generate", help="write a compressed vector")
    generate_parser.add_argument("config", type=Path)
    generate_parser.add_argument("output", type=Path)
    rtl_parser = subparsers.add_parser(
        "generate-rtl-vectors", help="write DSP-1 readmemh stage vectors"
    )
    rtl_parser.add_argument("config", type=Path)
    rtl_parser.add_argument("output", type=Path)
    rtl_parser.add_argument("--samples", type=int, default=1024)
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        if args.command == "generate-rtl-vectors":
            write_rtl_vectors(config, args.output, args.samples)
            return 0
        result = run_model(config)
        metrics = result_metrics(result)
        _print_metrics(metrics)
        if args.command == "generate":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                args.output,
                fixed_i=result.fixed_i,
                fixed_q=result.fixed_q,
                packed_words=result.packed_words,
                tlast=result.tlast,
                float_i=result.float_i,
                float_q=result.float_q,
                metadata=json.dumps(metrics, sort_keys=True),
            )
            print(f"wrote             : {args.output}")
    except (DspConfigurationError, OSError, OverflowError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
