#!/usr/bin/env python3
"""Focused tests for the DP-6A experiment-profile contract."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROFILE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROFILE_DIR.parents[1]
sys.path.insert(0, str(PROFILE_DIR))
sys.path.insert(0, str(REPO_ROOT / "host" / "dsp_model"))

import qcrate_dsp as dsp  # noqa: E402
import qcrate_profile as profile  # noqa: E402


LO_29 = PROFILE_DIR / "examples" / "lo_29mhz.json"
LO_28_5 = PROFILE_DIR / "examples" / "lo_28_5mhz.json"
LO_29_RESOLVED = PROFILE_DIR / "examples" / "resolved" / "lo_29mhz.resolved.json"


class ResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lo_29 = profile.compile_profile(LO_29)
        cls.lo_28_5 = profile.compile_profile(LO_28_5)

    def test_exact_tuning_words_and_ids(self) -> None:
        self.assertEqual(
            self.lo_29["dsp"]["active"]["LO_PHASE_INCREMENT"], 0x251EB852
        )
        self.assertEqual(
            self.lo_28_5["dsp"]["active"]["LO_PHASE_INCREMENT"], 0x247AE148
        )
        self.assertEqual(
            self.lo_29["identity"]["dsp_config_id"], "0x5db4fb578b27b09f"
        )
        self.assertEqual(
            self.lo_28_5["identity"]["dsp_config_id"], "0xaf46287bb969ed24"
        )
        self.assertNotEqual(
            self.lo_29["identity"]["profile_sha256"],
            self.lo_28_5["identity"]["profile_sha256"],
        )

    def test_model_accepts_resolved_profiles(self) -> None:
        config = dsp.config_from_resolved_document(self.lo_29)
        self.assertEqual(config.lo_frequency_hz, 29_000_000)
        self.assertEqual(config.frame_length, 1024)
        self.assertEqual(config.frame_count, 4)

    def test_tracked_resolved_profile_is_current_and_loadable(self) -> None:
        tracked = json.loads(LO_29_RESOLVED.read_text(encoding="ascii"))
        self.assertEqual(tracked, self.lo_29)
        self.assertEqual(profile.load_resolved_profile(LO_29_RESOLVED), tracked)
        config = dsp.load_config(LO_29_RESOLVED)
        self.assertEqual(config.lo_frequency_hz, 29_000_000)

    def test_model_rejects_tampered_integer(self) -> None:
        document = copy.deepcopy(self.lo_29)
        document["dsp"]["active"]["LO_PHASE_INCREMENT"] += 1
        with self.assertRaises(dsp.DspConfigurationError):
            dsp.config_from_resolved_document(document)

    def test_resolved_loader_rejects_tampered_identity_input(self) -> None:
        document = copy.deepcopy(self.lo_29)
        document["dsp"]["active"]["LO_PHASE_INCREMENT"] += 1
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=REPO_ROOT, encoding="utf-8"
        ) as stream:
            json.dump(document, stream)
            stream.flush()
            with self.assertRaises(profile.ProfileError):
                profile.load_resolved_profile(Path(stream.name))

    def test_display_name_does_not_change_identity(self) -> None:
        source = json.loads(LO_29.read_text(encoding="utf-8"))
        source["name"] = "A different operator label"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=REPO_ROOT, encoding="utf-8"
        ) as stream:
            json.dump(source, stream)
            stream.flush()
            renamed = profile.compile_profile(Path(stream.name))
        self.assertEqual(renamed["identity"], self.lo_29["identity"])

    def test_acquisition_change_affects_only_full_profile_identity(self) -> None:
        source = json.loads(LO_29.read_text(encoding="utf-8"))
        source["acquisition"]["frame_count"] = 3
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=REPO_ROOT, encoding="utf-8"
        ) as stream:
            json.dump(source, stream)
            stream.flush()
            changed = profile.compile_profile(Path(stream.name))
        self.assertEqual(
            changed["identity"]["dsp_config_id"],
            self.lo_29["identity"]["dsp_config_id"],
        )
        self.assertNotEqual(
            changed["identity"]["profile_sha256"],
            self.lo_29["identity"]["profile_sha256"],
        )

    def test_register_writes_are_aligned_and_unique(self) -> None:
        offsets = [entry["offset"] for entry in self.lo_29["register_writes"]]
        self.assertTrue(all(offset % 4 == 0 for offset in offsets))
        self.assertEqual(len(offsets), len(set(offsets)))
        self.assertTrue(all(0x3000 <= offset < 0x4000 for offset in offsets))

    def test_resolved_profile_maps_to_typed_r5_command(self) -> None:
        command = profile.configuration_command(self.lo_29, "qcrate-control")
        self.assertEqual(command[:3], [
            "qcrate-control", "config-apply", "0x5db4fb578b27b09f"
        ])
        self.assertEqual(command[3], "0x26666666")
        self.assertEqual(command[8], "0x251eb852")
        self.assertEqual(command[-2:], ["1024", "4"])

    def test_command_rejects_nonresolved_profile(self) -> None:
        with self.assertRaises(profile.ProfileError):
            profile.configuration_command({"format": "wrong"}, "qcrate-control")


if __name__ == "__main__":
    unittest.main()
