#!/usr/bin/env python3
"""Build, package, and deploy the Q-Crate KV260 PetaLinux platform."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Mapping, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
PETALINUX_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPT_DIR.parents[3]
PROJECT_DIR = PETALINUX_DIR / "qcrate-kv260"
PROJECT_PL_DTSI = PROJECT_DIR / "project-spec" / "configs" / "pl.dtsi"
PROJECT_WORKSPACE = PROJECT_DIR / "components" / "plnx_workspace"
PROJECT_WORKSPACE_BACKUP = PROJECT_DIR / "components" / \
    "plnx_workspace.qcrate-previous"
IMAGES_DIR = PROJECT_DIR / "images" / "linux"
XSA_FILE = REPO_ROOT / "build" / "artifacts" / "qcrate_kv260.xsa"
SDT_DIR = REPO_ROOT / "build" / "petalinux" / "sdt" / "qcrate-kv260"
SDT_SCRIPT = SCRIPT_DIR / "generate_sdt.tcl"
BOOTFW_SCRIPT = SCRIPT_DIR / "package_bootfw.sh"
WIC_IMAGE = IMAGES_DIR / "petalinux-sdimage.wic.xz"
BOOTFW_IMAGE = IMAGES_DIR / "qcrate-kv260-bootfw.bin"
BOOTFW_CHECKSUM = IMAGES_DIR / "qcrate-kv260-bootfw.bin.sha256"
DEFAULT_VITIS_SETTINGS = Path("/tools/Xilinx/Vitis/2024.2/settings64.sh")
DEFAULT_PETALINUX_SETTINGS = Path("/tools/Xilinx/PetaLinux/2024.2/settings.sh")
DEFAULT_BSP_DIR = PETALINUX_DIR / "KV260_BSP_DOWLOADED"

REQUIRED_ROOTFS_FILES = {
    "./usr/bin/image_update",
    "./usr/bin/qcrate-apb",
    "./usr/bin/qcrate-control",
    "./usr/bin/qcrate-first-boot",
    "./usr/bin/qcrate-load-pl",
    "./usr/bin/qcrate-r5-remoteproc",
    "./usr/bin/xmutil",
    "./usr/lib/firmware/qcrate/qcrate-r5.elf",
    "./usr/lib/firmware/xilinx/base/pl.dtbo",
    "./usr/lib/firmware/xilinx/base/qcrate_kv260.bit.bin",
    "./usr/lib/systemd/system/qcrate-pl-load.service",
    "./usr/lib/systemd/system/qcrate-r5.service",
}


class FlowError(RuntimeError):
    """Expected workflow or artifact failure."""


class Flow:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.dry_run = args.dry_run
        self._petalinux_env: dict[str, str] | None = None
        self._vitis_env: dict[str, str] | None = None

    def command(
        self,
        argv: Sequence[os.PathLike[str] | str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        sudo: bool = False,
    ) -> None:
        command = [os.fspath(item) for item in argv]
        if sudo:
            command.insert(0, "sudo")
        location = cwd or REPO_ROOT
        print(
            f"+ (cd {shlex.quote(os.fspath(location))} && {shlex.join(command)})",
            flush=True,
        )
        if self.dry_run:
            return
        subprocess.run(command, cwd=location, env=env, check=True)

    @staticmethod
    def require_file(path: Path, description: str) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise FlowError(f"missing {description}: {path}")

    @staticmethod
    def sourced_environment(settings: Path) -> dict[str, str]:
        if not settings.is_file():
            raise FlowError(f"missing tool settings script: {settings}")
        source_command = f"source {shlex.quote(os.fspath(settings))} >/dev/null && env -0"
        command = [
            "bash",
            "-c",
            source_command,
        ]
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE)
        environment: dict[str, str] = {}
        for entry in result.stdout.split(b"\0"):
            if not entry:
                continue
            key, value = entry.split(b"=", 1)
            environment[key.decode()] = value.decode()
        return environment

    @property
    def petalinux_env(self) -> dict[str, str]:
        if self._petalinux_env is None:
            self._petalinux_env = self.sourced_environment(self.args.petalinux_settings)
        return self._petalinux_env

    @property
    def vitis_env(self) -> dict[str, str]:
        if self._vitis_env is None:
            self._vitis_env = self.sourced_environment(self.args.vitis_settings)
        return self._vitis_env

    def configure(self) -> None:
        print("\n== Configure PetaLinux from the accepted XSA ==")
        self.require_file(XSA_FILE, "Vivado XSA")
        self.require_file(SDT_SCRIPT, "SDT-generation script")
        self.require_file(PROJECT_DIR / "project-spec" / "configs" / "config",
                          "tracked PetaLinux configuration")
        self.ensure_project_metadata()

        if self.dry_run:
            self.command(
                ["xsct", "-nodisp", SDT_SCRIPT, XSA_FILE, SDT_DIR],
                env=self.vitis_env,
            )
        else:
            SDT_DIR.parent.mkdir(parents=True, exist_ok=True)
            temporary_parent = Path(
                tempfile.mkdtemp(prefix="qcrate-kv260-sdt-", dir=SDT_DIR.parent)
            )
            temporary = temporary_parent / "qcrate-kv260"
            backup = SDT_DIR.with_name(f"{SDT_DIR.name}.previous")
            try:
                self.command(
                    ["xsct", "-nodisp", SDT_SCRIPT, XSA_FILE, temporary],
                    env=self.vitis_env,
                )
                self.audit_sdt(temporary)
                if backup.exists():
                    shutil.rmtree(backup)
                if SDT_DIR.exists():
                    SDT_DIR.rename(backup)
                temporary.rename(SDT_DIR)
                if backup.exists():
                    shutil.rmtree(backup)
                temporary_parent.rmdir()
            except Exception:
                if temporary_parent.exists():
                    shutil.rmtree(temporary_parent)
                if not SDT_DIR.exists() and backup.exists():
                    backup.rename(SDT_DIR)
                raise

        previous_pl_dtsi = None
        workspace_saved = False
        try:
            if not self.dry_run:
                if PROJECT_PL_DTSI.exists():
                    previous_pl_dtsi = PROJECT_PL_DTSI.read_bytes()
                    PROJECT_PL_DTSI.unlink()
                if PROJECT_WORKSPACE_BACKUP.exists():
                    if PROJECT_WORKSPACE.exists():
                        shutil.rmtree(PROJECT_WORKSPACE_BACKUP)
                    else:
                        PROJECT_WORKSPACE_BACKUP.rename(PROJECT_WORKSPACE)
                if PROJECT_WORKSPACE.exists():
                    PROJECT_WORKSPACE.rename(PROJECT_WORKSPACE_BACKUP)
                    workspace_saved = True
            self.command(
                ["petalinux-config", "--get-hw-description", SDT_DIR,
                 "--silentconfig"],
                cwd=PROJECT_DIR,
                env=self.petalinux_env,
            )
            self.command(
                ["petalinux-config", "--silentconfig"],
                cwd=PROJECT_DIR,
                env=self.petalinux_env,
            )
            if not self.dry_run:
                self.audit_sdt(SDT_DIR)
                self.audit_project_overlay()
                self.audit_project_config()
                if workspace_saved:
                    shutil.rmtree(PROJECT_WORKSPACE_BACKUP)
        except Exception:
            if not self.dry_run:
                PROJECT_PL_DTSI.unlink(missing_ok=True)
                if previous_pl_dtsi is not None:
                    PROJECT_PL_DTSI.parent.mkdir(parents=True, exist_ok=True)
                    PROJECT_PL_DTSI.write_bytes(previous_pl_dtsi)
                shutil.rmtree(PROJECT_WORKSPACE, ignore_errors=True)
                if workspace_saved:
                    PROJECT_WORKSPACE_BACKUP.rename(PROJECT_WORKSPACE)
            raise

    def find_bsp(self) -> Path:
        if self.args.bsp is not None:
            bsp = self.args.bsp.resolve()
            self.require_file(bsp, "KV260 SDT BSP")
            return bsp
        candidates = sorted(DEFAULT_BSP_DIR.glob("*.bsp"))
        if len(candidates) != 1:
            raise FlowError(
                "fresh project bootstrap needs exactly one BSP in "
                f"{DEFAULT_BSP_DIR}, or pass --bsp /path/to/kv260.bsp"
            )
        return candidates[0]

    def ensure_project_metadata(self) -> None:
        metadata = PROJECT_DIR / ".petalinux" / "metadata"
        if metadata.is_file():
            content = metadata.read_text(encoding="utf-8", errors="replace")
            if "PETALINUX_VER=2024.2" not in content:
                raise FlowError(f"existing project metadata is not PetaLinux 2024.2: {metadata}")
            return

        bsp = self.find_bsp()
        tmpdir = PETALINUX_DIR / ".tmp" / "qcrate-kv260"
        print(f"INFO: reconstructing ignored PetaLinux project metadata from {bsp}")
        if self.dry_run:
            self.command(
                ["petalinux-create", "project", "-s", bsp, "--tmpdir", tmpdir],
                cwd=PETALINUX_DIR,
                env=self.petalinux_env,
            )
            return

        bootstrap_root = Path(
            tempfile.mkdtemp(prefix="qcrate-petalinux-bootstrap-", dir=PETALINUX_DIR)
        )
        try:
            self.command(
                ["petalinux-create", "project", "-s", bsp, "--tmpdir", tmpdir],
                cwd=bootstrap_root,
                env=self.petalinux_env,
            )
            candidates = list(bootstrap_root.glob("*/.petalinux/metadata"))
            if len(candidates) != 1:
                raise FlowError("BSP bootstrap did not create one PetaLinux project")
            shutil.copytree(candidates[0].parent, metadata.parent)
        finally:
            shutil.rmtree(bootstrap_root, ignore_errors=True)

    @staticmethod
    def audit_sdt(directory: Path) -> None:
        psu_init = directory / "psu_init.c"
        pl_dtsi = directory / "pl.dtsi"
        system_top = directory / "system-top.dts"
        Flow.require_file(psu_init, "SDT PS initialization")
        Flow.require_file(pl_dtsi, "SDT PL device tree")
        Flow.require_file(system_top, "SDT system address map")
        psu_text = psu_init.read_text(encoding="utf-8", errors="replace")
        for value, description in (
            ("0x013F3F07U, 0x01010203U", "PL0 DPLL / 2 / 1"),
            ("0x013F3F07U, 0x01010403U", "PL1 DPLL / 4 / 1"),
        ):
            if value not in psu_text:
                raise FlowError(f"SDT is missing {description}: {value}")
        system_text = system_top.read_text(encoding="utf-8", errors="replace").lower()
        for address in ("a0000000", "a0010000"):
            if address not in system_text:
                raise FlowError(f"SDT system address map is missing address {address}")

        pl_text = pl_dtsi.read_text(encoding="utf-8", errors="replace").lower()
        for value, description in (
            ("dma@a0000000", "AXI DMA node"),
            ("assigned-clock-rates = <200000000>", "200 MHz PL0 clock"),
            ("assigned-clock-rates = <100000000>", "100 MHz PL1 clock"),
        ):
            if value not in pl_text:
                raise FlowError(f"SDT PL description is missing {description}")

        sg_contract = (
            (r"xlnx,include-sg\s*(?:;|=\s*<\s*(?:0x)?0*1\s*>)",
             "enabled AXI DMA scatter-gather"),
            (r"xlnx,sg-length-width\s*=\s*<\s*(?:23|0x0*17)\s*>",
             "23-bit AXI DMA SG length"),
            (r"xlnx,sg-include-stscntrl-strm\s*=\s*<\s*(?:0x)?0+\s*>",
             "disabled AXI DMA control/status stream"),
        )
        for pattern, description in sg_contract:
            if re.search(pattern, pl_text) is None:
                raise FlowError(f"SDT PL description is missing {description}")

    @staticmethod
    def audit_project_overlay() -> None:
        Flow.require_file(PROJECT_PL_DTSI, "PetaLinux-generated PL overlay")
        overlay = PROJECT_PL_DTSI.read_text(
            encoding="utf-8", errors="replace"
        ).lower()
        required = (
            (r"/plugin/\s*;", "device-tree plugin declaration"),
            (r'firmware-name\s*=\s*"qcrate_kv260\.bit\.bin"',
             "Q-Crate FPGA firmware name"),
            (r"xlnx,include-sg\s*(?:;|=\s*<\s*(?:0x)?0*1\s*>)",
             "enabled AXI DMA scatter-gather"),
            (r"xlnx,sg-length-width\s*=\s*<\s*(?:23|0x0*17)\s*>",
             "23-bit AXI DMA SG length"),
            (r"xlnx,sg-include-stscntrl-strm\s*=\s*<\s*(?:0x)?0+\s*>",
             "disabled AXI DMA control/status stream"),
        )
        for pattern, description in required:
            if re.search(pattern, overlay) is None:
                raise FlowError(
                    f"PetaLinux-generated PL overlay is missing {description}"
                )

    @staticmethod
    def audit_project_config() -> None:
        config = (PROJECT_DIR / "project-spec" / "configs" / "config").read_text()
        required = (
            "CONFIG_SUBSYSTEM_FPGA_MANAGER=y",
            "CONFIG_SUBSYSTEM_PL_DT_OVERLAY_FULL=y",
            "CONFIG_SUBSYSTEM_ROOTFS_EXT4=y",
            "CONFIG_YOCTO_BBMC_CORTEXR5_0_FREERTOS=y",
        )
        for setting in required:
            if setting not in config:
                raise FlowError(f"PetaLinux configuration is missing {setting}")

    def build(self) -> None:
        print("\n== Build PetaLinux ==")
        self.command(["petalinux-build"], cwd=PROJECT_DIR, env=self.petalinux_env)
        if not self.dry_run:
            for path, description in (
                (IMAGES_DIR / "rootfs.tar.gz", "root filesystem archive"),
                (IMAGES_DIR / "Image", "Linux kernel"),
                (IMAGES_DIR / "boot.scr", "U-Boot script"),
                (IMAGES_DIR / "system.dtb", "system device tree"),
                (IMAGES_DIR / "zynqmp_fsbl.elf", "FSBL"),
                (IMAGES_DIR / "pmufw.elf", "PMU firmware"),
                (IMAGES_DIR / "bl31.elf", "TF-A"),
                (IMAGES_DIR / "u-boot.elf", "U-Boot"),
            ):
                self.require_file(path, description)
            self.audit_rootfs()

    @staticmethod
    def audit_rootfs() -> None:
        rootfs = IMAGES_DIR / "rootfs.tar.gz"
        Flow.require_file(rootfs, "root filesystem archive")
        with tarfile.open(rootfs, "r:gz") as archive:
            members = set(archive.getnames())
        missing = sorted(REQUIRED_ROOTFS_FILES - members)
        if missing:
            raise FlowError("rootfs is missing required files: " + ", ".join(missing))

    def package(self) -> None:
        print("\n== Package SD image and Kria A/B boot firmware ==")
        self.require_file(IMAGES_DIR / "rootfs.tar.gz", "root filesystem archive")
        if not self.dry_run:
            self.audit_rootfs()
        self.command(
            [
                "petalinux-package",
                "wic",
                "--bootfiles",
                "boot.scr Image system.dtb system-zynqmp-sck-kv-g-revB.dtb",
                "--wic-extra-args=-c xz",
            ],
            cwd=PROJECT_DIR,
            env=self.petalinux_env,
        )
        self.command([BOOTFW_SCRIPT], cwd=REPO_ROOT, env=self.petalinux_env)
        if self.dry_run:
            return
        self.require_file(WIC_IMAGE, "compressed SD image")
        self.require_file(BOOTFW_IMAGE, "A/B boot firmware")
        self.require_file(BOOTFW_CHECKSUM, "boot-firmware checksum")
        self.command(["xz", "-t", WIC_IMAGE])
        self.write_checksum(WIC_IMAGE)
        self.verify_checksum(BOOTFW_CHECKSUM, BOOTFW_IMAGE)
        print(f"INFO: SD image SHA-256 {self.sha256(WIC_IMAGE)}")
        print(f"INFO: boot firmware SHA-256 {self.sha256(BOOTFW_IMAGE)}")

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def write_checksum(cls, path: Path) -> Path:
        checksum = path.with_name(f"{path.name}.sha256")
        checksum.write_text(f"{cls.sha256(path)}  {path.name}\n", encoding="ascii")
        return checksum

    @classmethod
    def verify_checksum(cls, checksum: Path, artifact: Path) -> None:
        cls.require_file(checksum, "checksum")
        fields = checksum.read_text(encoding="ascii").strip().split()
        if len(fields) != 2 or fields[1].lstrip("*") != artifact.name:
            raise FlowError(f"invalid portable checksum file: {checksum}")
        actual = cls.sha256(artifact)
        if actual != fields[0].lower():
            raise FlowError(f"checksum mismatch for {artifact}")

    @staticmethod
    def block_device_tree(device: Path) -> dict[str, object]:
        result = subprocess.run(
            [
                "lsblk", "--json", "--bytes",
                "--output", "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,FSTYPE,LABEL,MOUNTPOINTS",
                os.fspath(device),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        devices = json.loads(result.stdout).get("blockdevices", [])
        if len(devices) != 1:
            raise FlowError(f"cannot identify one whole device at {device}")
        return devices[0]

    @staticmethod
    def children(tree: dict[str, object]) -> list[dict[str, object]]:
        return [child for child in tree.get("children", []) if isinstance(child, dict)]

    def confirm_device(self, device: Path, tree: dict[str, object]) -> None:
        if tree.get("type") != "disk":
            raise FlowError(f"--device must name a whole disk, got {tree.get('type')!r}")
        for node in [tree, *self.children(tree)]:
            mountpoints = [item for item in (node.get("mountpoints") or []) if item]
            if "/" in mountpoints:
                raise FlowError(f"refusing to overwrite the host root device: {device}")

        size_gib = int(tree.get("size") or 0) / (1024 ** 3)
        print("\nSD deployment target:")
        print(f"  path   : {tree.get('path')}")
        print(f"  size   : {size_gib:.1f} GiB")
        print(f"  model  : {(tree.get('model') or '').strip()}")
        print(f"  serial : {(tree.get('serial') or '').strip()}")
        print("  ALL DATA ON THIS DEVICE WILL BE DESTROYED")
        if self.args.yes:
            return
        response = input(f"Type {device} to continue: ").strip()
        if response != os.fspath(device):
            raise FlowError("SD deployment cancelled")

    def unmount_children(self, tree: dict[str, object]) -> None:
        for node in self.children(tree):
            if any(node.get("mountpoints") or []):
                self.command(["umount", str(node["path"])], sudo=True)

    def authenticate_sudo(self) -> None:
        print("\n== Authenticate privileged SD-card operations ==", flush=True)
        self.command(["-v"], sudo=True)

    def write_wic(self, device: Path) -> None:
        xz_command = ["xz", "-dc", os.fspath(WIC_IMAGE)]
        dd_command = [
            "sudo", "dd", f"of={device}", "bs=16M", "status=progress", "conv=fsync"
        ]
        print(f"+ {shlex.join(xz_command)} | {shlex.join(dd_command)}")
        if self.dry_run:
            return
        decompressor = subprocess.Popen(xz_command, stdout=subprocess.PIPE)
        assert decompressor.stdout is not None
        writer = subprocess.Popen(dd_command, stdin=decompressor.stdout)
        decompressor.stdout.close()
        writer_status = writer.wait()
        decompressor_status = decompressor.wait()
        if decompressor_status != 0 or writer_status != 0:
            raise FlowError(
                f"SD write failed: xz={decompressor_status}, dd={writer_status}"
            )

    def wait_for_partitions(self, device: Path) -> tuple[Path, Path, int]:
        for _ in range(20):
            tree = self.block_device_tree(device)
            by_label = {str(node.get("label")): node for node in self.children(tree)}
            if "boot" in by_label and "root" in by_label:
                root_path = Path(str(by_label["root"]["path"]))
                partition_file = Path("/sys/class/block") / root_path.name / "partition"
                try:
                    part_number = int(partition_file.read_text(encoding="ascii").strip())
                except (OSError, ValueError) as exc:
                    raise FlowError(
                        f"cannot read partition number from {partition_file}"
                    ) from exc
                return Path(str(by_label["boot"]["path"])), root_path, part_number
            time.sleep(0.5)
        raise FlowError("timed out waiting for boot/root partitions after SD write")

    @staticmethod
    def e2fs_tools() -> tuple[Path, Path]:
        base = PETALINUX_DIR / ".tmp" / "qcrate-kv260" / "sysroots-components" / "x86_64"
        e2fsck_matches = list(base.glob("e2fsprogs-native/sbin/e2fsck"))
        resize_matches = list(base.glob("e2fsprogs-native/sbin/resize2fs"))
        if len(e2fsck_matches) != 1 or len(resize_matches) != 1:
            raise FlowError("cannot locate the PetaLinux-native e2fsck and resize2fs")
        return e2fsck_matches[0], resize_matches[0]

    def expand_rootfs(self, device: Path, boot: Path, root: Path, part_number: int) -> None:
        print("\n== Check filesystems and expand persistent ext4 root ==")
        e2fsck, resize2fs = self.e2fs_tools()
        self.command(["fsck.vfat", "-a", boot], sudo=True)
        self.command([e2fsck, "-fy", root], sudo=True)
        self.command(["parted", "-s", device, "resizepart", str(part_number), "100%"],
                     sudo=True)
        self.command(["partprobe", device], sudo=True)
        self.unmount_children(self.block_device_tree(device))
        self.command([e2fsck, "-fy", root], sudo=True)
        self.command([resize2fs, root], sudo=True)
        self.command([e2fsck, "-fn", root], sudo=True)
        self.command(["fsck.vfat", "-n", boot], sudo=True)

    def install_bootfw_on_card(self, root_partition: Path) -> None:
        print("\n== Install audited boot firmware into the SD root filesystem ==")
        mount_dir = Path(tempfile.mkdtemp(prefix="qcrate-sd-root-", dir="/tmp"))
        mounted = False
        try:
            self.command(["mount", root_partition, mount_dir], sudo=True)
            mounted = not self.dry_run
            target_home = mount_dir / "home" / "petalinux"
            target_dir = target_home / "qcrate" / "bootfw"
            self.command(["install", "-d", target_dir], sudo=True)
            self.command(
                ["install", "-m", "0644", BOOTFW_IMAGE, BOOTFW_CHECKSUM, target_dir],
                sudo=True,
            )
            self.command(
                ["chown", "-R", "--reference", target_home, target_home / "qcrate"],
                sudo=True,
            )
            if not self.dry_run:
                installed_image = target_dir / BOOTFW_IMAGE.name
                installed_checksum = target_dir / BOOTFW_CHECKSUM.name
                self.verify_checksum(installed_checksum, installed_image)
                self.require_file(mount_dir / "usr" / "bin" / "qcrate-first-boot",
                                  "installed first-boot tool")
        finally:
            if mounted:
                self.command(["umount", mount_dir], sudo=True)
            mount_dir.rmdir()

    def deploy(self) -> None:
        print("\n== Deploy verified image to SD card ==")
        if self.args.device is None:
            raise FlowError("deploy/all requires --device /dev/<whole-device>")
        device = self.args.device.resolve()
        if not device.exists():
            raise FlowError(f"SD device does not exist: {device}")
        self.require_file(WIC_IMAGE, "compressed SD image")
        self.require_file(WIC_IMAGE.with_name(f"{WIC_IMAGE.name}.sha256"),
                          "SD-image checksum")
        self.require_file(BOOTFW_IMAGE, "A/B boot firmware")
        self.require_file(BOOTFW_CHECKSUM, "boot-firmware checksum")
        self.verify_checksum(WIC_IMAGE.with_name(f"{WIC_IMAGE.name}.sha256"), WIC_IMAGE)
        self.verify_checksum(BOOTFW_CHECKSUM, BOOTFW_IMAGE)

        tree = self.block_device_tree(device)
        self.confirm_device(device, tree)
        self.authenticate_sudo()
        self.unmount_children(tree)
        self.write_wic(device)
        self.command(["sync"])
        self.command(["partprobe", device], sudo=True)
        if self.dry_run:
            print("DRY RUN: partition discovery, expansion, and mounted-card audit skipped")
            return
        self.finalize_device(device)

    def finalize_device(self, device: Path) -> None:
        boot, root, part_number = self.wait_for_partitions(device)
        self.unmount_children(self.block_device_tree(device))
        self.expand_rootfs(device, boot, root, part_number)
        self.install_bootfw_on_card(root)
        self.command(["sync"])
        final_tree = self.block_device_tree(device)
        self.unmount_children(final_tree)
        print(f"INFO: deployment complete; {device} is unmounted and safe to remove")

    def finalize(self) -> None:
        print("\n== Resume SD post-write finalization ==")
        if self.args.device is None:
            raise FlowError("finalize requires --device /dev/<whole-device>")
        device = self.args.device.resolve()
        if not device.exists():
            raise FlowError(f"SD device does not exist: {device}")
        self.require_file(BOOTFW_IMAGE, "A/B boot firmware")
        self.require_file(BOOTFW_CHECKSUM, "boot-firmware checksum")
        self.verify_checksum(BOOTFW_CHECKSUM, BOOTFW_IMAGE)
        tree = self.block_device_tree(device)
        if tree.get("type") != "disk":
            raise FlowError(f"--device must name a whole disk, got {tree.get('type')!r}")
        for node in [tree, *self.children(tree)]:
            if "/" in [item for item in (node.get("mountpoints") or []) if item]:
                raise FlowError(f"refusing to modify the host root device: {device}")
        self.authenticate_sudo()
        self.unmount_children(tree)
        self.finalize_device(device)

    def run(self) -> None:
        stages = {
            "configure": (self.configure,),
            "build": (self.build,),
            "package": (self.package,),
            "deploy": (self.deploy,),
            "finalize": (self.finalize,),
            "all": (self.configure, self.build, self.package, self.deploy),
        }
        for action in stages[self.args.stage]:
            action()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=("configure", "build", "package", "deploy", "finalize", "all"),
        help="workflow stage to execute",
    )
    parser.add_argument(
        "--device",
        type=Path,
        help="whole SD-card block device; required for deploy/finalize/all",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the typed SD-device confirmation (intended for controlled automation)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print commands without changing the project or SD card",
    )
    parser.add_argument(
        "--vitis-settings",
        type=Path,
        default=DEFAULT_VITIS_SETTINGS,
        help=f"Vitis settings script (default: {DEFAULT_VITIS_SETTINGS})",
    )
    parser.add_argument(
        "--petalinux-settings",
        type=Path,
        default=DEFAULT_PETALINUX_SETTINGS,
        help=f"PetaLinux settings script (default: {DEFAULT_PETALINUX_SETTINGS})",
    )
    parser.add_argument(
        "--bsp",
        type=Path,
        help="KV260 SDT BSP used only to reconstruct ignored fresh-clone metadata",
    )
    args = parser.parse_args()
    if args.stage in {"deploy", "finalize", "all"} and args.device is None:
        parser.error(f"{args.stage} requires --device /dev/<whole-device>")
    return args


def main() -> int:
    try:
        Flow(parse_args()).run()
    except (FlowError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
