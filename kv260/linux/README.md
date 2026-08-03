# KV260 Linux bring-up: Ubuntu and xmutil chapter

> **Status: closed learning chapter.** The APB path was proven successfully on
> hardware. Preserve these commands for reference and diagnosis, but use the
> fixed PetaLinux flow in `petalinux/README.md` for new Q-Crate platform work.

This chapter records the first A53 Linux-side bring-up of the Q-Crate hardware
on AMD's Kria Ubuntu image. It intentionally explored both direct FPGA Manager
loading and the Kria `xmutil` packaging flow before the project committed to a
fixed boot platform.

## Chapter result

The experiment established that:

- the XSA-derived `xmutil` package can load the Q-Crate bitstream and overlay;
- Linux can read every APB register and write `SYS.SCRATCH` correctly;
- the reset failure that originally hung AXI reads was observable with ILA and
  was corrected in the exported BD Tcl;
- `/dev/mem` is useful for a first physical-address smoke test but is not the
  final software interface;
- the stock Kria Ubuntu platform retained 100 MHz for both PS fabric clocks,
  while Q-Crate requires 200 MHz stream and 100 MHz control clocks;
- a runtime-loaded application must match the clock contract of its booted base
  platform, or use a platform-supported runtime clock-management mechanism.

This does not make `xmutil` defective. It is useful for Kria applications built
against a compatible base-platform contract. Q-Crate instead proceeds with a
fixed PetaLinux boot platform so its PS initialization, bitstream, device tree,
and software are generated from one hardware handoff.

## Historical test target

The completed experiment used:

```text
Kria Ubuntu 22.04 on KV260
Q-Crate bitstream loaded into PL
APB registers visible at 0xA0010000
AXI DMA registers visible at 0xA0000000
```

## Hardware addresses

These addresses come from `kv260/hw/bd/design_1.tcl`:

| Block | Physical base | Range |
|---|---:|---:|
| AXI DMA AXI-Lite | `0xA0000000` | `64 KiB` |
| Q-Crate APB window | `0xA0010000` | `64 KiB` |

Inside the Q-Crate APB window:

| Offset | Register |
|---:|---|
| `0x0000` | `SYS.DEVICE_ID`, expected `0x51435254` (`QCRT`) |
| `0x0004` | `SYS.VERSION`, expected `0x00000100` |
| `0x0010` | `SYS.SCRATCH`, read/write smoke-test register |
| `0x1004` | `STREAM.FRAME_LENGTH` |
| `0x1008` | `STREAM.FRAME_COUNT` |
| `0x100C` | `STREAM.STREAM_MODE` |
| `0x1010` | `STREAM.STATUS` |

## Create the xmutil package

From the development machine, create the local firmware package:

```bash
python3 scripts/package_xmutil.py
```

This default path uses `xsct -nodisp` and AMD/Xilinx `createdts` to generate the device-tree overlay from:

```text
build/artifacts/qcrate_kv260.xsa
```

Within this closed Kria Ubuntu experiment, that was the preferred packaging path
because the overlay was derived from the exact Vivado hardware handoff.

By default, the package script creates a load-only overlay. It keeps the XSA-generated bitstream, fabric-clock, reset, and AFI configuration, but removes Linux driver probe nodes such as AXI DMA and ZOCL. This is intentional for first bring-up: `xmutil loadapp` should configure PL without any kernel driver touching AXI registers before the APB smoke test.

This creates:

```text
build/xmutil/qcrate_kv260/qcrate_kv260.bit.bin
build/xmutil/qcrate_kv260/qcrate_kv260.dtbo
build/xmutil/qcrate_kv260/shell.json
```

If `build/artifacts/qcrate_kv260.bit` is newer than
`build/artifacts/qcrate_kv260.bit.bin`, the package script regenerates the
`.bit.bin` with `bootgen` before copying it into the xmutil package. This avoids
accidentally testing an old bitstream after a Vivado rebuild.

If `createdts` needs to clone `device-tree-xlnx`, it may require network access. To force a local device-tree repo, use:

```bash
python3 scripts/package_xmutil.py --dtg-repo /path/to/device-tree-xlnx
```

Example with a local cached DTG repo:

```bash
python3 scripts/package_xmutil.py \
  --dtg-repo /path/to/device-tree-xlnx \
  --keep-dts
```

The generated source is kept under:

```text
build/xmutil/createdts/qcrate_kv260/psu_cortexa53_0/device_tree_domain/bsp/pl-load-only.dtsi
```

The unfiltered XSA-generated source is also kept next to it as `pl.dtsi` when `--keep-dts` is used.

Later, when the basic APB and DMA paths are proven, build a package that lets Linux probe generated kernel driver nodes:

```bash
python3 scripts/package_xmutil.py --probe-kernel-drivers
```

There is also a manual load-only fallback overlay:

```text
kv260/linux/xmutil/qcrate_kv260.dtsi
```

Use it only for debugging or comparison. Unlike the raw `createdts` output,
this overlay places the AFI and fabric-clock nodes under `&amba`, matching the
layout used by AMD's Kria application firmware. It deliberately omits DMA and
ZOCL driver nodes during first bring-up.

```bash
python3 scripts/package_xmutil.py --use-manual-overlay
```

## Copy artifacts to the KV260

From the development machine:

```bash
ssh ubuntu@<kv260-ip> 'mkdir -p ~/qcrate'
scp -r build/xmutil/qcrate_kv260 ubuntu@<kv260-ip>:~/qcrate/
scp kv260/linux/qcrate_apb.py ubuntu@<kv260-ip>:~/qcrate/
```

Use your actual KV260 hostname or IP address in place of `<kv260-ip>`.

## Install and load the xmutil app

On the KV260:

```bash
sudo mkdir -p /lib/firmware/xilinx
sudo rm -rf /lib/firmware/xilinx/qcrate_kv260
sudo cp -r ~/qcrate/qcrate_kv260 /lib/firmware/xilinx/
```

The installed firmware directory should contain:

```bash
find /lib/firmware/xilinx/qcrate_kv260 -maxdepth 1 -type f | sort
```

Expected files:

```text
/lib/firmware/xilinx/qcrate_kv260/qcrate_kv260.bit.bin
/lib/firmware/xilinx/qcrate_kv260/qcrate_kv260.dtbo
/lib/firmware/xilinx/qcrate_kv260/shell.json
```

`xmutil listapps` reports applications known to `dfx-mgrd`. It does not list loose `.bit` files. For Q-Crate, the app name is:

```text
qcrate_kv260
```

Load it:

```bash
sudo xmutil listapps
sudo xmutil unloadapp
sudo xmutil loadapp qcrate_kv260
sudo xmutil listapps
dmesg --human | tail -80
```

Expected result:

```text
qcrate_kv260 appears in listapps and has an active slot after loadapp.
```

## If `xmutil loadapp` freezes

If the board becomes unresponsive during:

```bash
sudo xmutil loadapp qcrate_kv260
```

then the failure happened before the APB Python smoke test. First make sure the
installed package is the latest package generated on the development PC:

```bash
find /lib/firmware/xilinx/qcrate_kv260 -maxdepth 1 -type f -ls
sha256sum /lib/firmware/xilinx/qcrate_kv260/*
```

On the development PC, compare against:

```bash
find build/xmutil/qcrate_kv260 -maxdepth 1 -type f -ls
sha256sum build/xmutil/qcrate_kv260/*
```

The hashes must match exactly.

The default Q-Crate package should be a load-only overlay. It should not contain
`dma@...` or `zyxclmm_drm` nodes, because those nodes can make Linux drivers
touch PL AXI registers during `loadapp`.

On the development PC, check the generated overlay:

```bash
dtc -I dtb -O dts build/xmutil/qcrate_kv260/qcrate_kv260.dtbo | grep -E 'firmware-name|clocking|afi|dma@|zyxclmm'
```

Expected for first bring-up:

```text
firmware-name = "qcrate_kv260.bit.bin";
clocking0
clocking1
afi0
```

No `dma@...` or `zyxclmm_drm` text should appear until the basic APB path has
been proven.

After rebooting from a freeze, check whether the previous boot left useful
kernel logs:

```bash
journalctl -b -1 -k --no-pager | tail -120
```

If the load-only overlay still freezes, rebuild Vivado from the corrected BD Tcl
and then regenerate the xmutil package:

```bash
python3 scripts/build.py --stage all
python3 scripts/package_xmutil.py --dtg-repo /path/to/device-tree-xlnx --keep-dts
```

Then reinstall the package on the KV260 and retry `xmutil loadapp`.

On Kria Ubuntu, a normal `xmutil` application has a firmware directory such as:

```text
/lib/firmware/xilinx/<app_name>/
```

and includes at least:

```text
<app_name>.bit.bin
<app_name>.dtbo
shell.json
```

An XRT/Vitis accelerator also normally includes an `.xclbin`. Q-Crate is not an XRT accelerator yet, so the `.xclbin` should not be required for the first APB smoke test.

Useful inspection commands:

```bash
which xmutil
sudo xmutil listapps
dmesg --human | tail -80
find /lib/firmware/xilinx/qcrate_kv260 -maxdepth 1 -type f -ls
```

## Historical fast bitstream load

This section records the low-level Linux FPGA-manager path we tried before moving to `xmutil`. Keep it as a debugging reference, but do not use it as the normal Q-Crate flow.

First, unload any currently active Kria application. Your old YOLO accelerator may still be loaded in slot 0.

```bash
sudo xmutil listapps
sudo xmutil unloadapp
```

Check whether `fpgautil` is installed:

```bash
which fpgautil
fpgautil -h
```

If it exists, try loading the Q-Crate bitstream directly:

```bash
sudo fpgautil -b "$HOME/qcrate/qcrate_kv260.bit"
dmesg --human | tail -80
```

Expected success text is similar to:

```text
BIN FILE loaded through zynqMP FPGA manager successfully
```

Some images want a headerless `.bit.bin` file instead of a raw Vivado `.bit`. If raw `.bit` loading fails with a bitstream-format error, convert the bitstream on the development PC:

```bash
cat > /tmp/qcrate_bitstream.bif <<'EOF'
all:
{
  [destination_device = pl] build/artifacts/qcrate_kv260.bit
}
EOF

source /tools/Xilinx/Vivado/2024.2/settings64.sh
bootgen -image /tmp/qcrate_bitstream.bif -arch zynqmp -o build/artifacts/qcrate_kv260.bit.bin -w
scp build/artifacts/qcrate_kv260.bit.bin ubuntu@<kv260-ip>:~/qcrate/
```

Then retry on the KV260:

```bash
sudo fpgautil -b "$HOME/qcrate/qcrate_kv260.bit.bin"
dmesg --human | tail -80
```

This fast path does not load a device-tree overlay. That is acceptable for the first `/dev/mem` APB test because the Python script directly maps the physical address. It is not the right final path for Linux drivers, UIO, DMA buffer ownership, or repeatable deployment.

If the board freezes when the APB script reads `0xA0010000`, stop using this bitstream and reboot. That symptom usually means the A53 issued an AXI transaction into PL and no clocked/reset-released slave returned a response. A userspace timeout cannot reliably save the system from that kind of bus hang.

Before a second fast-path attempt, check the PL clock assumptions:

```bash
sudo xmutil listapps
sudo awk 'NR <= 3 || /pl[0-3]_ref|fpga/' /sys/kernel/debug/clk/clk_summary
cat /sys/class/fpga_manager/fpga0/state
```

Q-Crate expects:

```text
pl0_ref: 200 MHz, stream and AXI infrastructure
pl1_ref: 100 MHz, APB/control domain
```

The Vivado BD source must also enable both PL clock buffers:

```text
CONFIG.PSU__PL_CLK0_BUF {TRUE}
CONFIG.PSU__PL_CLK1_BUF {TRUE}
```

## APB smoke test

The first Linux test deliberately uses `/dev/mem`. This is not the final production access method, but it is the fastest way to prove the hardware address map.

Install nothing. The script uses only Python standard library.

```bash
chmod +x ~/qcrate/qcrate_apb.py
sudo ~/qcrate/qcrate_apb.py read 0x0000
sudo ~/qcrate/qcrate_apb.py dump
sudo ~/qcrate/qcrate_apb.py smoke
```

Expected important lines:

```text
0x51435254
OK  DEVICE_ID                    0x51435254
OK  VERSION                      0x00000100
OK  STREAM_CLOCK_HZ              0x0bebc200
OK  CONTROL_CLOCK_HZ             0x05f5e100
PASS qcrate_apb_smoke
```

`STREAM_CLOCK_HZ` and `CONTROL_CLOCK_HZ` are hardware-contract constants stored
in the Q-Crate registers. They are not frequency counters and do not measure the
physical PS clock outputs. Verify real clock rates through the Linux clock
framework, an ILA/counter, or external measurement; do not use these register
values as evidence that the boot platform applied the requested clocks.

Single-register examples:

```bash
sudo ~/qcrate/qcrate_apb.py read 0x0000
sudo ~/qcrate/qcrate_apb.py write 0x0010 0x5a5aa5a5
sudo ~/qcrate/qcrate_apb.py read 0x0010
```

Do not start the stream engine until AXI DMA S2MM is armed. The smoke test writes stream configuration registers but intentionally does not write `CONTROL.START`.

## What failures mean

`Permission denied`:

```text
Run with sudo. /dev/mem requires root.
```

`No such file or directory: /dev/mem`:

```text
The kernel or image may restrict /dev/mem. We should switch to UIO or a small kernel driver path.
```

Read returns all zeros, all ones, or the process hangs:

```text
The bitstream may not be loaded, the PL clock/reset may be inactive, or the physical address may not match the hardware handoff.
```

`DEVICE_ID` is not `0x51435254`:

```text
Do not continue to DMA. First confirm the loaded bitstream is the Q-Crate bitstream and that the APB base is still 0xA0010000.
```

## SD-card boot triage

Try to avoid re-imaging until you know which layer is failing. A KV260 SD boot failure is usually one of these:

1. Power or carrier-board issue.
2. Boot-mode switch or SD-card seating.
3. Corrupted FAT boot partition.
4. Corrupted ext4 root filesystem after an unsafe power loss.
5. U-Boot environment or boot script points to missing files.
6. Image package mismatch after updates.
7. SD card wear or marginal card quality.

Fast checks:

```bash
# On the development PC, identify the SD card device.
lsblk -f

# Replace /dev/sdX1 and /dev/sdX2 with the actual boot and rootfs partitions.
sudo fsck.vfat -n /dev/sdX1
sudo fsck.ext4 -n /dev/sdX2

# Mount read-only and inspect obvious boot files.
mkdir -p /tmp/kv260_boot /tmp/kv260_root
sudo mount -o ro /dev/sdX1 /tmp/kv260_boot
sudo mount -o ro /dev/sdX2 /tmp/kv260_root
find /tmp/kv260_boot -maxdepth 2 -type f | sort | head -80
find /tmp/kv260_root/boot -maxdepth 2 -type f | sort | head -80
sudo umount /tmp/kv260_boot /tmp/kv260_root
```

Use `-n` first so `fsck` only reports. If it reports filesystem errors, rerun without `-n` only after you are ready to repair the card.

Best diagnostic if the board still does not boot:

```text
Connect the USB-UART serial console and capture power-on output.
```

The serial log tells us where boot stops:

| Last visible stage | Likely problem |
|---|---|
| No text at all | power, cable, terminal baud, boot mode, or board-level issue |
| FSBL/PMU messages only | boot partition files or handoff issue |
| U-Boot prompt | boot script, kernel, initrd, device tree, or rootfs path |
| Linux starts then drops/errors | rootfs corruption, failed services, bad update |

Serial settings are normally:

```text
115200 baud, 8 data bits, no parity, 1 stop bit, no flow control
```

On Linux PC:

```bash
ls /dev/ttyUSB* /dev/ttyACM*
sudo screen /dev/ttyUSB1 115200
```

If multiple serial ports appear, try each one. Exit `screen` with `Ctrl-A`, then `K`.

## SD-card filesystem notes

Do not run `fsck.ext4` on the whole SD-card device:

```bash
sudo fsck.ext4 -n /dev/mmcblk0
```

`/dev/mmcblk0` is the complete disk. It normally starts with a partition table, not an ext4 superblock, so `fsck.ext4` can print:

```text
Bad magic number in super-block
Found a dos partition table
```

That message does not prove the card is corrupted. Check the actual partitions instead:

```bash
lsblk -f

# For a card detected as /dev/mmcblk0:
sudo umount /media/$USER/system-boot
sudo umount /media/$USER/writable
sudo fsck.vfat -n /dev/mmcblk0p1
sudo fsck.ext4 -n /dev/mmcblk0p2
```

Use `-n` first. It means "do not modify, only report". If errors are reported, make a backup before repair:

```bash
sudo dd if=/dev/mmcblk0 of=~/kv260_sd_backup.img bs=64M status=progress conv=fsync
sudo fsck.vfat -a /dev/mmcblk0p1
sudo fsck.ext4 -f /dev/mmcblk0p2
```

Confirm the device name with `lsblk -f` before running `dd` or repair commands. A wrong device name can overwrite or repair the wrong disk.
