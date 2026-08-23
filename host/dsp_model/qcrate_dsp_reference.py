#!/usr/bin/env python3
"""Standard-library bit-exact reference for the deployed DSP-2B stream."""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


PHASE_BITS = 32
PHASE_MODULUS = 1 << PHASE_BITS
QUARTER_PHASE = 1 << (PHASE_BITS - 2)
LUT_ADDR_BITS = 12
QUARTER_LUT_BITS = LUT_ADDR_BITS - 2
DECIMATION = 16
FIR_TAPS = 217


def saturate(value: int, bits: int) -> int:
    return min(max(int(value), -(1 << (bits - 1))), (1 << (bits - 1)) - 1)


def round_shift(value: int, shift: int) -> int:
    magnitude = abs(int(value))
    rounded = (magnitude + (1 << (shift - 1))) >> shift
    return rounded if value >= 0 else -rounded


def frequency_to_phase_word(frequency_hz: Decimal, sample_rate_hz: int) -> int:
    scaled = frequency_hz * PHASE_MODULUS / sample_rate_hz
    return int(scaled.to_integral_value(rounding=ROUND_HALF_UP)) & 0xFFFF_FFFF


def decimal_to_q1_15(value: Decimal) -> int:
    scaled = value * (1 << 15)
    return saturate(int(scaled.to_integral_value(rounding=ROUND_HALF_UP)), 16)


def load_signed_hex(path: Path, bits: int, expected_count: int) -> list[int]:
    mask = (1 << bits) - 1
    sign = 1 << (bits - 1)
    values = [int(line, 16) & mask for line in path.read_text().splitlines() if line]
    if len(values) != expected_count:
        raise ValueError(
            f"{path} contains {len(values)} values, expected {expected_count}"
        )
    return [value - (1 << bits) if value & sign else value for value in values]


def nco_lookup(phase_word: int, quarter_lut: list[int]) -> int:
    full_index = (phase_word & 0xFFFF_FFFF) >> (PHASE_BITS - LUT_ADDR_BITS)
    quadrant = full_index >> QUARTER_LUT_BITS
    offset = full_index & ((1 << QUARTER_LUT_BITS) - 1)
    if quadrant == 0:
        return quarter_lut[offset]
    if quadrant == 1:
        return quarter_lut[(1 << QUARTER_LUT_BITS) - offset]
    if quadrant == 2:
        return -quarter_lut[offset]
    return -quarter_lut[(1 << QUARTER_LUT_BITS) - offset]


def lfsr_step(state: int) -> int:
    shifted = state >> 1
    if state & 1:
        shifted ^= 0xB400
    return shifted


def generate_words(config_path: Path, table_dir: Path, output_count: int) -> list[int]:
    """Generate packed Q[31:16]/I[15:0] words for the fixed DSP-2B source."""
    if output_count < 1:
        raise ValueError("output_count must be positive")
    config = json.loads(config_path.read_text(), parse_float=Decimal)
    if config["sample_rate_hz"] != 200_000_000 or config["decimation"] != 16:
        raise ValueError("DSP-2B requires the frozen 200 MHz, decimate-by-16 contract")

    sine_lut = load_signed_hex(
        table_dir / "sine_quarter_q1_15.mem", 16, 1025
    )
    coefficients = load_signed_hex(
        table_dir / "fir_decim16_q1_17.hex", 18, FIR_TAPS
    )
    signal_increment = frequency_to_phase_word(
        Decimal(config["signal_frequency_hz"]), config["sample_rate_hz"]
    )
    lo_increment = frequency_to_phase_word(
        Decimal(config["lo_frequency_hz"]), config["sample_rate_hz"]
    )
    signal_phase = int(
        (Decimal(config["signal_phase_turns"]) * PHASE_MODULUS)
        .to_integral_value(rounding=ROUND_HALF_UP)
    ) & 0xFFFF_FFFF
    lo_phase = int(
        (Decimal(config["lo_phase_turns"]) * PHASE_MODULUS)
        .to_integral_value(rounding=ROUND_HALF_UP)
    ) & 0xFFFF_FFFF
    signal_amplitude = decimal_to_q1_15(Decimal(config["signal_amplitude"]))
    noise_amplitude = decimal_to_q1_15(Decimal(config["noise_amplitude"]))
    lfsr = int(config["noise_seed"])

    mixed_i: list[int] = []
    mixed_q: list[int] = []
    output_words: list[int] = []
    for input_index in range(output_count * DECIMATION):
        lfsr = lfsr_step(lfsr)
        noise_q1_15 = lfsr - 0x8000
        source_cosine = nco_lookup(signal_phase + QUARTER_PHASE, sine_lut)
        lo_cosine = nco_lookup(lo_phase + QUARTER_PHASE, sine_lut)
        lo_sine = nco_lookup(lo_phase, sine_lut)

        source = saturate(round_shift(source_cosine * signal_amplitude, 15), 16)
        noise = saturate(round_shift(noise_q1_15 * noise_amplitude, 15), 16)
        adc = saturate(source + noise, 16)
        mixed_i.append(saturate(round_shift(adc * lo_cosine, 13), 18))
        mixed_q.append(saturate(round_shift(-adc * lo_sine, 13), 18))
        signal_phase = (signal_phase + signal_increment) & 0xFFFF_FFFF
        lo_phase = (lo_phase + lo_increment) & 0xFFFF_FFFF

        if (input_index % DECIMATION == DECIMATION - 1):
            valid_taps = min(FIR_TAPS, input_index + 1)
            sum_i = sum(
                mixed_i[input_index - tap] * coefficients[tap]
                for tap in range(valid_taps)
            )
            sum_q = sum(
                mixed_q[input_index - tap] * coefficients[tap]
                for tap in range(valid_taps)
            )
            output_i = saturate(round_shift(sum_i, 19), 16)
            output_q = saturate(round_shift(sum_q, 19), 16)
            output_words.append(((output_q & 0xFFFF) << 16) | (output_i & 0xFFFF))

    return output_words
