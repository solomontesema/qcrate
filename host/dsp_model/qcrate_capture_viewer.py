#!/usr/bin/env python3
"""Visualize and audit a raw Q-Crate DSP DMA capture."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import qcrate_dsp as dsp
import qcrate_dsp_reference as deployed_reference


DEFAULT_CONFIG = Path(__file__).resolve().parent / "configs" / "tone_1mhz.json"


@dataclass(frozen=True)
class IqCapture:
    """Decoded packed Q1.15 samples from one DMA capture."""

    payload: bytes
    words: np.ndarray
    i_raw: np.ndarray
    q_raw: np.ndarray

    @property
    def i(self) -> np.ndarray:
        return self.i_raw.astype(np.float64) / (1 << dsp.OUTPUT_FRAC_BITS)

    @property
    def q(self) -> np.ndarray:
        return self.q_raw.astype(np.float64) / (1 << dsp.OUTPUT_FRAC_BITS)

    @property
    def complex_samples(self) -> np.ndarray:
        return self.i + 1j * self.q

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


def signed_16(values: np.ndarray) -> np.ndarray:
    """Convert unsigned low-16-bit values to native signed integers."""
    unsigned = np.asarray(values, dtype=np.uint32) & 0xFFFF
    signed = unsigned.astype(np.int32)
    signed[signed >= 0x8000] -= 0x1_0000
    return signed.astype(np.int16)


def decode_payload(payload: bytes) -> IqCapture:
    """Decode little-endian words packed as Q[31:16], I[15:0]."""
    if not payload:
        raise ValueError("capture is empty")
    if len(payload) % 4:
        raise ValueError(
            f"capture contains {len(payload)} bytes; packed IQ requires a multiple of 4"
        )
    words = np.frombuffer(payload, dtype="<u4").astype(np.uint32, copy=True)
    return IqCapture(
        payload=payload,
        words=words,
        i_raw=signed_16(words),
        q_raw=signed_16(words >> 16),
    )


def load_capture(path: Path) -> IqCapture:
    """Read and decode one raw capture file."""
    return decode_payload(path.read_bytes())


def dominant_frequency(samples: np.ndarray, sample_rate_hz: float) -> tuple[float, float]:
    """Return the strongest Hann-windowed complex FFT bin and its dBFS level."""
    values = np.asarray(samples, dtype=np.complex128)
    if values.ndim != 1 or len(values) < 8:
        raise ValueError("frequency analysis requires at least eight complex samples")
    window = np.hanning(len(values))
    spectrum = np.fft.fftshift(np.fft.fft(values * window))
    frequencies = np.fft.fftshift(np.fft.fftfreq(len(values), 1.0 / sample_rate_hz))
    magnitude = np.abs(spectrum) / np.sum(window)
    peak_index = int(np.argmax(magnitude))
    peak_dbfs = 20.0 * np.log10(max(float(magnitude[peak_index]), 1e-15))
    return float(frequencies[peak_index]), peak_dbfs


def spectrum_dbfs(
    samples: np.ndarray, sample_rate_hz: float
) -> tuple[np.ndarray, np.ndarray]:
    """Produce a centered Hann-windowed complex spectrum for plotting."""
    values = np.asarray(samples, dtype=np.complex128)
    window = np.hanning(len(values))
    spectrum = np.fft.fftshift(np.fft.fft(values * window))
    frequencies = np.fft.fftshift(np.fft.fftfreq(len(values), 1.0 / sample_rate_hz))
    magnitude = np.abs(spectrum) / np.sum(window)
    return frequencies, 20.0 * np.log10(np.maximum(magnitude, 1e-12))


def build_report(
    capture_path: Path, config_path: Path
) -> tuple[IqCapture, IqCapture, np.ndarray, dict[str, object]]:
    """Decode actual data and regenerate matching source/reference samples."""
    actual = load_capture(capture_path)
    config = dsp.load_config(config_path)
    expected_words = np.asarray(
        deployed_reference.generate_words(
            config_path, dsp.TABLE_DIR, len(actual.words)
        ),
        dtype=np.uint32,
    )
    expected = decode_payload(expected_words.astype("<u4").tobytes())
    modeled_input_count = min(len(actual.words) * config.decimation, 2048)
    modeled_input = dsp.run_fixed_frontend(config, modeled_input_count).adc
    mismatches = np.flatnonzero(actual.words != expected.words)
    output_rate_hz = config.sample_rate_hz / config.decimation
    peak_hz, peak_dbfs = dominant_frequency(actual.complex_samples, output_rate_hz)

    summary: dict[str, object] = {
        "format": "qcrate-dsp-capture-report-v1",
        "capture": str(capture_path),
        "configuration": str(config_path),
        "bytes": len(actual.payload),
        "words": len(actual.words),
        "input_sample_rate_hz": config.sample_rate_hz,
        "output_sample_rate_hz": output_rate_hz,
        "decimation": config.decimation,
        "first_word": f"0x{int(actual.words[0]):08x}",
        "last_word": f"0x{int(actual.words[-1]):08x}",
        "sha256": actual.sha256,
        "reference_mismatches": len(mismatches),
        "first_mismatch": int(mismatches[0]) if len(mismatches) else None,
        "dominant_frequency_hz": peak_hz,
        "dominant_level_dbfs": peak_dbfs,
        "peak_magnitude": float(np.max(np.abs(actual.complex_samples))),
        "rms_magnitude": float(
            np.sqrt(np.mean(np.abs(actual.complex_samples) ** 2))
        ),
    }
    return actual, expected, modeled_input, summary


def render_report(
    actual: IqCapture,
    expected: IqCapture,
    modeled_input: np.ndarray,
    summary: dict[str, object],
    output_path: Path,
    *,
    input_points: int,
    output_points: int,
    show: bool,
) -> None:
    """Render the modeled input and captured output as one review artifact."""
    import matplotlib

    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    input_count = min(input_points, len(modeled_input))
    output_count = min(output_points, len(actual.words))
    input_rate = float(summary["input_sample_rate_hz"])
    output_rate = float(summary["output_sample_rate_hz"])
    input_time_us = np.arange(input_count) / input_rate * 1e6
    output_time_us = np.arange(output_count) / output_rate * 1e6
    input_values = modeled_input[:input_count].astype(np.float64) / (1 << 15)
    frequencies, levels = spectrum_dbfs(actual.complex_samples, output_rate)
    mismatch_count = int(summary["reference_mismatches"])

    figure, axes = plt.subplots(2, 2, figsize=(13.5, 8.5), constrained_layout=True)
    figure.patch.set_facecolor("#f4f5f7")
    for axis in axes.flat:
        axis.set_facecolor("#ffffff")
        axis.grid(True, color="#d7dbe0", linewidth=0.7)

    input_axis = axes[0, 0]
    input_axis.plot(input_time_us, input_values, color="#175d8d", linewidth=1.2)
    input_axis.set_title("Modeled synthetic ADC input (not DMA-captured)")
    input_axis.set_xlabel("Time (us)")
    input_axis.set_ylabel("Q1.15 full scale")
    input_axis.set_ylim(-1.05, 1.05)

    output_axis = axes[0, 1]
    output_axis.plot(
        output_time_us, actual.i[:output_count], color="#087f5b", label="Captured I"
    )
    output_axis.plot(
        output_time_us, actual.q[:output_count], color="#c2410c", label="Captured Q"
    )
    if mismatch_count:
        output_axis.plot(
            output_time_us,
            expected.i[:output_count],
            color="#087f5b",
            linestyle="--",
            alpha=0.7,
            label="Expected I",
        )
        output_axis.plot(
            output_time_us,
            expected.q[:output_count],
            color="#c2410c",
            linestyle="--",
            alpha=0.7,
            label="Expected Q",
        )
    output_axis.set_title("Captured decimated complex output")
    output_axis.set_xlabel("Time (us)")
    output_axis.set_ylabel("Q1.15 full scale")
    output_axis.set_ylim(-1.05, 1.05)
    output_axis.legend(loc="upper right", ncols=2, fontsize=8)

    constellation_axis = axes[1, 0]
    color_index = np.arange(len(actual.words))
    constellation = constellation_axis.scatter(
        actual.i,
        actual.q,
        c=color_index,
        cmap="viridis",
        s=7,
        alpha=0.65,
        linewidths=0,
    )
    constellation_axis.set_title("Captured IQ constellation")
    constellation_axis.set_xlabel("I")
    constellation_axis.set_ylabel("Q")
    constellation_axis.set_xlim(-1.05, 1.05)
    constellation_axis.set_ylim(-1.05, 1.05)
    constellation_axis.set_aspect("equal", adjustable="box")
    figure.colorbar(constellation, ax=constellation_axis, label="Sample index")

    spectrum_axis = axes[1, 1]
    spectrum_axis.plot(frequencies / 1e6, levels, color="#6f42c1", linewidth=1.0)
    spectrum_axis.axvline(
        float(summary["dominant_frequency_hz"]) / 1e6,
        color="#111827",
        linestyle="--",
        linewidth=0.9,
    )
    spectrum_axis.set_title("Captured complex spectrum")
    spectrum_axis.set_xlabel("Frequency (MHz)")
    spectrum_axis.set_ylabel("Magnitude (dBFS)")
    spectrum_axis.set_xlim(-output_rate / 2e6, output_rate / 2e6)
    spectrum_axis.set_ylim(max(-140.0, float(np.max(levels)) - 100.0), 3.0)

    match_text = (
        "exact bit match" if mismatch_count == 0 else f"{mismatch_count} mismatches"
    )
    figure.suptitle(
        "Q-Crate KV260 DSP-2B DMA capture\n"
        f"{len(actual.words)} IQ words, {match_text}, "
        f"peak {float(summary['dominant_frequency_hz']) / 1e6:.6f} MHz",
        fontsize=15,
    )
    figure.text(
        0.5,
        0.002,
        f"SHA-256 {summary['sha256']}",
        ha="center",
        va="bottom",
        fontsize=8,
        color="#4b5563",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=160, facecolor=figure.get_facecolor())
    print(f"wrote plot         : {output_path}")
    if show:
        plt.show()
    plt.close(figure)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="raw little-endian DMA IQ file")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, help="PNG path (default: capture.png)")
    parser.add_argument("--summary", type=Path, help="JSON path (default: capture.json)")
    parser.add_argument("--input-points", type=int, default=256)
    parser.add_argument("--output-points", type=int, default=256)
    parser.add_argument("--show", action="store_true", help="open the completed plot")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.input_points < 8 or args.output_points < 8:
        raise SystemExit("error: input/output point counts must be at least 8")
    output_path = args.output or args.capture.with_suffix(".png")
    summary_path = args.summary or args.capture.with_suffix(".json")
    try:
        actual, expected, modeled_input, summary = build_report(
            args.capture, args.config
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"capture words      : {summary['words']}")
        print(f"reference mismatch : {summary['reference_mismatches']}")
        print(f"dominant frequency : {float(summary['dominant_frequency_hz']) / 1e6:.6f} MHz")
        print(f"capture SHA-256    : {summary['sha256']}")
        print(f"wrote summary      : {summary_path}")
        render_report(
            actual,
            expected,
            modeled_input,
            summary,
            output_path,
            input_points=args.input_points,
            output_points=args.output_points,
            show=args.show,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
