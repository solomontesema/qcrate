#!/usr/bin/env python3
"""Focused contract tests for the Q-Crate DSP-0 reference model."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


MODEL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODEL_DIR))

import qcrate_dsp as dsp  # noqa: E402


CONFIG = MODEL_DIR / "configs" / "tone_1mhz.json"


class FixedPointTests(unittest.TestCase):
    def test_round_shift_ties_away_from_zero(self) -> None:
        self.assertEqual(dsp.round_shift(1, 1), 1)
        self.assertEqual(dsp.round_shift(-1, 1), -1)
        self.assertEqual(dsp.round_shift(3, 1), 2)
        self.assertEqual(dsp.round_shift(-3, 1), -2)
        self.assertEqual(dsp.round_shift(2, 1), 1)
        self.assertEqual(dsp.round_shift(-2, 1), -1)

    def test_signed_saturation(self) -> None:
        self.assertEqual(dsp.saturate(40000, 16), 32767)
        self.assertEqual(dsp.saturate(-40000, 16), -32768)
        self.assertEqual(dsp.saturate(123, 16), 123)

    def test_iq_word_layout(self) -> None:
        packed = dsp.pack_iq_words(
            np.asarray([-2, 0x1234], dtype=np.int16),
            np.asarray([0x2345, -3], dtype=np.int16),
        )
        np.testing.assert_array_equal(packed, [0x2345FFFE, 0xFFFD1234])


class NcoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sine = dsp.generate_quarter_sine_lut()

    def test_tracked_lut_matches_generator(self) -> None:
        tracked = dsp.load_hex(
            dsp.SINE_TABLE, dsp.ADC_BITS, dsp.QUARTER_LUT_SIZE
        ).astype(np.int16)
        np.testing.assert_array_equal(tracked, self.sine)

    def test_quadrant_boundaries(self) -> None:
        self.assertEqual(dsp.nco_lookup(0, self.sine), 0)
        self.assertEqual(dsp.nco_lookup(dsp.QUARTER_PHASE, self.sine), 32767)
        self.assertEqual(dsp.nco_lookup(2 * dsp.QUARTER_PHASE, self.sine), 0)
        self.assertEqual(dsp.nco_lookup(3 * dsp.QUARTER_PHASE, self.sine), -32767)

    def test_tuning_words_are_frozen(self) -> None:
        self.assertEqual(dsp.frequency_to_phase_word(30_000_000), 0x26666666)
        self.assertEqual(dsp.frequency_to_phase_word(29_000_000), 0x251EB852)

    def test_noise_sequence_is_deterministic(self) -> None:
        expected = [25200, -3784, -18276, -25522, -29145, 13075, 28041, 17092]
        np.testing.assert_array_equal(dsp.lfsr16_noise(8, 0xACE1), expected)


class FirTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generated = dsp.quantize_fir_coefficients(
            dsp.generate_float_fir_coefficients()
        )
        cls.tracked = dsp.load_hex(
            dsp.FIR_TABLE, dsp.COEFFICIENT_BITS, dsp.FIR_TAPS
        )

    def test_tracked_coefficients_match_generator(self) -> None:
        np.testing.assert_array_equal(self.tracked, self.generated)
        np.testing.assert_array_equal(self.tracked, self.tracked[::-1])
        self.assertEqual(int(np.sum(self.tracked)), 1 << dsp.COEFFICIENT_FRAC_BITS)

    def test_quantized_filter_meets_spectral_contract(self) -> None:
        fft_length = 1 << 18
        coefficients = self.tracked.astype(np.float64) / (
            1 << dsp.COEFFICIENT_FRAC_BITS
        )
        response = np.fft.rfft(coefficients, fft_length)
        frequencies = np.fft.rfftfreq(fft_length, 1.0 / dsp.SAMPLE_RATE_HZ)
        response_db = 20.0 * np.log10(np.maximum(np.abs(response), 1e-300))
        passband = response_db[frequencies <= dsp.PASSBAND_HZ]
        stopband = response_db[frequencies >= dsp.STOPBAND_HZ]
        self.assertLessEqual(float(np.ptp(passband)), 0.1)
        self.assertLessEqual(float(np.max(stopband)), -70.0)


class ConfigurationTests(unittest.TestCase):
    def test_reference_configuration(self) -> None:
        config = dsp.load_config(CONFIG)
        self.assertEqual(config.output_samples, 4096)
        self.assertEqual(config.frame_length, 1024)
        self.assertEqual(config.frame_count, 4)

    def test_duplicate_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(
                '{"format":"qcrate-dsp-v1","format":"bad"}\n',
                encoding="utf-8",
            )
            with self.assertRaises(dsp.DspConfigurationError):
                dsp.load_config(path)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = dsp.load_config(CONFIG)
        cls.result = dsp.run_model(cls.config)

    def test_reference_vector_is_exact_and_framed(self) -> None:
        self.assertEqual(
            self.result.sha256,
            "e645b9eeb7506178ac0de33d08a2d8d21b90f80289859c557be30e3b09583a4b",
        )
        np.testing.assert_array_equal(
            np.flatnonzero(self.result.tlast), [1023, 2047, 3071, 4095]
        )
        self.assertFalse(
            np.array_equal(
                self.result.packed_words[:1024],
                self.result.packed_words[1024:2048],
            )
        )

    def test_fixed_model_tracks_floating_reference(self) -> None:
        metrics = dsp.result_metrics(self.result)
        self.assertLessEqual(metrics["max_abs_error"], 1.2e-4)
        self.assertLessEqual(metrics["rms_error"], 6.0e-5)

    def test_output_contains_expected_complex_tone(self) -> None:
        settled = 32
        complex_iq = (
            self.result.fixed_i[settled:].astype(np.float64)
            + 1j * self.result.fixed_q[settled:].astype(np.float64)
        )
        spectrum = np.fft.fft(complex_iq * np.hanning(len(complex_iq)))
        frequencies = np.fft.fftfreq(
            len(complex_iq),
            1.0 / (dsp.SAMPLE_RATE_HZ / dsp.DECIMATION),
        )
        peak_hz = float(frequencies[int(np.argmax(np.abs(spectrum)))])
        bin_width_hz = dsp.SAMPLE_RATE_HZ / dsp.DECIMATION / len(complex_iq)
        self.assertLessEqual(abs(peak_hz - 1_000_000), bin_width_hz)


if __name__ == "__main__":
    unittest.main()
