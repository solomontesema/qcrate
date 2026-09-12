#!/usr/bin/env python3
"""Focused contract and numerical tests for the DP-6E sweep runner."""
from __future__ import annotations

import binascii
import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


PROFILE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROFILE_DIR))

import qcrate_sweep as sweep  # noqa: E402
import qcrate_sweep_acceptance as acceptance  # noqa: E402


SPEC = PROFILE_DIR / "examples" / "lo_sweep.json"


def make_exact_run(root: Path, resolved_path: Path, ordinal: int) -> None:
    resolved = sweep.profile_contract.load_resolved_profile(resolved_path)
    words_per_shot = (
        resolved["acquisition"]["frame_length_words"]
        * resolved["acquisition"]["frame_count"]
    )
    words = sweep.analyzer.expected_words(str(resolved_path), words_per_shot)
    payload = np.asarray(words, dtype="<u4").tobytes()
    config_id = int(resolved["identity"]["dsp_config_id"], 0)
    run_id = ordinal + 1
    stream_id = 1
    root.mkdir(parents=True)
    (root / "samples.iq16").write_bytes(payload)
    header = struct.pack("!IHHH6xQI4x", 0x51494458, 1, 32, 128, run_id, stream_id)
    record = struct.pack(
        "!QIIQQQQQQQIIIIIIIIIHHQ8x",
        ordinal + 1, 1, 0, 0, 17, 1000 + ordinal, 100, 200, 0, len(payload),
        resolved["acquisition"]["frame_count"],
        resolved["acquisition"]["frame_length_words"],
        1, 1, 0, 0, 0, binascii.crc32(payload) & 0xFFFF_FFFF, 0, 2, 4,
        config_id,
    )
    (root / "shots.qidx").write_bytes(header + record)
    center_hz = round(float(resolved["dsp"]["realized"]["lo_frequency_hz"]))
    common = {
        "run_id": f"0x{run_id:016x}",
        "stream_id": f"0x{stream_id:08x}",
    }
    (root / "run.json").write_text(json.dumps({
        "format": "qcrate-run-v1",
        **common,
        "complete": True,
        "stream": {
            "config_id": resolved["identity"]["dsp_config_id"],
            "center_frequency_hz": center_hz,
            "sample_rate_numerator": 12_500_000,
            "sample_rate_denominator": 1,
        },
    }), encoding="utf-8")
    (root / "sender.json").write_text(json.dumps({
        "format": "qcrate-sender-report-v1",
        **common,
        "complete": True,
        "stream": {
            "config_id": resolved["identity"]["dsp_config_id"],
            "center_frequency_hz": center_hz,
        },
        "health": {
            "missed_triggers": 0,
            "skipped_triggers": 0,
            "dma_error_events": 0,
        },
    }), encoding="utf-8")
    shutil.copyfile(resolved_path, root / sweep.analyzer.RUN_PROFILE_NAME)


class SweepPlanTests(unittest.TestCase):
    def test_plan_has_distinct_identities_and_all_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sweep"
            plan = sweep.prepare_plan(SPEC, root)
            self.assertEqual(len(plan["points"]), 10)
            self.assertEqual(
                {point["region"] for point in plan["points"]},
                {"passband", "transition", "stopband"},
            )
            self.assertEqual(sum(point["reference"] for point in plan["points"]), 1)
            self.assertEqual(
                len({point["dsp_config_id"] for point in plan["points"]}),
                len(plan["points"]),
            )
            self.assertEqual(sweep.load_plan(root)["sweep_sha256"], plan["sweep_sha256"])

    def test_mislabeled_frequency_region_is_rejected(self) -> None:
        document = sweep.load_json(SPEC)
        document["points"][0]["region"] = "stopband"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "bad.json"
            spec.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(sweep.SweepError, "passband, not stopband"):
                sweep.prepare_plan(spec, root / "sweep")
            self.assertFalse((root / "sweep").exists())

    def test_stale_plan_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sweep"
            sweep.prepare_plan(SPEC, root)
            path = root / "sweep-plan.json"
            document = sweep.load_json(path)
            document["points"][0]["profile_sha256"] = "0" * 64
            sweep.atomic_json(path, document)
            with self.assertRaisesRegex(sweep.SweepError, "stale or corrupted"):
                sweep.load_plan(root)

    def test_interrupted_run_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            run.mkdir()
            (run / "evidence.txt").write_text("partial", encoding="ascii")
            destination = sweep._preserve_interrupted(run)
            self.assertFalse(run.exists())
            self.assertEqual((destination / "evidence.txt").read_text(), "partial")

    def test_target_fault_script_uses_owned_state_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sweep"
            plan = sweep.prepare_plan(SPEC, root)
            active = sweep.profile_contract.load_resolved_profile(
                root / plan["points"][-1]["resolved_profile"]
            )
            alternate = sweep.profile_contract.load_resolved_profile(
                root / plan["points"][0]["resolved_profile"]
            )
            script = acceptance.target_fault_script(
                active, alternate, "/tmp/demo.qseq"
            )
            self.assertIn("config-stage", script)
            self.assertIn("0x00008000", script)
            self.assertIn("qcrate-sequence arm", script)
            self.assertIn("qcrate-sequence abort", script)
            self.assertIn("R5 reason        : 3 ", script)
            self.assertIn("R5 reason        : 10 ", script)
            self.assertLess(script.index("qcrate-sequence arm"), script.index("config-commit"))
            self.assertIn(alternate["identity"]["dsp_config_id"], script)
            syntax = acceptance.subprocess.run(
                ["sh", "-n"], input=script, text=True, capture_output=True
            )
            self.assertEqual(syntax.returncode, 0, syntax.stderr)


class SweepEstimatorTests(unittest.TestCase):
    def test_coherent_tone_recovers_complex_amplitude_and_phase(self) -> None:
        sample_rate = 12_500_000.0
        frequency = 1_375_000.0
        amplitude = 0.37
        phase = np.deg2rad(173.0)
        indices = np.arange(4096)
        samples = amplitude * np.exp(
            1j * (2.0 * np.pi * frequency * indices / sample_rate + phase)
        )
        actual = sweep.coherent_tone(samples, frequency, sample_rate)
        self.assertAlmostEqual(abs(actual), amplitude, places=12)
        self.assertAlmostEqual(np.angle(actual), phase, places=12)

    def test_alias_and_wrapped_phase_are_unambiguous(self) -> None:
        self.assertAlmostEqual(
            sweep.alias_frequency(7_000_000, 12_500_000), -5_500_000
        )
        self.assertAlmostEqual(sweep.phase_error_degrees(-179.0, 179.0), 2.0)
        self.assertEqual(sweep.response_region(2_000_000), "passband")
        self.assertEqual(sweep.response_region(3_000_000), "transition")
        self.assertEqual(sweep.response_region(6_250_000), "stopband")

    def test_complete_exact_sweep_produces_report_and_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sweep"
            plan = sweep.prepare_plan(SPEC, root)
            for point in plan["points"]:
                make_exact_run(
                    root / point["run"],
                    root / point["resolved_profile"],
                    point["ordinal"],
                )
            summary = sweep.analyze(root)
            self.assertEqual(summary["result"], "PASS")
            self.assertEqual(summary["total_points"], 10)
            self.assertEqual(summary["total_iq_words_checked"], 40_960)
            self.assertTrue(all(point["iq_mismatches"] == 0 for point in summary["points"]))
            self.assertTrue(any(not point["phase_required"] for point in summary["points"]))
            self.assertGreater((root / "dp6e-sweep.png").stat().st_size, 10_000)

            cases = {
                name: {"pass": True, "requirement": name}
                for name in acceptance.REQUIRED_FAULT_CASES
            }
            accepted = acceptance.finalize_acceptance(root, {
                "format": acceptance.FAULT_FORMAT,
                "target_command_status": 0,
                "cases": cases,
            })
            self.assertEqual(accepted["result"], "PASS")
            self.assertTrue((root / "dp6e-acceptance.json").is_file())
            self.assertGreater(
                (root / "dp6e-acceptance.png").stat().st_size, 10_000
            )


if __name__ == "__main__":
    unittest.main()
