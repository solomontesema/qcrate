#!/usr/bin/env python3
"""Interactive and headless analyzer for Q-Crate repeated IQ runs."""
from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import sys
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DSP_DIR = ROOT / "host" / "dsp_model"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(DSP_DIR))

import qcrate_capture_viewer as capture_viewer  # noqa: E402
import qcrate_dsp as dsp  # noqa: E402
import qcrate_dsp_reference as deployed_reference  # noqa: E402
from qcrate_run import RunBundle, RunHealth, RunIndex, ShotRecord  # noqa: E402


DEFAULT_SAMPLE_RATE_HZ = 12_500_000.0
DEFAULT_CONFIG = DSP_DIR / "configs" / "tone_1mhz.json"
TABLE_MANIFEST = ROOT / "rtl" / "dsp" / "tables" / "manifest.json"
SHOT_WINDOW_SIZE = 512
SELECTION_DEBOUNCE_MS = 90


@dataclass(frozen=True)
class ShotAnalysis:
    shot: ShotRecord
    capture: capture_viewer.IqCapture
    sample_rate_hz: float
    sample_rate_source: str
    spectrum_frequency_hz: np.ndarray
    spectrum_dbfs: np.ndarray
    dominant_frequency_hz: float
    dominant_level_dbfs: float
    peak_magnitude: float
    rms_magnitude: float
    file_crc32: int
    reference_mismatches: int | None


@lru_cache(maxsize=8)
def current_config_id(config: Path = DEFAULT_CONFIG) -> int:
    digest = hashlib.sha256(config.read_bytes() + TABLE_MANIFEST.read_bytes()).digest()
    return int.from_bytes(digest[:8], "big")


@lru_cache(maxsize=8)
def expected_words(config_text: str, count: int) -> np.ndarray:
    return np.asarray(
        deployed_reference.generate_words(Path(config_text), dsp.TABLE_DIR, count),
        dtype=np.uint32,
    )


def analyze_shot(
    bundle: RunBundle | RunIndex,
    shot: ShotRecord,
    *,
    fallback_sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
    config: Path = DEFAULT_CONFIG,
) -> ShotAnalysis:
    if shot.payload_format != 2 or shot.sample_bytes_per_word != 4:
        raise ValueError(
            f"shot {shot.shot_id} is not an IQ_S16_LE four-byte stream"
        )
    payload = bundle.read_samples(shot)
    capture = capture_viewer.decode_payload(payload)
    expected_count = shot.frame_count * shot.frame_samples
    if len(capture.words) != expected_count:
        raise ValueError(
            f"shot {shot.shot_id} contains {len(capture.words)} samples; "
            f"QIDX describes {expected_count}"
        )
    sample_rate_hz, sample_rate_source = bundle.sample_rate_hz(
        fallback_sample_rate_hz
    )
    frequency, levels = capture_viewer.spectrum_dbfs(
        capture.complex_samples, sample_rate_hz
    )
    peak_index = int(np.argmax(levels))
    magnitude = np.abs(capture.complex_samples)
    mismatches = None
    if shot.config_id == current_config_id(config):
        expected = expected_words(str(config.resolve()), len(capture.words))
        mismatches = int(np.count_nonzero(capture.words != expected))
    return ShotAnalysis(
        shot=shot,
        capture=capture,
        sample_rate_hz=sample_rate_hz,
        sample_rate_source=sample_rate_source,
        spectrum_frequency_hz=frequency,
        spectrum_dbfs=levels,
        dominant_frequency_hz=float(frequency[peak_index]),
        dominant_level_dbfs=float(levels[peak_index]),
        peak_magnitude=float(np.max(magnitude)),
        rms_magnitude=float(np.sqrt(np.mean(magnitude**2))),
        file_crc32=binascii.crc32(payload) & 0xFFFFFFFF,
        reference_mismatches=mismatches,
    )


def render_analysis(figure: object, analysis: ShotAnalysis) -> None:
    """Render one complete shot into a reusable Matplotlib figure."""
    figure.clear()
    axes = figure.subplots(2, 2)
    figure.subplots_adjust(left=0.075, right=0.97, bottom=0.08, top=0.89,
                           hspace=0.34, wspace=0.25)
    for axis in axes.flat:
        axis.set_facecolor("#ffffff")
        axis.grid(True, color="#d7dce2", linewidth=0.7)

    capture = analysis.capture
    time_count = min(len(capture.words), 256)
    time_us = np.arange(time_count) / analysis.sample_rate_hz * 1e6
    time_axis = axes[0, 0]
    time_axis.plot(time_us, capture.i[:time_count], color="#087f5b",
                   linewidth=1.0, label="I")
    time_axis.plot(time_us, capture.q[:time_count], color="#c2410c",
                   linewidth=1.0, label="Q")
    time_axis.set_title("Complex waveform")
    time_axis.set_xlabel("Time (us)")
    time_axis.set_ylabel("Q1.15 full scale")
    time_axis.set_ylim(-1.05, 1.05)
    time_axis.legend(loc="upper right", ncols=2, fontsize=8)

    magnitude_axis = axes[0, 1]
    magnitude = np.abs(capture.complex_samples[:time_count])
    phase = np.unwrap(np.angle(capture.complex_samples[:time_count]))
    magnitude_axis.plot(time_us, magnitude, color="#175d8d", linewidth=1.0)
    magnitude_axis.set_title("Magnitude and unwrapped phase")
    magnitude_axis.set_xlabel("Time (us)")
    magnitude_axis.set_ylabel("Magnitude", color="#175d8d")
    phase_axis = magnitude_axis.twinx()
    phase_axis.plot(time_us, phase, color="#8b5cf6", linewidth=0.8, alpha=0.75)
    phase_axis.set_ylabel("Phase (rad)", color="#6d28d9")

    constellation_axis = axes[1, 0]
    stride = max(1, len(capture.words) // 8000)
    indices = np.arange(0, len(capture.words), stride)
    points = constellation_axis.scatter(
        capture.i[indices], capture.q[indices], c=indices, cmap="viridis",
        s=8, alpha=0.68, linewidths=0,
    )
    constellation_axis.set_title("IQ constellation")
    constellation_axis.set_xlabel("I")
    constellation_axis.set_ylabel("Q")
    constellation_axis.set_xlim(-1.05, 1.05)
    constellation_axis.set_ylim(-1.05, 1.05)
    constellation_axis.set_aspect("equal", adjustable="box")
    figure.colorbar(points, ax=constellation_axis, label="Sample index", pad=0.02)

    spectrum_axis = axes[1, 1]
    spectrum_axis.plot(
        analysis.spectrum_frequency_hz / 1e6,
        analysis.spectrum_dbfs,
        color="#a21caf",
        linewidth=0.95,
    )
    spectrum_axis.axvline(
        analysis.dominant_frequency_hz / 1e6,
        color="#111827",
        linestyle="--",
        linewidth=0.9,
    )
    spectrum_axis.set_title("Complex spectrum")
    spectrum_axis.set_xlabel("Frequency (MHz)")
    spectrum_axis.set_ylabel("Magnitude (dBFS)")
    spectrum_axis.set_xlim(-analysis.sample_rate_hz / 2e6,
                           analysis.sample_rate_hz / 2e6)
    spectrum_axis.set_ylim(
        max(-140.0, float(np.max(analysis.spectrum_dbfs)) - 100.0), 3.0
    )

    reference = (
        "reference unavailable"
        if analysis.reference_mismatches is None
        else f"reference mismatches {analysis.reference_mismatches}"
    )
    figure.suptitle(
        f"Q-Crate shot {analysis.shot.shot_id} | "
        f"{len(capture.words):,} IQ samples | "
        f"peak {analysis.dominant_frequency_hz / 1e6:.6f} MHz | {reference}",
        fontsize=13,
        color="#111827",
    )


def render_incomplete(figure: object, shot: ShotRecord) -> None:
    figure.clear()
    axis = figure.subplots(1, 1)
    axis.set_axis_off()
    issues = ", ".join(shot.issue_names) or "unspecified integrity failure"
    axis.text(
        0.5, 0.58, f"Shot {shot.shot_id} was not published", ha="center",
        va="center", fontsize=18, color="#991b1b", transform=axis.transAxes,
    )
    axis.text(
        0.5, 0.47, f"Integrity issues: {issues}", ha="center", va="center",
        fontsize=11, color="#374151", transform=axis.transAxes,
    )
    axis.text(
        0.5, 0.39, "No sample bytes are exposed for an incomplete shot.",
        ha="center", va="center", fontsize=10, color="#6b7280",
        transform=axis.transAxes,
    )


def analysis_metadata(analysis: ShotAnalysis) -> list[tuple[str, str]]:
    shot = analysis.shot
    reference = (
        "not available for config"
        if analysis.reference_mismatches is None
        else str(analysis.reference_mismatches)
    )
    return [
        ("Shot ID", str(shot.shot_id)),
        ("State", shot.state.name.lower()),
        ("Frames / samples", f"{shot.frame_count} / {len(analysis.capture.words):,}"),
        ("First timestamp", f"{shot.first_sample_timestamp:,} ticks"),
        ("Sample rate", f"{analysis.sample_rate_hz / 1e6:.6f} MS/s ({analysis.sample_rate_source})"),
        ("Dominant tone", f"{analysis.dominant_frequency_hz / 1e6:.6f} MHz"),
        ("Dominant level", f"{analysis.dominant_level_dbfs:.2f} dBFS"),
        ("Peak / RMS", f"{analysis.peak_magnitude:.6f} / {analysis.rms_magnitude:.6f}"),
        ("Payload CRC", f"0x{analysis.file_crc32:08x} ({'match' if analysis.file_crc32 == shot.payload_crc32 else 'MISMATCH'})"),
        ("Reference mismatches", reference),
        ("Packet interval", f"{shot.first_packet_sequence}..{shot.last_packet_sequence}"),
        ("Duplicates / reorder", f"{shot.duplicate_packets} / {shot.reordered_packets}"),
        ("Configuration ID", f"0x{shot.config_id:016x}"),
    ]


def health_rows(health: RunHealth) -> list[tuple[str, str]]:
    udp_rate = (
        "pending" if health.udp_payload_mbps is None
        else f"{health.udp_payload_mbps:.3f} Mb/s"
    )
    queue_health = (
        "pending sender report"
        if health.token_queue_high_water is None
        else f"token {health.token_queue_high_water}, DMA {health.dma_ready_high_water} banks"
    )
    dma_health = (
        "pending sender report"
        if health.dma_stall_cycles is None
        else (
            f"stall {health.dma_stall_cycles}, backpressure {health.starvation_events}, "
            f"missed/skipped {health.missed_triggers}/{health.skipped_triggers}"
        )
    )
    cpu = (
        "pending"
        if health.recorder_cpu_percent is None
        else f"host {health.recorder_cpu_percent:.2f}%"
    )
    if health.sender_cpu_percent is not None:
        cpu += f", KV260 {health.sender_cpu_percent:.2f}%"
    integrity_events = (
        health.incomplete_shots + health.missing_packets
        + health.malformed_packets + health.conflicting_packets
        + health.kernel_receive_drops + health.continuity_errors
    )
    return [
        ("Elapsed / rate", f"{health.duration_seconds:.1f} s / {health.shot_rate_hz:.3f} shot/s"),
        ("Sample / UDP", f"{health.sample_payload_mbps:.3f} / {udp_rate}"),
        ("Datagrams", f"{health.datagrams:,}"),
        ("Dup / reorder", f"{health.duplicate_packets} / {health.reordered_packets}"),
        ("Late / foreign", f"{health.late_packets} / {health.foreign_packets}"),
        ("Loss / skipped IDs", f"{integrity_events} / {health.skipped_shot_ids}"),
        ("Queue high water", queue_health),
        ("DMA / backpressure", dma_health),
        ("Process CPU", cpu),
    ]


class AnalyzerApplication:
    def __init__(self, initial_run: Path | None, fallback_rate: float,
                 session_log: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import ttk
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        from matplotlib.figure import Figure

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("Q-Crate Analyzer")
        self.root.geometry("1460x900")
        self.root.minsize(1080, 680)
        self.bundle: RunIndex | None = None
        self.selected_shot_id: int | None = None
        self.selected_record_index: int | None = None
        self.window_start = 0
        self.window_stop = 0
        self.render_job: str | None = None
        self.fallback_rate = fallback_rate
        self.run_path = tk.StringVar(value="No run open")
        self.run_summary = tk.StringVar(value="Select a Q-Crate run directory")
        self.status = tk.StringVar(value="Ready")
        self.follow = tk.BooleanVar(value=True)
        self.instrument_state = tk.StringVar(value="NO RUN")
        self.shot_id_entry = tk.StringVar()
        self.shot_window_label = tk.StringVar(value="Records 0 of 0")
        self.session_log = session_log
        self.session_id = uuid.uuid4().hex
        self.logged_run: tuple[Path, int] | None = None

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=25)
        style.configure("Header.TLabel", font=("TkDefaultFont", 11, "bold"))
        style.configure("Health.Accepted.TLabel", foreground="#087f5b",
                        font=("TkDefaultFont", 11, "bold"))
        style.configure("Health.Failed.TLabel", foreground="#991b1b",
                        font=("TkDefaultFont", 11, "bold"))
        style.configure("Health.Recording.TLabel", foreground="#175d8d",
                        font=("TkDefaultFont", 11, "bold"))

        toolbar = ttk.Frame(self.root, padding=(10, 8))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Open run", command=self.choose_run).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Previous", command=lambda: self.move_selection(-1)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(toolbar, text="Next", command=lambda: self.move_selection(1)).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text="Save PNG", command=self.save_png).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(toolbar, text="Auto refresh", variable=self.follow).pack(side=tk.RIGHT)
        ttk.Label(toolbar, textvariable=self.run_path).pack(side=tk.LEFT, padx=14, fill=tk.X, expand=True)

        body = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)
        sidebar = ttk.Frame(body, padding=(10, 6))
        plot_area = ttk.Frame(body, padding=(4, 4))
        body.add(sidebar, weight=0)
        body.add(plot_area, weight=1)

        ttk.Label(sidebar, text="Run", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(sidebar, textvariable=self.run_summary, justify=tk.LEFT,
                  wraplength=330).pack(anchor=tk.W, fill=tk.X, pady=(3, 10))
        health_header = ttk.Frame(sidebar)
        health_header.pack(fill=tk.X)
        ttk.Label(health_header, text="Instrument health",
                  style="Header.TLabel").pack(side=tk.LEFT)
        self.health_state_label = ttk.Label(
            health_header, textvariable=self.instrument_state,
            style="Health.Recording.TLabel",
        )
        self.health_state_label.pack(side=tk.RIGHT)
        self.health_tree = ttk.Treeview(
            sidebar, columns=("value",), show="tree headings", height=9,
            selectmode="none",
        )
        self.health_tree.heading("#0", text="Metric")
        self.health_tree.heading("value", text="Value")
        self.health_tree.column("#0", width=130, stretch=False)
        self.health_tree.column("value", width=207, stretch=True)
        self.health_tree.pack(fill=tk.X, pady=(4, 10))
        shot_header = ttk.Frame(sidebar)
        shot_header.pack(fill=tk.X)
        ttk.Label(shot_header, text="Shots", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Label(shot_header, textvariable=self.shot_window_label).pack(side=tk.RIGHT)
        shot_frame = ttk.Frame(sidebar)
        shot_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 10))
        self.shot_tree = ttk.Treeview(
            shot_frame, columns=("state", "timestamp", "frames", "bytes"),
            show="tree headings", selectmode="browse", height=11,
        )
        self.shot_tree.heading("#0", text="Shot")
        self.shot_tree.heading("state", text="State")
        self.shot_tree.heading("timestamp", text="Timestamp")
        self.shot_tree.heading("frames", text="Frames")
        self.shot_tree.heading("bytes", text="Bytes")
        self.shot_tree.column("#0", width=64, anchor=tk.E, stretch=False)
        self.shot_tree.column("state", width=82, stretch=False)
        self.shot_tree.column("timestamp", width=110, anchor=tk.E, stretch=False)
        self.shot_tree.column("frames", width=55, anchor=tk.E, stretch=False)
        self.shot_tree.column("bytes", width=75, anchor=tk.E, stretch=False)
        scroll = ttk.Scrollbar(shot_frame, orient=tk.VERTICAL,
                               command=self.shot_tree.yview)
        self.shot_tree.configure(yscrollcommand=scroll.set)
        self.shot_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.shot_tree.tag_configure("incomplete", foreground="#991b1b")
        self.shot_tree.bind("<<TreeviewSelect>>", self.on_select)
        jump_frame = ttk.Frame(sidebar)
        jump_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(jump_frame, text="Shot ID").pack(side=tk.LEFT)
        shot_entry = ttk.Entry(
            jump_frame, textvariable=self.shot_id_entry, width=16
        )
        shot_entry.pack(side=tk.LEFT, padx=(6, 4), fill=tk.X, expand=True)
        shot_entry.bind("<Return>", lambda _event: self.jump_to_shot())
        ttk.Button(jump_frame, text="Go", command=self.jump_to_shot).pack(side=tk.RIGHT)

        ttk.Label(sidebar, text="Measurement", style="Header.TLabel").pack(anchor=tk.W)
        self.metadata = ttk.Treeview(
            sidebar, columns=("value",), show="tree headings", height=10,
            selectmode="none",
        )
        self.metadata.heading("#0", text="Field")
        self.metadata.heading("value", text="Value")
        self.metadata.column("#0", width=142, stretch=False)
        self.metadata.column("value", width=195, stretch=True)
        self.metadata.pack(fill=tk.X, pady=(4, 0))

        self.figure = Figure(figsize=(10, 7), dpi=100, facecolor="#f4f5f7")
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_area)
        navigation = NavigationToolbar2Tk(self.canvas, plot_area, pack_toolbar=False)
        navigation.update()
        navigation.pack(fill=tk.X)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        status_bar = ttk.Label(self.root, textvariable=self.status, relief=tk.SUNKEN,
                               anchor=tk.W, padding=(8, 4))
        status_bar.pack(fill=tk.X)
        self.root.bind("<Control-o>", lambda _event: self.choose_run())
        self.root.bind("<Control-s>", lambda _event: self.save_png())
        self.root.bind("<Left>", lambda event: self.on_navigation_key(event, -1))
        self.root.bind("<Right>", lambda event: self.on_navigation_key(event, 1))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.log_session("start")
        self.root.after(1000, self.periodic_refresh)
        if initial_run is not None:
            self.load_run(initial_run)
        else:
            self.render_welcome()

    def render_welcome(self) -> None:
        self.figure.clear()
        axis = self.figure.subplots(1, 1)
        axis.set_axis_off()
        axis.text(0.5, 0.56, "Q-Crate Networked Pulsed-IQ Analyzer",
                  ha="center", fontsize=20, color="#111827", transform=axis.transAxes)
        axis.text(0.5, 0.46, "Open a recorded run to inspect verified shots.",
                  ha="center", fontsize=11, color="#4b5563", transform=axis.transAxes)
        self.canvas.draw_idle()

    def render_waiting(self) -> None:
        self.figure.clear()
        axis = self.figure.subplots(1, 1)
        axis.set_axis_off()
        axis.text(0.5, 0.54, "Waiting for first measurement", ha="center",
                  fontsize=18, color="#175d8d", transform=axis.transAxes)
        axis.text(0.5, 0.46, "Recorder is ready", ha="center", fontsize=10,
                  color="#4b5563", transform=axis.transAxes)
        self.canvas.draw_idle()

    def choose_run(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askdirectory(title="Open Q-Crate run")
        if selected:
            self.load_run(Path(selected))

    def log_session(
        self, event: str, bundle: RunBundle | RunIndex | None = None
    ) -> None:
        if self.session_log is None:
            return
        record: dict[str, object] = {
            "format": "qcrate-analyzer-session-v1",
            "session_id": self.session_id,
            "event": event,
            "unix_time_ns": time.time_ns(),
        }
        if bundle is not None:
            record["run_id"] = f"0x{bundle.run_id:016x}"
            record["run_path"] = str(bundle.path)
        self.session_log.parent.mkdir(parents=True, exist_ok=True)
        with self.session_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()

    def close(self) -> None:
        self.log_session("close", self.bundle)
        if self.render_job is not None:
            self.root.after_cancel(self.render_job)
        if self.bundle is not None:
            self.bundle.close()
        self.root.destroy()

    def update_health(self, health: RunHealth) -> None:
        self.health_tree.delete(*self.health_tree.get_children())
        for name, value in health_rows(health):
            self.health_tree.insert("", "end", text=name, values=(value,))
        self.instrument_state.set(health.state.upper())
        style = {
            "accepted": "Health.Accepted.TLabel",
            "failed": "Health.Failed.TLabel",
            "recording": "Health.Recording.TLabel",
        }[health.state]
        self.health_state_label.configure(style=style)

    def load_run(self, path: Path, *, preserve: bool = False) -> None:
        from tkinter import messagebox

        resolved = path.resolve()
        same_run = bool(
            preserve and self.bundle is not None and self.bundle.path == resolved
        )
        old_count = self.bundle.record_count if same_run and self.bundle else 0
        previous_index = self.selected_record_index if same_run else None
        follow_tail = bool(
            previous_index is not None and old_count and previous_index == old_count - 1
        )
        try:
            if same_run:
                assert self.bundle is not None
                changed = self.bundle.refresh()
                bundle = self.bundle
            else:
                bundle = RunIndex.open(resolved)
                changed = True
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            if not preserve:
                messagebox.showerror("Cannot open run", str(exc))
            self.status.set(f"Run refresh failed: {exc}")
            return
        if not same_run:
            if self.bundle is not None:
                self.bundle.close()
            self.selected_record_index = None
            self.selected_shot_id = None
            self.window_start = self.window_stop = 0
            self.shot_tree.delete(*self.shot_tree.get_children())
            self.shot_window_label.set("Records 0 of 0")
        self.bundle = bundle
        self.run_path.set(str(bundle.path))
        health = bundle.health()
        state = health.state
        self.run_summary.set(
            f"Run 0x{bundle.run_id:016x}\nStream 0x{bundle.stream_id:08x}\n"
            f"State: {state}\nComplete: {bundle.complete_count:,}   "
            f"Incomplete: {bundle.incomplete_count:,}"
        )
        self.update_health(health)
        run_identity = (bundle.path, bundle.run_id)
        if bundle.run_id and self.logged_run != run_identity:
            self.log_session("open_run", bundle)
            self.logged_run = run_identity
        if not changed:
            return
        if bundle.record_count:
            selected = (
                bundle.record_count - 1 if follow_tail
                else previous_index
                if previous_index is not None and previous_index < bundle.record_count
                else bundle.record_count - 1
            )
            window_changed = not (
                self.window_start <= selected < self.window_stop
            )
            if follow_tail or not same_run or window_changed:
                self.populate_shot_window(selected)
            else:
                self.shot_window_label.set(
                    f"Records {self.window_start + 1:,}-{self.window_stop:,} "
                    f"of {bundle.record_count:,}"
                )
        else:
            if bundle.run_id == 0 and bundle.manifest is None:
                self.run_summary.set(
                    "Run awaiting first datagram\nState: recording\n"
                    "Complete: 0   Incomplete: 0"
                )
                self.render_waiting()
                self.status.set("Recorder is ready; waiting for first measurement")
            else:
                self.status.set("Run contains no committed shot records")

    def populate_shot_window(self, selected: int) -> None:
        if self.bundle is None or not self.bundle.record_count:
            self.shot_tree.delete(*self.shot_tree.get_children())
            self.window_start = self.window_stop = 0
            self.shot_window_label.set("Records 0 of 0")
            return
        count = self.bundle.record_count
        start = max(0, min(selected - SHOT_WINDOW_SIZE // 2,
                           count - SHOT_WINDOW_SIZE))
        stop = min(count, start + SHOT_WINDOW_SIZE)
        records = self.bundle.records(start, stop)
        self.shot_tree.delete(*self.shot_tree.get_children())
        for ordinal, shot in enumerate(records, start):
            self.shot_tree.insert(
                "", "end", iid=f"r{ordinal}", text=str(shot.shot_id),
                values=(shot.state.name.lower(), f"{shot.first_sample_timestamp:,}",
                        shot.frame_count, f"{shot.sample_bytes:,}"),
                tags=(() if shot.complete else ("incomplete",)),
            )
        self.window_start = start
        self.window_stop = stop
        self.shot_window_label.set(
            f"Records {start + 1:,}-{stop:,} of {count:,}"
        )
        self.select_record(selected)

    def select_record(self, ordinal: int) -> None:
        if self.bundle is None or not (0 <= ordinal < self.bundle.record_count):
            return
        if not (self.window_start <= ordinal < self.window_stop):
            self.populate_shot_window(ordinal)
            return
        iid = f"r{ordinal}"
        if self.shot_tree.selection() != (iid,):
            self.shot_tree.selection_set(iid)
        self.shot_tree.see(iid)
        shot = self.bundle.record_at(ordinal)
        self.selected_record_index = ordinal
        self.selected_shot_id = shot.shot_id
        self.shot_id_entry.set(str(shot.shot_id))
        self.schedule_display(ordinal)

    def on_select(self, _event: object) -> None:
        selection = self.shot_tree.selection()
        if selection:
            self.select_record(int(selection[0][1:]))

    def schedule_display(self, ordinal: int) -> None:
        if self.render_job is not None:
            self.root.after_cancel(self.render_job)
        self.render_job = self.root.after(
            SELECTION_DEBOUNCE_MS, lambda: self.display_record(ordinal)
        )

    def display_record(self, ordinal: int) -> None:
        self.render_job = None
        if self.bundle is None:
            return
        if ordinal != self.selected_record_index:
            return
        shot = self.bundle.record_at(ordinal)
        shot_id = shot.shot_id
        self.selected_shot_id = shot_id
        self.metadata.delete(*self.metadata.get_children())
        try:
            if shot.complete:
                analysis = analyze_shot(
                    self.bundle, shot, fallback_sample_rate_hz=self.fallback_rate
                )
                render_analysis(self.figure, analysis)
                rows = analysis_metadata(analysis)
                self.status.set(
                    f"Shot {shot_id}: {len(analysis.capture.words):,} samples, "
                    f"peak {analysis.dominant_frequency_hz / 1e6:.6f} MHz"
                )
            else:
                render_incomplete(self.figure, shot)
                rows = [
                    ("Shot ID", str(shot.shot_id)),
                    ("State", "incomplete"),
                    ("Issues", ", ".join(shot.issue_names) or "unspecified"),
                    ("Missing packets", str(shot.missing_packets)),
                    ("Packet interval", f"{shot.first_packet_sequence}..{shot.last_packet_sequence}"),
                    ("Configuration ID", f"0x{shot.config_id:016x}"),
                ]
                self.status.set(f"Shot {shot_id}: incomplete measurement quarantined")
            for name, value in rows:
                self.metadata.insert("", "end", text=name, values=(value,))
            self.canvas.draw_idle()
        except (OSError, ValueError) as exc:
            self.status.set(f"Shot {shot_id} analysis failed: {exc}")

    def move_selection(self, delta: int) -> None:
        if self.bundle is None or not self.bundle.record_count:
            return
        current = self.selected_record_index
        if current is None:
            current = self.bundle.record_count - 1
        target = max(0, min(self.bundle.record_count - 1, current + delta))
        self.select_record(target)

    def on_navigation_key(self, event: object, delta: int) -> str | None:
        widget = getattr(event, "widget", None)
        if widget is not None and widget.winfo_class() in ("Entry", "TEntry"):
            return None
        self.move_selection(delta)
        return "break"

    def jump_to_shot(self) -> None:
        if self.bundle is None:
            return
        try:
            shot_id = int(self.shot_id_entry.get().strip(), 0)
        except ValueError:
            self.status.set("Shot ID must be a decimal or 0x-prefixed integer")
            return
        ordinal = self.bundle.find_shot_id(shot_id)
        if ordinal is None:
            self.status.set(f"Shot {shot_id} is not present in this run")
            return
        self.select_record(ordinal)

    def save_png(self) -> None:
        from tkinter import filedialog

        if self.bundle is None or self.selected_shot_id is None:
            return
        default = f"shot-{self.selected_shot_id}.png"
        selected = filedialog.asksaveasfilename(
            title="Export analyzer view", defaultextension=".png",
            initialfile=default, filetypes=(("PNG image", "*.png"),),
        )
        if selected:
            self.figure.savefig(selected, dpi=160, facecolor=self.figure.get_facecolor())
            self.status.set(f"Saved {selected}")

    def periodic_refresh(self) -> None:
        if self.follow.get() and self.bundle is not None:
            self.load_run(self.bundle.path, preserve=True)
        self.root.after(1000, self.periodic_refresh)

    def run(self) -> None:
        self.root.mainloop()


def select_shot(bundle: RunBundle, shot_id: int | None) -> ShotRecord:
    if shot_id is not None:
        for shot in bundle.shots:
            if shot.shot_id == shot_id:
                return shot
        raise ValueError(f"shot {shot_id} is not present")
    if not bundle.complete_shots:
        raise ValueError("run contains no complete shots")
    return bundle.complete_shots[0]


def write_snapshot(
    run_path: Path,
    output: Path,
    *,
    shot_id: int | None,
    fallback_rate: float,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    bundle = RunBundle.open(run_path)
    shot = select_shot(bundle, shot_id)
    figure = Figure(figsize=(13.5, 8.5), dpi=100, facecolor="#f4f5f7")
    if shot.complete:
        analysis = analyze_shot(bundle, shot, fallback_sample_rate_hz=fallback_rate)
        render_analysis(figure, analysis)
    else:
        render_incomplete(figure, shot)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, facecolor=figure.get_facecolor())
    print(f"PASS wrote {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", nargs="?", type=Path, help="Q-Crate run directory")
    parser.add_argument("--sample-rate-hz", type=float, default=DEFAULT_SAMPLE_RATE_HZ,
                        help="fallback for old manifests without stream metadata")
    parser.add_argument("--snapshot", type=Path, help="write one PNG without opening the GUI")
    parser.add_argument("--shot", type=int, help="shot ID for --snapshot")
    parser.add_argument(
        "--session-log", type=Path,
        help="append machine-readable GUI lifecycle evidence",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sample_rate_hz <= 0:
        raise SystemExit("error: sample rate must be positive")
    try:
        if args.snapshot:
            if args.run is None:
                raise ValueError("--snapshot requires a run directory")
            write_snapshot(args.run, args.snapshot, shot_id=args.shot,
                           fallback_rate=args.sample_rate_hz)
        else:
            AnalyzerApplication(args.run, args.sample_rate_hz,
                                args.session_log).run()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
