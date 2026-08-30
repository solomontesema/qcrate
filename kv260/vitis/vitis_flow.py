#!/usr/bin/env python3
"""Host-side entry point for the reproducible Q-Crate R5 Vitis flow."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_CONFIG = REPO_ROOT / "config" / "build.json"
VITIS_SCRIPT = REPO_ROOT / "kv260" / "vitis" / "create_r5_firmware.py"
VITIS_BUILD = REPO_ROOT / "build" / "vitis"
WORKSPACE = VITIS_BUILD / "workspace"
ARTIFACT = REPO_ROOT / "build" / "artifacts" / "qcrate-r5.elf"
RECIPE_FILES = (
    REPO_ROOT
    / "kv260"
    / "linux"
    / "petalinux"
    / "qcrate-kv260"
    / "project-spec"
    / "meta-user"
    / "recipes-apps"
    / "qcrate-openamp"
    / "files"
)


class FlowError(RuntimeError):
    """A concise user-facing flow failure."""


def load_vitis_settings() -> Path:
    with BUILD_CONFIG.open(encoding="utf-8") as stream:
        config = json.load(stream)
    configured = os.environ.get("QCRATE_VITIS_SETTINGS", config["vitis_settings"])
    settings = Path(configured).expanduser().resolve()
    if not settings.is_file():
        raise FlowError(f"Vitis settings script is missing: {settings}")
    return settings


def run_vitis_build() -> None:
    settings = load_vitis_settings()
    data_dir = VITIS_BUILD / "vitis-data"
    data_dir.mkdir(parents=True, exist_ok=True)

    command = (
        f"source {shlex.quote(str(settings))} && "
        f"export XILINX_VITIS_DATA_DIR={shlex.quote(str(data_dir))} && "
        f"vitis -s {shlex.quote(str(VITIS_SCRIPT))}"
    )
    print("\n== Build R5-0 FreeRTOS/OpenAMP firmware ==", flush=True)
    print(f"- {command}", flush=True)
    subprocess.run(
        ["bash", "-lc", command],
        cwd=REPO_ROOT,
        check=True,
    )


def find_built_elf() -> Path:
    for filename in ("qcrate_r5.elf", "rpmsg-echo.elf", "rpmsg-echo.out"):
        candidates = []
        for path in WORKSPACE.rglob(filename):
            if path.is_file():
                with path.open("rb") as stream:
                    if stream.read(4) == b"\x7fELF":
                        candidates.append(path)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            formatted = "\n".join(f"  {path}" for path in candidates)
            raise FlowError(
                f"multiple {filename} outputs found in the Vitis workspace:\n"
                f"{formatted}"
            )
    raise FlowError("R5 service ELF was not found in the Vitis workspace")


def stage_recipe_inputs() -> None:
    elf = find_built_elf()
    RECIPE_FILES.mkdir(parents=True, exist_ok=True)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)

    copies = (
        (elf, ARTIFACT),
        (elf, RECIPE_FILES / "qcrate-r5.elf"),
        (
            REPO_ROOT / "kv260" / "linux" / "openamp" / "qcrate_control.c",
            RECIPE_FILES / "qcrate_control.c",
        ),
        (
            REPO_ROOT / "common" / "protocol" / "qcrate_protocol.h",
            RECIPE_FILES / "qcrate_protocol.h",
        ),
        (
            REPO_ROOT / "kv260" / "linux" / "openamp" / "qcrate_rpmsg_client.c",
            RECIPE_FILES / "qcrate_rpmsg_client.c",
        ),
        (
            REPO_ROOT / "kv260" / "linux" / "openamp" / "qcrate_rpmsg_client.h",
            RECIPE_FILES / "qcrate_rpmsg_client.h",
        ),
    )
    for source, destination in copies:
        shutil.copy2(source, destination)
        print(f"- staged {destination.relative_to(REPO_ROOT)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("build", "stage", "all"),
        help="build firmware, stage Yocto inputs, or perform both",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.action in ("build", "all"):
            run_vitis_build()
        if args.action in ("stage", "all"):
            print("\n== Stage R5 firmware and shared sources for Yocto ==")
            stage_recipe_inputs()
    except (FlowError, OSError, subprocess.CalledProcessError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
