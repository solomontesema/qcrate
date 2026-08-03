#!/usr/bin/env python3
"""Create a local Kria xmutil firmware package for Q-Crate."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path


APP_NAME = "qcrate_kv260"
DEFAULT_OVERLAY_DTSI = "kv260/linux/xmutil/qcrate_kv260.dtsi"
DEFAULT_DTG_BRANCH = "xlnx_rel_v2024.2"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def find_xsct(explicit: str | None) -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)

    xilinx_vitis = os.environ.get("XILINX_VITIS")
    if xilinx_vitis:
        candidates.append(str(Path(xilinx_vitis) / "bin" / "xsct"))

    candidates.extend([
        "/tools/Xilinx/Vitis/2024.2/bin/xsct",
        "/tools/Xilinx/Vivado/2024.2/scripts/xsct",
    ])

    from_path = shutil.which("xsct")
    if from_path:
        candidates.append(from_path)

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    raise FileNotFoundError(
        "xsct not found. Source Vitis settings or pass --xsct /path/to/xsct."
    )


def find_tool(name: str, explicit: str | None = None) -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)

    xilinx_vivado = os.environ.get("XILINX_VIVADO")
    if xilinx_vivado:
        candidates.append(str(Path(xilinx_vivado) / "bin" / name))

    candidates.extend([
        f"/tools/Xilinx/Vivado/2024.2/bin/{name}",
        f"/tools/Xilinx/Vitis/2024.2/bin/{name}",
    ])

    from_path = shutil.which(name)
    if from_path:
        candidates.append(from_path)

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    raise FileNotFoundError(f"{name} not found")


def run(cmd: list[str], cwd: Path) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def write_shell_json(path: Path) -> None:
    data = {
        "shell_type": "XRT_FLAT",
        "num_slots": 1,
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, APP_NAME)),
        "pcie_config": {
            "device_id": "0x0001",
            "vendor_id": "0x10ee",
        },
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_load_only_overlay(src: Path, dst: Path) -> None:
    """Keep the XSA-generated fpga_full overlay but remove driver probe nodes."""
    lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    skip_depth = 0

    for line in lines:
        stripped = line.strip()
        if skip_depth > 0:
            skip_depth += line.count("{")
            skip_depth -= line.count("}")
            continue
        if "zyxclmm_drm" in stripped:
            skip_depth = line.count("{") - line.count("}")
            if skip_depth <= 0:
                skip_depth = 1
            continue
        if stripped.startswith("&amba"):
            break

        out.append(line)

    dst.write_text("".join(out), encoding="utf-8")


def ensure_bit_bin(root: Path, bit: Path, bit_bin: Path) -> None:
    if bit_bin.exists() and (not bit.exists() or bit_bin.stat().st_mtime >= bit.stat().st_mtime):
        return
    if not bit.exists():
        raise FileNotFoundError(f"missing bitstream for .bit.bin conversion: {bit}")

    bootgen = find_tool("bootgen")
    bit_bin.parent.mkdir(parents=True, exist_ok=True)
    bif = bit_bin.parent / "qcrate_bitstream.bif"
    bif.write_text(
        "all:\n"
        "{\n"
        f"  [destination_device = pl] {bit}\n"
        "}\n",
        encoding="utf-8",
    )
    run([bootgen, "-image", str(bif), "-arch", "zynqmp", "-o", str(bit_bin), "-w"], root)


def main() -> int:
    root = repo_root()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", default=APP_NAME,
                        help=f"xmutil application name, default: {APP_NAME}")
    parser.add_argument("--xsa", default="build/artifacts/qcrate_kv260.xsa",
                        help="input XSA path")
    parser.add_argument("--bit", default="build/artifacts/qcrate_kv260.bit",
                        help="input .bit path used to refresh .bit.bin")
    parser.add_argument("--bit-bin", default="build/artifacts/qcrate_kv260.bit.bin",
                        help="input .bit.bin path")
    parser.add_argument("--out-dir", default="build/xmutil",
                        help="output root for generated package")
    parser.add_argument("--use-manual-overlay", action="store_true",
                        help="compile the repo-owned manual overlay instead of XSCT/createdts")
    parser.add_argument("--probe-kernel-drivers", action="store_true",
                        help="keep XSA-generated Linux driver nodes in the DTBO")
    parser.add_argument("--overlay-dtsi", default=DEFAULT_OVERLAY_DTSI,
                        help="manual overlay source used only with --use-manual-overlay")
    parser.add_argument("--dtg-branch", default=DEFAULT_DTG_BRANCH,
                        help=f"device-tree-xlnx branch for createdts, default: {DEFAULT_DTG_BRANCH}")
    parser.add_argument("--dtg-repo", default=None,
                        help="local device-tree-xlnx repo; avoids createdts network clone")
    parser.add_argument("--xsct", default=None,
                        help="path to xsct")
    parser.add_argument("--keep-dts", action="store_true",
                        help="keep generated createdts sources under the output directory")
    args = parser.parse_args()

    app_name = args.app_name
    xsa = resolve(root, args.xsa)
    bit = resolve(root, args.bit)
    bit_bin = resolve(root, args.bit_bin)
    overlay_dtsi = resolve(root, args.overlay_dtsi)
    dtg_repo = resolve(root, args.dtg_repo) if args.dtg_repo else None
    out_root = resolve(root, args.out_dir)
    package_dir = out_root / app_name
    dts_dir = out_root / "createdts"

    ensure_bit_bin(root, bit, bit_bin)

    required_files = [bit_bin]
    if args.use_manual_overlay:
        required_files.append(overlay_dtsi)
    else:
        required_files.append(xsa)
        if dtg_repo:
            required_files.append(dtg_repo)

    for required in required_files:
        if not required.exists():
            raise FileNotFoundError(required)

    dtc = shutil.which("dtc")
    if not dtc:
        raise FileNotFoundError("dtc not found")

    package_dir.mkdir(parents=True, exist_ok=True)

    if args.use_manual_overlay:
        pl_dtsi = overlay_dtsi
    else:
        xsct = find_xsct(args.xsct)
        cmd = [
            xsct,
            "-nodisp",
            str(root / "scripts" / "generate_dtbo_createdts.tcl"),
            str(xsa),
            str(dts_dir),
            app_name,
            args.dtg_branch,
        ]
        if dtg_repo:
            cmd.append(str(dtg_repo))
        run(cmd, root)
        pl_dtsi = dts_dir / app_name / "psu_cortexa53_0" / "device_tree_domain" / "bsp" / "pl.dtsi"
        if not pl_dtsi.exists():
            raise FileNotFoundError(f"createdts did not create {pl_dtsi}")
        if not args.probe_kernel_drivers:
            load_only_dtsi = dts_dir / app_name / "psu_cortexa53_0" / "device_tree_domain" / "bsp" / "pl-load-only.dtsi"
            write_load_only_overlay(pl_dtsi, load_only_dtsi)
            pl_dtsi = load_only_dtsi

    shutil.copy2(bit_bin, package_dir / f"{app_name}.bit.bin")
    write_shell_json(package_dir / "shell.json")

    run([dtc, "-@", "-O", "dtb", "-o", str(package_dir / f"{app_name}.dtbo"),
         str(pl_dtsi)], root)

    if not args.use_manual_overlay and not args.keep_dts:
        shutil.rmtree(dts_dir)

    print()
    print(f"Created xmutil firmware package: {package_dir}")
    print()
    print("Install on KV260:")
    print(f"  scp -r {package_dir} ubuntu@<kv260-ip>:~/qcrate/")
    print(f"  sudo rm -rf /lib/firmware/xilinx/{app_name}")
    print(f"  sudo cp -r ~/qcrate/{app_name} /lib/firmware/xilinx/")
    print("  sudo xmutil unloadapp")
    print(f"  sudo xmutil loadapp {app_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
