# Q-Crate PetaLinux platform

## Why this folder exists

The earlier Kria Ubuntu and `xmutil` work proved that the Q-Crate APB hardware
works, but it also exposed a platform-contract mismatch: the stock boot platform
kept both PS fabric clocks at 100 MHz while Q-Crate requires a 200 MHz stream
clock and a 100 MHz control clock.

This folder exists to build a Linux platform from the Q-Crate XSA so that one
hardware handoff defines:

- processing-system and PLL initialization;
- `pl_clk0=200 MHz` and `pl_clk1=100 MHz`;
- the PL bitstream loaded during boot;
- the static Linux device tree and interrupt/address map;
- the kernel and root-filesystem configuration;
- the SDK/sysroot used by Linux applications;
- the hardware basis for later R5 FreeRTOS/OpenAMP integration.

The initial PetaLinux bring-up is a learning gate, not a separate throwaway
platform. Its outputs feed the intended PetaLinux plus Vitis Q-Crate
deliverable.

## Status

```text
Current gate: fixed platform and one-frame DMA accepted; finite SG chain active
Accepted project: qcrate-kv260, based on the KV260 Starter Kit SDT BSP
Boot policy: XSA-matched QSPI boot chain plus FPGA Manager full-overlay loading
Validated here: Update 1, BSP, XSA-to-SDT import, platform build, WIC packaging,
                persistent rootfs, single PL owner, Q-Crate full-overlay load,
                A/B firmware update, 200/100 MHz clocks, APB smoke/dump,
                automated clean-SD deployment, staged target acceptance, and
                100 consecutive fully verified DMA captures
```

The accepted source project is `qcrate-kv260/`. It selects the
`xilinx-k26-kv` Yocto machine and preserves the KV260 carrier-card overlay. The
earlier generic `zynqmp-generic` experiment is retained, ignored by Git, as
`qcrate-kv260-xsct-import-probe/`. The image recorded below passed host-side
deployment checks but failed its first target boot because its root-device
argument used the development host's MMC number. It is not an accepted image.

The accepted SD image masks DFX manager, disables the Kria dashboard, and gives
`qcrate-pl-load.service` sole ownership of the PL overlay. The APB tool refuses
access while the required clocks or FPGA ownership are absent.

Do not add commands to this README merely because they look plausible. Add each
command when it is run, record the tool version, and state its observed output
or success condition. This keeps the document usable as a reproducible build
record rather than a collection of unverified snippets.

## Step 0: project origin and source boundary

`qcrate-kv260/project-spec/` was created once when the downloaded KV260 SDT BSP
was expanded into a PetaLinux project:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux

petalinux-create project \
  -s KV260_BSP_DOWLOADED/xilinx-kv260-starterkit-v2024.2-12072024.bsp \
  --tmpdir "$PWD/.tmp/qcrate-kv260"
mv xilinx-kv260-starterkit-2024.2 qcrate-kv260
```

That command created both source-like project policy and disposable tool
state. They have different version-control treatment:

| Path | Role | Git policy |
| --- | --- | --- |
| `project-spec/configs/` | Accepted platform, kernel-facing, BusyBox, and rootfs choices | Track |
| `project-spec/meta-user/` | Q-Crate recipes, services, patches, and DT additions | Track |
| `project-spec/dts_dir/` | KV260 carrier overlay source selected by the BSP | Track |
| `project-spec/hw-description/` | XSA/SDT copy made by `petalinux-config` | Regenerate |
| `.petalinux/` | Tool-version/project marker and local metadata | Regenerate |
| `build/`, `components/`, `images/`, `pre-built/` | Yocto workspace and outputs | Regenerate |

The initial `petalinux-config` and `petalinux-config -c rootfs` sessions wrote
their accepted selections into `project-spec/configs/config` and
`project-spec/configs/rootfs_config`. Q-Crate additions were then expressed as
recipes and configuration under `meta-user`. These files are now source code:
they are reviewable, diffable, and the inputs consumed by `petalinux-build`.

`petalinux_flow.py` deliberately does not recreate or overwrite this accepted
`project-spec` from BSP defaults on every build. Doing so would discard Q-Crate
policy and make results depend on interactive menu actions or a large fragile
sequence of scripted menu edits. On a fresh clone, `configure` uses the BSP
only to reconstruct ignored `.petalinux/metadata`; it then imports the current
XSA and applies the tracked project specification through silent configuration.
The BSP download itself remains untracked because it is a large external
tool-version input whose checksum and filename are recorded below.

## Primary automated workflow

The manual commands later in this document remain the diagnostic reference.
Normal reproduction uses two tracked Python tools and the focused Bootgen audit
script:

```text
scripts/petalinux_flow.py   host configure/build/package/SD deployment
scripts/package_bootfw.sh  checked Kria A/B BOOT.BIN generation
qcrate-first-boot          target A/B installation and platform acceptance
```

The host tool starts from the accepted Vivado XSA. It does not run Vivado. Its
`all` stage performs these operations in order:

1. Reconstruct ignored PetaLinux metadata from the downloaded BSP when needed.
2. Regenerate SDT and import it with `petalinux-config`.
3. Build and audit the root filesystem and boot components.
4. Package and verify `.wic.xz` and the A/B boot firmware.
5. Confirm the explicit whole-card device, write the image, check filesystems,
   expand ext4, copy the boot firmware, verify it on-card, and unmount.

Identify the card independently, then run the complete flow as an ordinary
host user. Do not run the entire Python process through `sudo`; it requests
privilege only for block-device and mount operations:

```bash
cd /tools/fpga_projects/qcrate
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,FSTYPE,LABEL,MOUNTPOINTS
python3 kv260/linux/petalinux/scripts/petalinux_flow.py all \
  --device /dev/mmcblk0
```

The tool displays the selected device and requires its complete path to be
typed before erasing it. `--yes` exists for controlled automation and should
not be used during interactive development. Inspect commands without changing
the project or card with:

```bash
python3 kv260/linux/petalinux/scripts/petalinux_flow.py configure --dry-run
python3 kv260/linux/petalinux/scripts/petalinux_flow.py package --dry-run
```

Individual resumable stages are also available:

```bash
python3 kv260/linux/petalinux/scripts/petalinux_flow.py configure
python3 kv260/linux/petalinux/scripts/petalinux_flow.py build
python3 kv260/linux/petalinux/scripts/petalinux_flow.py package
python3 kv260/linux/petalinux/scripts/petalinux_flow.py deploy \
  --device /dev/mmcblk0
```

If power, media removal, or a host-tool incompatibility interrupts `deploy`
after `dd` completes, do not rewrite the image. Resume filesystem expansion,
boot-firmware installation, verification, and unmounting with:

```bash
python3 kv260/linux/petalinux/scripts/petalinux_flow.py finalize \
  --device /dev/mmcblk0
```

Run deployment and finalization from an interactive terminal. The script calls
`sudo -v` once before privileged media operations so authentication completes
before filesystem checks or resizing begins.

`configure` uses the single BSP under `KV260_BSP_DOWLOADED/` only when ignored
`.petalinux/metadata` is absent. Use `--bsp /path/to/file.bsp` if the BSP is
stored elsewhere. Generated metadata, Yocto state, images, and the BSP remain
outside Git.

SDT is generated under a randomized `qcrate-kv260-sdt-*/qcrate-kv260`
staging path. The script audits that tree and atomically promotes it to
`build/petalinux/sdt/qcrate-kv260`; a failed staging tree is deleted. Vitis
2024.2 may print `expected integer but got "33.3333333"` while reading the
XSA's 33.3333333 MHz PS reference-clock property. This diagnostic was observed
on the accepted XSA and is non-fatal when it is followed by `INFO: generated
KV260 SDT`; the script independently checks PS clock programming, the system
address map, PL clocks, and the DMA node before importing the result.

On the first target boot, write the checked firmware into the inactive A/B
slot and shut Linux down:

```bash
sudo qcrate-first-boot install --poweroff
```

Remove board power after shutdown, wait several seconds, and boot again. The
candidate is marked bootable only after loader ownership, FPGA state, overlay,
200/100 MHz clocks, read-only APB, APB writes, and register dump all pass:

```bash
sudo qcrate-first-boot accept
```

Later boots and SD cards normally need no QSPI write. Run the non-destructive
platform regression instead:

```bash
sudo qcrate-first-boot test
```

### Commit boundary

Add only the human-authored project inputs. This targeted command avoids UART
logs, downloaded BSP contents, build products, and unrelated private files:

```bash
cd /tools/fpga_projects/qcrate
git add \
  kv260/linux/petalinux/.gitignore \
  kv260/linux/petalinux/README.md \
  kv260/linux/petalinux/scripts \
  kv260/linux/petalinux/qcrate-kv260/project-spec
git status --short
git diff --cached --check
```

## Stage 1: fixed-platform acceptance

The first PetaLinux milestone is complete only when all of these are true:

1. PetaLinux 2024.2 accepts `build/artifacts/qcrate_kv260.xsa`.
2. The project builds Linux, device tree, root filesystem, and an audited
   A/B QSPI update without relying on GUI state.
3. The selected KV260 boot strategy loads the matching Q-Crate bitstream before
   any PL device is accessed; the artifact and load point are documented.
4. The KV260 boots from a separate SD card with serial-console logs captured.
5. Linux reports `pl_clk0` at 200 MHz and `pl_clk1` at 100 MHz before any
   Q-Crate userspace program runs.
6. `DEVICE_ID`, `VERSION`, and `SYS.SCRATCH` pass repeated APB tests.
7. Rebooting produces the same result without manual `xmutil`, FPGA Manager, or
   register-write commands.
8. The project can export an SDK/sysroot suitable for an A53 Linux application.

DMA is intentionally outside this first acceptance gate. Boot and control-path
correctness must be stable before adding DMA buffer ownership, cache coherency,
and driver questions.

The next milestone is implemented and maintained separately in
[`../dma/README.md`](../dma/README.md). It adds a receive-only DMAEngine client,
a coherent kernel buffer, and a userspace verifier without weakening this
fixed-platform acceptance boundary.

## Stage 2: software platform deliverable

After fixed-platform acceptance, the same PetaLinux project supplies the Linux
artifacts for a Vitis platform:

```text
Q-Crate XSA
  + PetaLinux kernel, root filesystem, boot files, and sysroot
        |
        v
Vitis Q-Crate platform
  - A53 Linux domain and userspace application
  - later R5 FreeRTOS domain
  - later OpenAMP resource table and shared-memory contract
```

Vitis is useful for managing the combined A53/R5 software platform. It is not a
requirement for every Linux executable: ordinary A53 applications may also be
built reproducibly with the PetaLinux SDK and the repository's normal build
scripts.

## Optional local graphical image

The accepted Q-Crate image is intentionally a headless embedded instrument,
administered through UART and SSH. This keeps the image, boot time, update
surface, and failure modes small, and leaves display buffers, CMA memory, DMA
bandwidth, and PL resources available while the acquisition path is being
proved. A keyboard and mouse add little to that workflow without a local
display session.

An embedded graphical variant is possible, but it is a separate platform
profile rather than an automatic consequence of Linux booting. It would need:

1. **Hardware and device tree:** enable and verify the KV260 PS DisplayPort
   pipeline and its carrier DP/HDMI output path. This is the conservative local
   desktop choice. A separate PL HDMI pipeline is relevant only on hardware
   with the required video-PHY and connector routing, and would add video
   mixer/formatter, timing, PHY, clocks, AXI, and device-tree nodes.
2. **Kernel:** DRM/KMS display, ZynqMP display, framebuffer/console as desired,
   Mali GPU support if accelerated rendering is required, USB host, USB HID,
   input/evdev, and sufficient CMA.
3. **Root filesystem:** `libdrm`, Wayland/Weston or X11, a compositor/window
   manager, input libraries such as `libinput`, fonts, terminal/application
   packages, and optional `usbutils`. The current project explicitly leaves
   `packagegroup-core-weston`, `packagegroup-core-x11`,
   `packagegroup-xilinx-matchbox`, Qt groups, `libdrm`, and `usbutils` disabled.
4. **System integration:** launch the graphical session with systemd, define
   users and permissions for `/dev/dri` and `/dev/input`, test EDID/hot-plug,
   keyboard and mouse enumeration, resolution, rendering, shutdown, and memory
   pressure alongside Q-Crate DMA.

PetaLinux/Yocto can produce that graphical embedded image, but selecting a few
packages does not turn it into Ubuntu. An Ubuntu-like desktop also implies a
larger distribution policy, package manager/repositories, desktop services,
and long-term update strategy. If Q-Crate later needs a local operator panel,
a small Weston or Qt kiosk image is a better first embedded profile than a
general-purpose desktop; the accepted headless profile should remain available
for deployment and recovery.

## Repository policy

Track small human-authored platform inputs, including:

- this README and subsequent command/run notes;
- `project-spec/configs` changes;
- `project-spec/meta-user` recipes, patches, and device-tree additions;
- repository scripts that create, configure, build, package, or deploy the
  platform.

Do not track generated PetaLinux build trees, downloaded Yocto state, generated
images, copied XSAs, SDK installers, or boot binaries. The local `.gitignore`
protects this folder before project creation.

## Documentation update rule

Update this README after each accepted step with:

```text
Purpose
Exact command
Expected result
Observed result
Generated artifacts
Failure diagnosis, if any
Next step
```

When a command is replaced, retain the lesson but clearly mark the obsolete
command as historical. The first screen of this README must always state the
current milestone and whether the documented image is safe to deploy.

## Recorded bring-up: 2026-08-05

### Toolchain and host preflight

The installed toolchain is PetaLinux 2024.2:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
printf 'PetaLinux %s\n' "$PETALINUX_VER"
```

Observed result:

```text
PetaLinux 2024.2
```

The host is Ubuntu 22.04.5 LTS with 8 CPU cores, 31 GiB RAM, 2 GiB swap, and
about 1.6 TiB free on the ext4 `/tools` filesystem. `/bin/sh` resolves to Bash,
and the PetaLinux environment check reported no missing required host tools or
libraries.

PetaLinux 2024.2 officially lists Ubuntu 22.04.1 through 22.04.4, not 22.04.5.
This is therefore an explicit learning-host exception, not a supported-host
claim. Reproduce release or CI builds on an AMD-listed host if a later failure
could be host-version dependent. See the
[PetaLinux installation requirements](https://docs.amd.com/r/2024.2-English/ug1144-petalinux-tools-reference-guide/Installation-Requirements).

`/tmp` had only about 24 GiB free, so PetaLinux/Yocto temporary state is kept on
the project filesystem:

```bash
mkdir -p /tools/fpga_projects/qcrate/kv260/linux/petalinux/.tmp
export TMPDIR=/tools/fpga_projects/qcrate/kv260/linux/petalinux/.tmp
```

The no-TFTP-server warning is expected for this SD-card workflow.

### Hardware-handoff verification

Inputs:

```text
build/artifacts/qcrate_kv260.xsa
build/artifacts/qcrate_kv260.bit
```

Recorded SHA-256 values:

```text
XSA: be54b6102f64c591dca72231d957815e77f9880b2f94149e6316b2a47b0559e7
BIT: 05aa033c7468d5912abe693bfd08022e3124fce7fbff86105b77cf2a4c7b19cf
```

The bitstream embedded in the XSA has the same BIT hash. The XSA also contains
the HWH and generated PS initialization files. Inspection of `psu_init.c`
confirmed the required clock contract:

```text
PL0_REF_CTRL = 0x01010203: DPLL, divisors 2 and 1, enabled -> 200 MHz
PL1_REF_CTRL = 0x01010403: DPLL, divisors 4 and 1, enabled -> 100 MHz
```

This establishes that the Vivado hardware handoff contains the requested
clocks. The failed factory-firmware experiment later proved that a Linux full
overlay alone cannot reliably replace this pre-Linux PS initialization. The
accepted flow therefore executes the XSA-generated FSBL from an updated QSPI
A/B slot.

### Generic-template import experiment

The following commands successfully created a generic ZynqMP project skeleton
and imported the Q-Crate XSA:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
export TMPDIR=/tools/fpga_projects/qcrate/kv260/linux/petalinux/.tmp

cd /tools/fpga_projects/qcrate/kv260/linux/petalinux
petalinux-create project -n qcrate-kv260 --template zynqMP \
  --tmpdir "$TMPDIR"

cd qcrate-kv260
petalinux-config --get-hw-description \
  /tools/fpga_projects/qcrate/build/artifacts/qcrate_kv260.xsa \
  --silentconfig
```

Observed result:

```text
[INFO] Successfully configured project
```

The resulting configuration is AArch64 but uses machine `template`, Yocto
machine `zynqmp-generic`, an initrd root filesystem, and no FPGA Manager. This
proves the tools can consume the XSA; it does not supply all KV260 carrier,
boot-media, and image-layout policy.

When run through Codex's restricted execution sandbox, BitBake initially could
not bind its local Unix socket and returned `PermissionError: [Errno 1]
Operation not permitted`. Running the same import outside that sandbox
completed. This was an execution-sandbox restriction, not an XSA or PetaLinux
error.

### Platform-base decision

Three technically distinct starting points exist:

1. **KV260 Starter Kit SDT BSP (recommended).** Start from AMD's current,
   carrier-aware reference project. Generate a System Device Tree from the
   accepted Q-Crate XSA with `sdtgen`, then import that SDT directory into
   PetaLinux. The XSA remains Vivado's hardware handoff; SDT is the richer
   software-facing description generated from it. This also aligns with the
   later A53/R5/OpenAMP platform work.
2. **Generic ZynqMP template.** Manually recreate the KV260 machine, carrier,
   boot, and SD-image policy. This duplicates vendor integration and adds risk
   without advancing the Q-Crate objectives.
3. **KV260 Starter Kit XSCT BSP.** Import the XSA directly using the legacy
   generation flow. This is the lower-change fallback for upgrading an older
   PetaLinux project, but AMD recommends SDT for new 2024.2 designs.

Option 1 was selected. The PetaLinux 2024.2 KV260 BSPs require the 2024.2 eSDK
Update 1, and the approximately 1017 MiB KV260 Starter Kit SDT BSP was used.
AMD documents both BSP variants on the
[2024.2 embedded tools download page](https://www.xilinx.com/support/download.html/content/xilinx/en/downloadNav/embedded-design-tools/2024-2.html).

The SDT flow does not discard or bypass the XSA. AMD's `sdtgen` reads the XSA,
extracts its PS initialization and bitstream, and generates the hardware-wide
device-tree description. PetaLinux then consumes the SDT directory instead of
reading the XSA directly. See the
[PetaLinux 2024.2 flow introduction](https://docs.amd.com/r/2024.2-English/ug1144-petalinux-tools-reference-guide/Introduction)
and the
[SDT generator repository](https://github.com/Xilinx/system-device-tree-xlnx).

## Accepted SDT project creation: 2026-08-05

### 1. Verify eSDK Update 1

The AArch64 eSDK update was installed by the user before the BSP project was
created. The exact local update command was not captured in this session; the
canonical offline command shape is:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
petalinux-upgrade -f <directory-containing-the-update1-aarch64-payload> \
  --platform aarch64
```

The useful local verification is the updater log, because `.version-history`
continues to identify the 2024.2 base release:

```bash
sed -n '1,160p' /tools/Xilinx/PetaLinux/2024.2/update.log
```

Observed result:

```text
[INFO] Start updating aarch64 eSDK
[INFO] aarch64 eSDK is upto date
```

`update.log.old` records an earlier failed attempt. The current log and the
later successful project-local SDK refresh establish which attempt won.

### 2. Verify the downloaded BSP

The downloaded BSP is intentionally excluded from Git:

```text
kv260/linux/petalinux/KV260_BSP_DOWLOADED/
  xilinx-kv260-starterkit-v2024.2-12072024.bsp
```

Check its type, size, and checksums before extraction:

```bash
cd /tools/fpga_projects/qcrate

file kv260/linux/petalinux/KV260_BSP_DOWLOADED/*.bsp
ls -lh kv260/linux/petalinux/KV260_BSP_DOWLOADED/*.bsp
md5sum kv260/linux/petalinux/KV260_BSP_DOWLOADED/*.bsp
sha256sum kv260/linux/petalinux/KV260_BSP_DOWLOADED/*.bsp
```

Observed result:

```text
Type: XZ compressed data, CRC64
Size: 1017 MiB compressed, about 2.1 GiB uncompressed
MD5: 14d0453f2bfffe8aaa15ba9d06a250a2
SHA-256: cb641471e90258184db6c18e98ae0c8cd2c999c59f1cd001a95708979684edbb
```

The MD5 exactly matches AMD's published value. SHA-256 is recorded here as the
stronger local provenance value.

To inspect an XZ-compressed BSP without relying on its `.bsp` suffix:

```bash
xz -dc KV260_BSP_DOWLOADED/xilinx-kv260-starterkit-v2024.2-12072024.bsp \
  | tar -tf - | less
```

This lists the project without extracting it. Do not pipe through `head` when
testing archive integrity, because terminating the reader early gives `xz` a
broken pipe rather than checking the full stream.

### 3. Create the project from the BSP

Each live PetaLinux project must have its own Yocto `TMPDIR`. Sharing one
between projects can corrupt or confuse build state. The first extraction used
the parent `.tmp` directory; this was corrected before SDT import. Use the
following form on a fresh recreation:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux

mkdir -p .tmp/qcrate-kv260
petalinux-create project \
  -s KV260_BSP_DOWLOADED/xilinx-kv260-starterkit-v2024.2-12072024.bsp \
  --tmpdir "$PWD/.tmp/qcrate-kv260"
```

The BSP chooses its own extracted directory name. Rename it once to the stable
repository project name:

```bash
mv xilinx-kv260-starterkit-2024.2 qcrate-kv260
```

Observed result:

```text
[INFO] New project successfully created
```

The original generic-template experiment was preserved as
`qcrate-kv260-xsct-import-probe/`; it is not part of the accepted build.

### 4. Generate SDT from the Q-Crate XSA

The checked-in Tcl script fixes the board description to
`zynqmp-smk-k26-reva`, validates its arguments, and refuses to overwrite an
existing output directory:

```bash
cd /tools/fpga_projects/qcrate
mkdir -p build/petalinux/sdt
source /tools/Xilinx/Vitis/2024.2/settings64.sh

xsct -nodisp \
  kv260/linux/petalinux/scripts/generate_sdt.tcl \
  build/artifacts/qcrate_kv260.xsa \
  build/petalinux/sdt/qcrate-kv260
```

`-nodisp` matters: without it, the 2024.2 XSCT launcher attempts to start Xvfb
and D-Bus even though SDT generation is a batch operation.

Observed result:

```text
INFO: generated KV260 SDT in .../build/petalinux/sdt/qcrate-kv260
```

SDTGen also printed:

```text
expected integer but got "33.3333333"
```

This refers to the PS reference-clock property emitted in `pcw.dtsi` as a
decimal string. AMD's installed SDT samples use the same string representation.
The output was accepted only after all required files were present, the BSP
import succeeded, and the hardware checks below passed.

### 5. Audit the generated SDT

Confirm that the SDT contains the Q-Crate address map, board include, and fabric
clock assignments:

```bash
rg -n 'a0000000|a0010000|zynqmp-smk-k26|assigned-clock-rates' \
  build/petalinux/sdt/qcrate-kv260/{system-top.dts,pl.dtsi}
```

Expected clock properties in `pl.dtsi`:

```text
clocking0 / PL0: assigned-clock-rates = <200000000>
clocking1 / PL1: assigned-clock-rates = <100000000>
```

Confirm that SDTGen extracted the exact accepted bitstream:

```bash
sha256sum \
  build/artifacts/qcrate_kv260.bit \
  build/petalinux/sdt/qcrate-kv260/qcrate_kv260.bit
```

Both files produced:

```text
05aa033c7468d5912abe693bfd08022e3124fce7fbff86105b77cf2a4c7b19cf
```

The generated `psu_init.c` also retains:

```text
PL0_REF_CTRL = 0x01010203: 200 MHz from DPLL
PL1_REF_CTRL = 0x01010403: 100 MHz from DPLL
```

### 6. Import SDT into the KV260 BSP project

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux/qcrate-kv260

petalinux-config --get-hw-description \
  /tools/fpga_projects/qcrate/build/petalinux/sdt/qcrate-kv260 \
  --silentconfig
```

On the first import after Update 1, PetaLinux reported that the tool eSDK had
changed and asked whether to refresh `components/yocto`. Answering `y` is the
correct choice; using the BSP's older project-local SDK would defeat the tool
update.

Observed final result:

```text
[INFO] Successfully configured project
```

The brief `Bitbake is not available` message occurred while the old
project-local SDK was being replaced. BitBake and PRServer started normally
later in the same command.

### 7. Accepted post-import state

The configuration now establishes:

```text
PetaLinux flow:       SDT
Yocto machine:        xilinx-k26-kv
Primary SD device:    SDHCI1
Carrier DT overlay:   zynqmp-sck-kv-g-revB.dtso
PL0 assigned rate:    200000000 Hz
PL1 assigned rate:    100000000 Hz
DMA register range:   0xA0000000..0xA000FFFF
Q-Crate APB range:    0xA0010000..0xA001FFFF
Root filesystem:      persistent ext4 on SD partition 2
FPGA Manager:         full PL overlay, loaded automatically by systemd
```

The generated/copy-only directories remain ignored: `build/`, `components/`,
`pre-built/`, `hardware/`, `images/`, and `project-spec/hw-description/`.
Version control retains the PetaLinux configuration, carrier DT source,
`meta-user`, and the SDT-generation script. `.petalinux/metadata` is generated
and excluded because it records a workstation-specific absolute hardware path.

## KV260 boot boundary

The KV260 starter kit is not a conventional SD-primary ZynqMP board. Factory
boot mode is QSPI, which contains FSBL, PMU firmware, TF-A, and U-Boot in A/B
firmware partitions. U-Boot then loads Linux from the secondary SD device.
Consequently, placing another `BOOT.BIN` on SD does not automatically execute
its FSBL or initialize the PS from that file.

AMD's standard Kria application model keeps the QSPI boot firmware independent
of applications and loads a bitstream plus matching device-tree overlay after
Linux starts. A custom QSPI `BOOT.BIN` is supported, but it is a boot-firmware
update with A/B recovery considerations, not ordinary SD-image deployment. See
the [Kria boot-firmware overview](https://xilinx.github.io/kria-apps-docs/bootfw/build/html/docs/bootfw_overview.html)
and [Kria bitstream management](https://xilinx.github.io/kria-apps-docs/creating_applications/2022.1/build/html/docs/bitstream_management.html).

The accepted policy keeps the same split but replaces the inactive QSPI A/B
slot through `xmutil bootfw_update` with XSA-matched FSBL, PMUFW, TF-A, DTB,
and U-Boot. The Q-Crate bitstream remains an SD-rootfs artifact loaded by Linux
FPGA Manager. This executes the required PS initialization during early boot
without coupling each PL application build to QSPI provisioning.

The factory-firmware attempt remains an important boundary test: importing an
XSA or applying a Linux overlay does not execute `psu_init.c`. Early PS clocks,
DDR, MIO, and reset setup belong to the boot-firmware contract; the device tree
then describes and manages the resulting hardware for Linux.

## Accepted fixed-platform configuration

### Platform choices

The checked-in PetaLinux configuration now selects:

```text
CONFIG_SUBSYSTEM_FPGA_MANAGER=y
CONFIG_SUBSYSTEM_PL_DT_OVERLAY_FULL=y
CONFIG_SUBSYSTEM_ROOTFS_EXT4=y
CONFIG_SUBSYSTEM_HOSTNAME="qcrate-kv260"
CONFIG_SUBSYSTEM_PRODUCT="Q-Crate KV260"
```

`full` is the correct overlay type because Q-Crate programs the complete PL.
`dfx` is for a static shell with reconfigurable partitions and is not this
architecture. The first generated ext4 boot argument was incorrect for the
KV260 runtime:

```text
root=/dev/mmcblk0p2 ro rootwait
```

The host named the removable card `/dev/mmcblk0` while it was attached to the
development PC, but device names are local to each Linux system. On the KV260,
the carrier SD controller has device-tree alias `mmc1`; the first UART capture
proved that the card appears as `/dev/mmcblk1`:

```text
mmc1: SDHCI controller on ff170000.mmc
mmcblk1: mmc1:544c MSSD0 58.3 GiB
mmcblk1: p1 p2
```

The kernel therefore waited forever for nonexistent `/dev/mmcblk0p2`, before
mounting rootfs or starting systemd. The accepted target boot argument is:

```text
root=/dev/mmcblk1p2 ro rootwait
```

`CONFIG_SUBSYSTEM_SDROOT_DEV` now records `/dev/mmcblk1p2`. References to
`/dev/mmcblk0` in the SD-writing section remain correct for the recorded host
card reader and must always be rechecked with `lsblk` before use.

The initial kernel mount may be read-only; systemd remounts the normal ext4
root according to `/etc/fstab`. On the target, verify the final mount is
writable with `findmnt`, as shown below.

The copied BSP originally forced `ramdisk.cpio.gz.u-boot` into
`IMAGE_BOOT_FILES` and appended an initramfs-only package. Both were removed
after selecting ext4. Rootfs output formats were reduced to `ext4 tar.gz`, and
automatic copying to an unused TFTP directory was disabled. This avoids
building and deploying unused RAM-root artifacts while retaining the ext4
image and the tarball used for offline inspection and WIC packaging.

### Automatic PL loader

`meta-user` contains three local recipes:

```text
qcrate-firmware   matching bitstream binary and full device-tree overlay
qcrate-pl-loader  systemd one-shot service and FPGA Manager command
qcrate-tools      /usr/bin/qcrate-apb and its Python 3 runtime dependency
```

The image includes them through:

```bitbake
IMAGE_INSTALL:append = " qcrate-firmware qcrate-pl-loader qcrate-tools"
```

`qcrate-firmware` depends on `sdt-artifacts`, `bootgen-native`, and
`dtc-native`. It converts the XSA-extracted `qcrate_kv260.bit` to the FPGA
Manager `.bit.bin` format and compiles the generated full plugin overlay from
`project-spec/configs/pl.dtsi`. The recipe uses PetaLinux/Yocto-native tools,
not whatever `bootgen` or `dtc` happens to be installed on the host.

Do not substitute the SDT artifact named `pl.dtsi` for the generated full
overlay. The SDT artifact is an include fragment beginning with the PL bus;
the PetaLinux-generated file begins with `/dts-v1/; /plugin/;` and contains
the FPGA-region target, firmware name, fabric clocks, DMA, and fixups needed by
Linux overlay application.

At boot, `qcrate-pl-load.service` runs after local filesystems are available
and before `multi-user.target`. It executes:

```bash
/usr/bin/fpgautil \
  -b /lib/firmware/xilinx/base/qcrate_kv260.bit.bin \
  -o /lib/firmware/xilinx/base/pl.dtbo
```

Both inputs come from the same XSA import. Loading only the bitstream is
invalid for this platform because Linux would lack the clock, DMA, interrupt,
and address-map nodes. Loading only the overlay is equally invalid because its
devices would refer to unprogrammed logic.

The service intentionally fails if either artifact is missing. The 2024.2
`fpgautil` program can return zero even after reporting an overlay or FPGA-state
failure, so the wrapper rejects error text and independently requires all of
the following:

```text
FPGA Manager state       operating
full-overlay status      applied
live firmware-name       qcrate_kv260.bit.bin
pl_clk0 rate             200000000 Hz (+/- 1000 Hz)
pl_clk1 rate             100000000 Hz (+/- 1000 Hz)
```

An existing full overlay is accepted only if it passes the same identity and
clock checks. This makes service restart idempotent for Q-Crate while rejecting
a stale vendor overlay. Temporary `fpgautil` output is confined to
`/run/qcrate-pl-load` instead of the persistent root filesystem.

The KV260 BSP still installs `dfx-mgr`, `xmutil`, and its reference firmware so
they remain available for later DFX learning. A `dfx-mgr_%.bbappend` disables
only `dfx-mgr.service` autostart. The fixed Q-Crate platform therefore has one
automatic owner of `/configfs/device-tree/overlays/full` without removing the
vendor tools.

The service has no stop or unload action because Q-Crate is a fixed boot
platform, not an `xmutil` application slot. Future Q-Crate services should use:

```ini
Requires=qcrate-pl-load.service
After=qcrate-pl-load.service
```

The existing OpenSSH image feature is retained. `qcrate-tools` depends on the
complete `python3` runtime because the utility uses split standard-library
modules including `argparse`, `mmap`, and `dataclasses`.
`qcrate-pl-loader` depends on `fpga-manager-script`, so all required runtime
packages follow from the recipes rather than workstation setup instructions.
`qcrate-apb` performs the same firmware-name and clock-rate preflight before it
maps the default APB window. A failed preflight exits with status 2 without
opening `/dev/mem`, preventing a known missing-clock AXI stall from freezing
the A53. Non-default `--base` accesses remain explicit low-level diagnostics
and are not covered by this guard.

### Normalize and audit configuration

Run PetaLinux's noninteractive configuration pass after changing the XSA,
platform configuration, or rootfs package selection:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux/qcrate-kv260
petalinux-config --silentconfig
```

Observed on 2026-08-05:

```text
[INFO] pl-overlay [ full ] is enabled ...
[INFO] Successfully configured project
```

Audit the normalized settings and generated overlay without opening a GUI:

```bash
rg -n 'FPGA_MANAGER|PL_DT_OVERLAY|ROOTFS_EXT4|BOOTARGS_GENERATED' \
  project-spec/configs/config

rg -n 'firmware-name|clocking[01]|assigned-clock-rates|dma@a0000000' \
  project-spec/configs/pl.dtsi
```

Expected overlay facts:

```text
firmware-name = "qcrate_kv260.bit.bin"
clocking0 assigned-clock-rates = 200000000
clocking1 assigned-clock-rates = 100000000
DMA node at 0xA0000000
```

`project-spec/configs/pl.dtsi` is generated and ignored. The source of truth is
the XSA plus checked-in PetaLinux configuration, not a hand-edited generated
overlay. `petalinux_flow.py configure` transactionally invalidates both the
generated `components/plnx_workspace` and the copied overlay before hardware
import. This forces Lopper to regenerate the full plugin overlay from the new
SDT instead of reporting `Specified workspace already set up, leaving as-is`
and retaining an older hardware description. The script audits the regenerated
firmware name and DMA SG contract and restores both previous artifacts if
import fails.

## First image build and deployment

The first image was built, audited, packaged, and written on 2026-08-05. The
remaining acceptance work requires booting the KV260 and running the checks in
the next section.

### 1. Build

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux/qcrate-kv260
petalinux-build
```

The first build performs the full Yocto recipe parse and downloads/builds any
uncached dependencies. The recorded build completed successfully. Later
focused rebuilds used the existing sstate cache:

```bash
petalinux-build -c qcrate-firmware
petalinux-build -c rootfs
```

The focused firmware build attempted 1358 tasks, reused 1337, and passed all
package QA. The rootfs rebuild attempted 15444 tasks, reused 15430, and
completed with no errors. Its eight deferred-runqueue warnings concerned
shared native tools across FSBL/PMU multiconfigs; BitBake resolved them and all
tasks succeeded.

On failure, return the first causal `ERROR:` block, not the complete log. Useful
searches are:

```bash
rg -n 'ERROR:|Failed|Nothing RPROVIDES|qcrate-' build/ | head -80
```

### 2. Audit the built root filesystem

Before writing an SD card, confirm that the fixed platform is complete:

```bash
tar -tzf images/linux/rootfs.tar.gz | \
  rg 'qcrate-apb|qcrate-pl-load|firmware/xilinx/base/(pl.dtbo|qcrate_kv260.bit.bin)'

rg -n '^(qcrate-firmware|qcrate-pl-loader|qcrate-tools) ' \
  images/linux/rootfs.manifest
```

The initial audit found the service and APB tool but no bitstream or overlay.
That was a real image-packaging defect, not a build failure. The
`qcrate-firmware` recipe above fixed it. The accepted rebuild contains:

```text
./etc/systemd/system/multi-user.target.wants/qcrate-pl-load.service
./usr/bin/qcrate-apb
./usr/bin/qcrate-load-pl
./usr/lib/systemd/system/qcrate-pl-load.service
./usr/lib/firmware/xilinx/base/qcrate_kv260.bit.bin
./usr/lib/firmware/xilinx/base/pl.dtbo
```

`/lib` is a usr-merge symlink on the target, so the loader's `/lib/firmware`
path resolves to the packaged `/usr/lib/firmware` directory. The service
enable link targets `/usr/lib/systemd/system/qcrate-pl-load.service`.

Recorded rootfs artifacts:

```text
rootfs.ext4   1.1 GiB  SHA-256 22a0ff93f36918d32d0c00c14e33870659987d5042856ed283e6fdddb90cba03
rootfs.tar.gz 202 MiB  SHA-256 ff716d711c6fba97f255259399d1d20989a380390b7ba27ff545b438ad4510c4
pl.dtbo       3797 B   SHA-256 fdd9b6198074c09b6b2c3b16df26f093d930dbc5705f0303159898f940ff7eaa
bit.bin       7807932 B SHA-256 61ec5fdc939ebcd240cf25f99d530d6b516b849e0cca712a6596ef7a21a73821
```

Do not deploy an image if either firmware artifact or the service enable link
is absent. Fix packaging rather than weakening the loader's checks.

### 3. Package the QSPI-primary SD image

Use the current non-deprecated command form and explicitly omit `BOOT.BIN` from
the SD boot partition because the board boots its A/B firmware from QSPI:

```bash
petalinux-package wic \
  --bootfiles 'boot.scr Image system.dtb system-zynqmp-sck-kv-g-revB.dtb' \
  --wic-extra-args='-c xz'
```

Expected output:

```text
images/linux/petalinux-sdimage.wic.xz
```

Observed result:

```text
Compressed size:   138 MiB
Expanded size:     6 GiB
SHA-256:           0c7654e27687f7e4cc04ce36dcd8ea3bd67a45c022c63ff6cbc652e7b6adcc66
Partition 1:       2 GiB FAT32, label boot, active
Partition 2:       4 GiB ext4, label root
```

The WIC warning `bootloader config not specified, using defaults` is expected
for this QSPI-primary image. Verify the compressed image without trusting only
the command exit status:

```bash
xz --robot -l images/linux/petalinux-sdimage.wic.xz
xz -t images/linux/petalinux-sdimage.wic.xz
```

The compressed `.wic.xz` is the authoritative deployable artifact. A previous
uncompressed `.wic` can remain in `images/linux/` when a later
`petalinux-package wic -c xz` updates only `.wic.xz`. Do not use an existing
`.wic` unless it was decompressed from the verified `.wic.xz` in the same
workflow. Streaming `.wic.xz` directly to `dd`, as below, removes this stale
intermediate entirely.

### 4. Write a spare SD card

Identify the removable device carefully. The target below is the whole device,
not a partition:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,TRAN,RM,FSTYPE,MOUNTPOINTS
```

The recorded card was the only attached SD card:

```text
Device:       /dev/mmcblk0
Capacity:     58.3 GiB (marketed as 64 GB)
Card serial:  0x000044c6
Old labels:   system-boot, writable
```

After replacing `/dev/sdX` with the freshly verified whole-device path and
unmounting all of its partitions:

```bash
sudo umount /dev/sdX1 /dev/sdX2
xzcat images/linux/petalinux-sdimage.wic.xz | \
  sudo dd of=/dev/sdX bs=16M status=progress conv=fsync
sync
```

Never infer `/dev/sdX` from an old session; removable-device names can change
after every reconnect.

This workstation used `udisksctl` to unmount and PolicyKit to authorize the
write because its noninteractive execution channel could not provide a sudo
password. The actual data write is equivalent to:

```bash
xzcat images/linux/petalinux-sdimage.wic.xz | \
  sudo dd of=/dev/mmcblk0 bs=16M status=progress conv=fsync
```

It wrote 6442455040 bytes and completed the physical `fsync` before returning.

### 5. Expand persistent ext4 to the card capacity

The reproducible WIC is deliberately fixed at 6 GiB; generating a 64 GB image
would waste build time and storage. Expand only the second partition after the
write. First confirm that partition 2 is the ext4 root and is not mounted:

```bash
lsblk -o NAME,PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS /dev/mmcblk0
sudo parted -s /dev/mmcblk0 resizepart 2 100%
sudo partprobe /dev/mmcblk0
```

The PetaLinux image uses a newer ext4 feature than Ubuntu 22.04's host
`e2fsck 1.46.5` understands. The old tool reports `FEATURE_C12`; do not use it
to repair or resize this filesystem. Use Yocto's matching 1.47.0 tools:

```bash
E2FS_NATIVE=/tools/fpga_projects/qcrate/kv260/linux/petalinux/.tmp/qcrate-kv260/sysroots-components/x86_64/e2fsprogs-native/sbin

sudo "$E2FS_NATIVE/e2fsck" -fy /dev/mmcblk0p2
sudo "$E2FS_NATIVE/resize2fs" /dev/mmcblk0p2
sudo "$E2FS_NATIVE/e2fsck" -fn /dev/mmcblk0p2
sudo fsck.vfat -n /dev/mmcblk0p1
```

Observed final layout:

```text
/dev/mmcblk0p1  2.0 GiB FAT32 boot  24 MiB used
/dev/mmcblk0p2  56.3 GiB ext4 root  about 51 GiB free
```

The final FAT and ext4 checks completed cleanly. Mounted-card inspection also
confirmed that every boot-file hash matched `images/linux/` and both firmware
hashes matched the audited rootfs. The card was then unmounted and is safe to
remove.

## First UART boot diagnosis: 2026-08-05

The first serial capture is retained as `uart1_board_boot_log.txt`. It proves
that factory QSPI successfully reached U-Boot, U-Boot found partition 1 and
loaded `boot.scr`, `Image`, and `system.dtb`, and Linux started normally. The
blocking sequence is:

```text
Kernel command line: ... root=/dev/mmcblk0p2 ro rootwait ...
Waiting for root device /dev/mmcblk0p2...
mmc1: SDHCI controller on ff170000.mmc
mmcblk1: mmc1:544c MSSD0 58.3 GiB
mmcblk1: p1 p2
```

Linux never mounted rootfs or executed `/sbin/init`. Consequently there was no
login prompt, `qcrate-pl-load.service` never ran, and SOM DS34 correctly stayed
off. This was not an FPGA Manager or RTL failure.

The same capture records 2020.2 FSBL, TF-A, U-Boot, and PMU firmware in QSPI.
The 2024.2 kernel repeatedly reports that some pinctrl operations require
firmware 2023.1 or newer. Those warnings did not cause the root-device stall,
but the QSPI/Linux compatibility gap must be reviewed after the corrected
image reaches userspace and before fixed-platform acceptance.

The follow-up manual test is retained as `quick_fix_result_log.txt`. It did
not test the corrected `/dev/mmcblk1p2` argument: Linux reported an empty
kernel command line and then panicked on `unknown-block(0,0)`. The entered
`setenv` value therefore did not reach the device tree passed to Linux. For a
manual U-Boot test, verify both representations before running `booti`:

```text
printenv bootargs
fdt print /chosen bootargs
```

Both commands must show a nonempty `root=... rootwait` argument. This check
separates a command-entry/device-tree problem from a genuine root-device
selection problem.

## First userspace boot and clock failure: 2026-08-06

The corrected root argument reached persistent userspace successfully:

```text
/dev/mmcblk1p2 ext4 rw,relatime
```

The `petalinux` account accepted its empty initial password and required a new
password at first login. The root account in this particular built image is
locked; it does not accept `root` as a password.

`qcrate-pl-load.service` reported `active (exited)`, FPGA Manager reported
`operating`, and the configfs overlay reported `applied`. Those checks were not
sufficient. The clock summary showed both `pl0_ref` and `pl1_ref` at about
100 MHz with prepare and enable counts of zero. The generated overlay requests
200 MHz and 100 MHz respectively, so zero counts mean that its `xlnx,fclk`
devices did not complete probe and did not enable the PS-to-PL clocks.

A subsequent APB read stalled the AXI transaction and made the board
unresponsive. Do not run `qcrate-apb` when either fabric clock has a zero enable
count or the rates are not exactly 200 MHz and 100 MHz. The loader must be
strengthened to treat missing, disabled, or incorrectly programmed fabric
clocks as a service failure before this image is accepted.

The follow-up service and kernel logs identified the immediate root cause. The
KV260 BSP's `packagegroup-kria` installs and enables `dfx-mgr.service`, and its
`default_firmware` alternative selects `k26-starter-kits`. That daemon loaded
the vendor base bitstream and `k26-starter-kits.dtbo` first. The vendor overlay
adds both `firmware-name = "k26-starter-kits.bin"` and PL reset properties to
the full FPGA region.

`qcrate-pl-load.service` then attempted to apply Q-Crate to that same full
region. `fpgautil` printed `Failed to apply Overlay`, while the kernel reported
`Region already has overlay applied` and returned `-EINVAL`. PetaLinux 2024.2's
`fpgautil` nevertheless returned success. The custom loader then observed the
vendor overlay's existing `full/status=applied` and incorrectly declared the
Q-Crate load successful. The missing live-tree `clocking0` and `clocking1`
nodes confirm that the Q-Crate overlay never applied.

This is a PL-load ownership conflict, not evidence that the XSA or generated
Q-Crate overlay lost the requested clock rates. A fixed platform must have one
owner: either disable the BSP DFX manager's default-firmware autoload and let
the Q-Crate service own the full region, or package Q-Crate as the DFX
manager's default firmware. Do not leave both enabled.

### Manual ownership proof

Before rebuilding, `test3.txt` tested the corrected ownership model at runtime:

```bash
sudo systemctl stop qcrate-pl-load.service
sudo systemctl stop dfx-mgr.service
sudo fpgautil -R -n full
test ! -e /configfs/device-tree/overlays/full && echo "vendor overlay removed"
sudo systemctl start qcrate-pl-load.service
```

Observed results:

```text
vendor overlay removed
FPGA Manager state: operating
full overlay status: applied
firmware-name: qcrate_kv260.bit.bin
/proc/device-tree/axi/clocking0: present
/proc/device-tree/axi/clocking1: present
```

This proves that the Q-Crate bitstream and overlay can load once the vendor
owner is removed. The serial-pasted `find` command lost its escaped semicolon
and recursively printed `/sys/devices/platform`; the final clock-summary
command was also left at a continuation prompt. Therefore `test3.txt` does not
contain a valid rate measurement or APB result. It supports the ownership fix,
but does not complete platform acceptance.

### Permanent ownership rebuild

The first permanent source changes were:

```text
recipes-bsp/dfx-mgr/dfx-mgr_%.bbappend
    disabled vendor dfx-mgr.service autostart
qcrate-load-pl
    rejects fpgautil false success, wrong owner, and wrong clock rates
qcrate-apb
    refuses default-window APB access until owner and clocks are valid
```

The resulting image booted from `/dev/mmcblk1p2`, but DFX still won the FPGA
region. The new evidence exposed a systemd dependency that a direct
`is-enabled dfx-mgr.service` check does not reveal:

```text
kria-dashboard.service: enabled
kria-dashboard.service: Wants=dfx-mgr.service
dfx-mgr.service:        disabled, but started as a dependency
live firmware-name:     k26-starter-kits.bin
qcrate-pl-load.service: failed safely
```

In systemd, `disabled` means a unit has no automatic enable link of its own. It
does not prohibit another enabled unit from starting it through `Wants=` or
`Requires=`. The fixed-platform correction therefore uses three layers:

```text
recipes-bsp/dfx-mgr/dfx-mgr_%.bbappend
    disables dfx-mgr's own autostart
recipes-utils/kria-dashboard/kria-dashboard.bbappend
    disables the dashboard that requests dfx-mgr.service
qcrate-pl-loader
    installs /etc/systemd/system/dfx-mgr.service -> /dev/null
qcrate-pl-load.service
    orders before and conflicts with dfx-mgr.service
```

Two implementation details matter. The vendor dashboard recipe is unversioned,
so `kria-dashboard_%.bbappend` does not match it. Also, Yocto 2024.2 accepts
`SYSTEMD_AUTO_ENABLE = "mask"` but its generated package script does not create
the `/dev/null` link. Q-Crate therefore packages that link explicitly.

The DFX manager, dashboard, `xmutil`, and reference firmware remain installed
for later DFX study. Masking changes only the default service policy of this
fixed Q-Crate image. A future DFX milestone must deliberately unmask DFX and
disable the fixed-platform loader rather than enabling two FPGA-region owners.

### Runtime proof of the dependency fix

The currently booted image can test the corrected ownership model without an
image rebuild. These masks are runtime-only and disappear at reboot:

```bash
sudo systemctl stop kria-dashboard.service dfx-mgr.service
sudo systemctl mask --runtime kria-dashboard.service dfx-mgr.service
sudo fpgautil -R -n full
test ! -e /configfs/device-tree/overlays/full && echo "vendor overlay removed"
sudo systemctl reset-failed qcrate-pl-load.service
sudo systemctl start qcrate-pl-load.service

systemctl status qcrate-pl-load.service --no-pager -l
sudo journalctl -b -u qcrate-pl-load.service --no-pager -l
tr -d '\0' </proc/device-tree/fpga-region/firmware-name; echo
cat /sys/devices/platform/axi/axi:clocking0/set_rate
cat /sys/devices/platform/axi/axi:clocking1/set_rate
```

Do not run `qcrate-apb` unless the service is `active (exited)`, the firmware
name is `qcrate_kv260.bit.bin`, and the two rates are respectively `200000000`
and `100000000`.

The runtime proof removed the ownership race and loaded
`qcrate_kv260.bit.bin`, but both clocks remained at 99,999,999 Hz. This isolates
the second cause: SDT generated `assigned-clocks` and `assigned-clock-rates`,
but omitted `assigned-clock-parents`. The factory-QSPI FSBL leaves PL0 and PL1
on its default clock source; unlike an XSA-generated FSBL, it never executes
Q-Crate's DPLL source selection from `psu_init.c`.

`qcrate-firmware` now appends `qcrate-clock-parents.dtsi` to the generated PL
overlay before compilation. It assigns DPLL, ZynqMP clock ID 3, as the parent
of both PL clocks. Linux's common-clock framework will therefore reparent each
clock before applying the generated 200 MHz and 100 MHz rates. Keeping this
supplement in the recipe instead of editing generated `pl.dtsi` makes the fix
survive later XSA/SDT imports.

The corrected image was deployed and proved that this overlay supplement is
not sufficient with the installed factory QSPI boot firmware. The live system
had exactly one PL owner, loaded `qcrate_kv260.bit.bin`, and created both FCLK
devices, but both muxes stayed on the 1.5 GHz factory parent path and both
outputs remained at 99,999,999 Hz. The 2024.2 kernel also reported unsupported
platform-firmware APIs during boot. Rebuilding the rootfs or overlay again
cannot repair initialization performed before Linux starts.

### XSA-matched Kria boot firmware

The standard fixed-platform correction is to update the KV260's inactive QSPI
boot slot with FSBL, PMUFW, TF-A, DTB, and U-Boot generated by this accepted
PetaLinux/SDT project. The PL bitstream remains absent from QSPI and continues
to be loaded from the SD rootfs by FPGA Manager. This preserves Kria's split
between platform boot firmware and late-bound PL applications.

Package and audit the A/B update image with the repository script:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate
kv260/linux/petalinux/scripts/package_bootfw.sh
```

The script refuses to package unless SDT `psu_init.c` contains these values:

```text
PL0_REF_CTRL = 0x01010203: DPLL / 2 / 1 = 200 MHz
PL1_REF_CTRL = 0x01010403: DPLL / 4 / 1 = 100 MHz
```

It also verifies that Bootgen selected only `zynqmp_fsbl.elf`, `pmufw.elf`,
`bl31.elf`, `system.dtb`, and `u-boot.elf`, and rejects any `.bit` partition.
The checked output is:

```text
qcrate-kv260/images/linux/qcrate-kv260-bootfw.bin
SHA-256: cef289b22152c83f9af2ffd1578e824df85ee3445120c211a601395589b76c41
```

Copy that file and its `.sha256` companion to `~/qcrate/bootfw` on the board
without renaming it to the SD-card `BOOT.BIN`. First verify the transfer and
record the A/B state, then update only through the supported Kria tool:

```bash
cd ~/qcrate/bootfw
sha256sum -c qcrate-kv260-bootfw.bin.sha256
sudo xmutil bootfw_status
sudo xmutil bootfw_update -i "$PWD/qcrate-kv260-bootfw.bin"
sudo xmutil bootfw_status
```

Never write a raw QSPI MTD partition with `dd`. `xmutil` writes the inactive
slot and preserves the known-good slot. After the update completes, perform a
full power cycle. On the first successful boot, validate the candidate slot
immediately so it does not fall back on a later reboot:

```bash
sudo xmutil bootfw_update -v
sudo xmutil bootfw_status
```

Then run the normal clock and APB acceptance sequence below. If Linux cannot
boot the candidate, power-cycle again to let the Kria image selector recover
the previous known-good slot; use the FWUEN recovery flow only if A/B fallback
does not recover the board.

Rebuild the changed packages and root filesystem through the normal incremental
PetaLinux build, then create a new WIC image:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux/qcrate-kv260

petalinux-build
petalinux-package wic \
  --bootfiles 'boot.scr Image system.dtb system-zynqmp-sck-kv-g-revB.dtb' \
  --wic-extra-args='-c xz'
```

Audit ownership and safety content before writing the card:

```bash
tar -tzf images/linux/rootfs.tar.gz | \
  rg 'qcrate-apb|qcrate-load-pl|dfx-mgr.service|kria-dashboard.service'
tar -xOf images/linux/rootfs.tar.gz \
  ./usr/lib/systemd/system/qcrate-pl-load.service
tar -tvzf images/linux/rootfs.tar.gz | \
  rg 'etc/systemd/system/(dfx-mgr.service|multi-user.target.wants/(qcrate-pl-load|dfx-mgr|kria-dashboard).service)'
sha256sum images/linux/petalinux-sdimage.wic.xz
```

The decisive audit is that `qcrate-pl-load.service` is enabled and
`dfx-mgr.service` resolves to `/dev/null`, while neither DFX nor the dashboard
has a `multi-user.target.wants` link. Their original unit files remain under
`/usr/lib/systemd/system`, preserving the tools for later DFX work.

The first permanent image build completed on 2026-08-06, before the dashboard
dependency was understood. It is retained as build history but is superseded
and must not be redeployed. BitBake parsed 23,212 recipes with zero errors and
reported:

```text
Attempted 18926 tasks; 17513 reused; all succeeded
Successfully built project
```

Fourteen runqueue warnings concerned forced deferred native tasks shared by the
FSBL and PMU multiconfigurations; no task failed. The generated rootfs audit
proved only the incomplete `disabled` policy:

```text
qcrate-pl-load.service enable link: present
dfx-mgr systemd/SysV enable link:    absent
dfx-mgr package and service file:    installed; later started by dashboard
qcrate-load-pl installed hash:       matches repository source
qcrate-apb installed hash:           matches repository source
```

Packaging and compressed-image integrity also passed. The superseded artifact
was:

```text
File:   images/linux/petalinux-sdimage.wic.xz
Size:   151696680 bytes (about 145 MiB)
WIC:    6442455040 bytes uncompressed
SHA256: 4bb485863363a9c9c24f0d378527e896abe9995a93d6cbdccbc4d7ab35ed213f
```

`xz -t images/linux/petalinux-sdimage.wic.xz` passed. Integrity did not prove
runtime service ownership because systemd dependencies can start disabled
units. The replacement image must pass the mask/link audit above.

### First permanent-image write failure

The first attempt to deploy this image started at step 9 of the command summary
and therefore skipped the old step 8 decompression command. Step 10 then wrote
the pre-existing uncompressed `petalinux-sdimage.wic`, which was older than the
new `.wic.xz`:

```text
petalinux-sdimage.wic     2026-08-05 20:50, 6442455040 bytes
petalinux-sdimage.wic.xz  2026-08-06 11:35,  151696680 bytes
```

Host inspection of the returned SD card proved that it contained the old
image: its DTB still selected `/dev/mmcblk0p2`, `dfx-mgr.service` was enabled,
and both installed Q-Crate scripts had the old hashes. The UART kernel command
line independently showed `root=/dev/mmcblk0p2`. This was a stale host artifact
write, not a failure of the permanent Yocto changes.

The command summary now verifies and streams `.wic.xz` directly. This makes
steps 9 and 10 self-contained and prevents the same class of mistake.

## First-boot acceptance

Capture the serial console for the first boot. Use it to install the audited
XSA-matched image into the inactive QSPI slot, power-cycle, and validate that
candidate immediately. The new QSPI firmware then loads the SD kernel and ext4
rootfs, after which systemd must load Q-Crate without an application-level
`xmutil loadapp` or manual `fpgautil` command.

Check the root filesystem and automatic loader first:

```bash
findmnt -no SOURCE,FSTYPE,OPTIONS /
systemctl is-enabled dfx-mgr.service
systemctl is-enabled kria-dashboard.service
systemctl status qcrate-pl-load.service --no-pager
sudo journalctl -b -u qcrate-pl-load.service --no-pager -l
cat /sys/class/fpga_manager/fpga0/state
cat /configfs/device-tree/overlays/full/status
tr -d '\0' </proc/device-tree/fpga-region/firmware-name; echo
```

Expected results are partition 2 with `ext4` and `rw`, service state
`active (exited)`, `dfx-mgr.service` state `masked`, `kria-dashboard.service`
state `disabled`, FPGA Manager state `operating`, overlay status `applied`, and
firmware name `qcrate_kv260.bit.bin`. The expected root source on this KV260 is
`/dev/mmcblk1p2`; this is independent of the SD card's device name on the
development PC.

Then verify the overlay-created clocks before touching APB:

```bash
sudo sh -c 'mountpoint -q /sys/kernel/debug || mount -t debugfs none /sys/kernel/debug'
sudo sh -c "grep -E 'pl[01]_ref' /sys/kernel/debug/clk/clk_summary"
cat /sys/devices/platform/axi/axi:clocking0/set_rate
cat /sys/devices/platform/axi/axi:clocking1/set_rate
```

Expected final rates:

```text
pl0_ref  200000000
pl1_ref  100000000
```

Only after the loader and clocks pass should userspace access Q-Crate. The new
tool performs this owner/rate preflight itself and refuses to open `/dev/mem`
when the platform is unsafe:

```bash
sudo qcrate-apb smoke --skip-writes
sudo qcrate-apb smoke
sudo qcrate-apb dump
```

The first read must return `DEVICE_ID=0x51435254`; the full smoke test must end
with `PASS qcrate_apb_smoke`. Reboot once and repeat these checks to prove the
platform is automatic and persistent.

If APB hangs, do not keep retrying `/dev/mem`. Collect these first:

```bash
systemctl status qcrate-pl-load.service --no-pager
journalctl -b -u qcrate-pl-load.service --no-pager
dmesg | grep -Ei 'fpga|overlay|fclk|clock|amba|dma' | tail -120
sudo sh -c "grep -E 'pl[01]_ref' /sys/kernel/debug/clk/clk_summary"
```

The prior Ubuntu investigation proved that an APB access with an absent PL
clock can stall the AXI transaction and freeze the A53. Service success and
clock rates are therefore preconditions, not optional diagnostics.

If the loader reports `Failed to apply Overlay`, compare the overlay fixups
with the symbols exported by the base DT before debugging FPGA logic:

```bash
fdtdump /boot/system.dtb 2>/dev/null | sed -n '/__symbols__ {/,/};/p'
fdtdump /lib/firmware/xilinx/base/pl.dtbo 2>/dev/null | \
  sed -n '/__fixups__ {/,/};/p'
```

The deployed KV260 base DT exports `amba = "/axi"`, but not `amba_pl`. Runtime
PL additions must therefore target `&amba`. The `amba_pl` label seen in raw SDT
source is not available to the kernel overlay resolver. This distinction first
surfaced when the DMAEngine client node was added: targeting `&amba_pl` made the
entire overlay fail before any Linux driver probe or APB transaction.

## Complete command summary

This section is the short operational runbook. Read the detailed sections
above when a command fails or when changing the platform architecture.

### One-time tool and project bootstrap

These commands document how the accepted project was originally created. Do
not rerun `petalinux-create` over the checked-in `qcrate-kv260/` directory.

```bash
# Load PetaLinux 2024.2 and keep Yocto temporary state off the small /tmp disk.
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux
mkdir -p .tmp/qcrate-kv260

# Confirm that eSDK Update 1 is active.
sed -n '1,160p' /tools/Xilinx/PetaLinux/2024.2/update.log

# Verify the downloaded AMD KV260 SDT BSP before extraction.
file KV260_BSP_DOWLOADED/xilinx-kv260-starterkit-v2024.2-12072024.bsp
md5sum KV260_BSP_DOWLOADED/xilinx-kv260-starterkit-v2024.2-12072024.bsp
sha256sum KV260_BSP_DOWLOADED/xilinx-kv260-starterkit-v2024.2-12072024.bsp

# Create the carrier-aware BSP project once, before applying repository inputs.
petalinux-create project \
  -s KV260_BSP_DOWLOADED/xilinx-kv260-starterkit-v2024.2-12072024.bsp \
  --tmpdir "$PWD/.tmp/qcrate-kv260"
mv xilinx-kv260-starterkit-2024.2 qcrate-kv260
```

Expected BSP MD5 is `14d0453f2bfffe8aaa15ba9d06a250a2`. The final two
commands are historical bootstrap commands; the tracked project already
contains the accepted configuration and `meta-user` recipes.

### Repeatable XSA-to-SD workflow

Run this sequence after an accepted RTL or block-design change.

```bash
# 1. Build Vivado from repository sources and export BIT plus XSA.
cd /tools/fpga_projects/qcrate
python3 scripts/build.py --stage all
test -s build/artifacts/qcrate_kv260.bit
test -s build/artifacts/qcrate_kv260.xsa
sha256sum build/artifacts/qcrate_kv260.bit build/artifacts/qcrate_kv260.xsa

# 2. Regenerate the KV260 System Device Tree from the new XSA.
source /tools/Xilinx/Vitis/2024.2/settings64.sh
SDT_DIR=/tools/fpga_projects/qcrate/build/petalinux/sdt/qcrate-kv260
test ! -e "$SDT_DIR" || mv "$SDT_DIR" "${SDT_DIR}.previous-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$(dirname "$SDT_DIR")"
xsct -nodisp \
  kv260/linux/petalinux/scripts/generate_sdt.tcl \
  build/artifacts/qcrate_kv260.xsa \
  "$SDT_DIR"

# 3. Audit the hardware contract before importing it.
rg -n 'a0000000|a0010000|zynqmp-smk-k26|assigned-clock-rates' \
  "$SDT_DIR"/{system-top.dts,pl.dtsi}
sha256sum build/artifacts/qcrate_kv260.bit "$SDT_DIR/qcrate_kv260.bit"

# 4. Import the SDT and normalize the accepted PetaLinux configuration.
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux/qcrate-kv260
petalinux-config --get-hw-description "$SDT_DIR" --silentconfig
petalinux-config --silentconfig

# 5. Confirm full-overlay, persistent-rootfs, clock, and DMA settings.
rg -n 'FPGA_MANAGER|PL_DT_OVERLAY|ROOTFS_EXT4|BOOTARGS_GENERATED' \
  project-spec/configs/config
rg -n 'firmware-name|clocking[01]|assigned-clock-rates|dma@a0000000' \
  project-spec/configs/pl.dtsi

# 6. Build the complete PetaLinux image.
petalinux-build

# 7. Refuse deployment unless all Q-Crate runtime artifacts are packaged.
tar -tzf images/linux/rootfs.tar.gz | \
  rg 'qcrate-apb|qcrate-pl-load|firmware/xilinx/base/(pl.dtbo|qcrate_kv260.bit.bin)'
tar -xOf images/linux/rootfs.tar.gz \
  ./usr/lib/firmware/xilinx/base/pl.dtbo > /tmp/qcrate-pl.dtbo
dtc -I dtb -O dts /tmp/qcrate-pl.dtbo 2>/dev/null | \
  rg 'assigned-clock-parents|assigned-clock-rates'
tar -tvzf images/linux/rootfs.tar.gz | \
  rg 'etc/systemd/system/(dfx-mgr.service|multi-user.target.wants/(qcrate-pl-load|dfx-mgr|kria-dashboard).service)'
rg -n '^(qcrate-firmware|qcrate-pl-loader|qcrate-tools) ' \
  images/linux/rootfs.manifest
sha256sum images/linux/rootfs.ext4 images/linux/rootfs.tar.gz \
  images/linux/Image images/linux/boot.scr \
  images/linux/system-zynqmp-sck-kv-g-revB.dtb

# 8. Package a factory-QSPI SD image; intentionally omit BOOT.BIN.
petalinux-package wic \
  --bootfiles 'boot.scr Image system.dtb system-zynqmp-sck-kv-g-revB.dtb' \
  --wic-extra-args='-c xz'
sha256sum images/linux/petalinux-sdimage.wic.xz
xz --robot -l images/linux/petalinux-sdimage.wic.xz
xz -t images/linux/petalinux-sdimage.wic.xz

# 8a. Package and audit the XSA-matched Kria A/B boot-firmware update.
cd /tools/fpga_projects/qcrate
kv260/linux/petalinux/scripts/package_bootfw.sh
cd kv260/linux/petalinux/qcrate-kv260/images/linux
sha256sum -c qcrate-kv260-bootfw.bin.sha256
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux/qcrate-kv260
```

### SD-card write and expansion

The example device below is the recorded card. Re-run `lsblk` and replace every
device path if the current card appears under a different name.

```bash
# 9. Identify the whole card immediately before the destructive write.
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,FSTYPE,LABEL,MOUNTPOINTS
sha256sum images/linux/petalinux-sdimage.wic.xz | \
  tee images/linux/petalinux-sdimage.wic.xz.sha256
sha256sum -c images/linux/petalinux-sdimage.wic.xz.sha256
xz -t images/linux/petalinux-sdimage.wic.xz

# 10. Unmount the old filesystems and stream the verified image to the card.
sudo umount /dev/mmcblk0p1 /dev/mmcblk0p2
xzcat images/linux/petalinux-sdimage.wic.xz | \
  sudo dd of=/dev/mmcblk0 bs=16M status=progress conv=fsync
sync

# 11. Confirm the new 2 GiB boot and 4 GiB root partitions.
sudo partprobe /dev/mmcblk0
lsblk -o NAME,PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS /dev/mmcblk0

# 12. Check with the matching Yocto ext4 tools, then use the whole card.
E2FS_NATIVE=/tools/fpga_projects/qcrate/kv260/linux/petalinux/.tmp/qcrate-kv260/sysroots-components/x86_64/e2fsprogs-native/sbin
sudo fsck.vfat -n /dev/mmcblk0p1
sudo "$E2FS_NATIVE/e2fsck" -fn /dev/mmcblk0p2
sudo parted -s /dev/mmcblk0 resizepart 2 100%
sudo partprobe /dev/mmcblk0
sudo "$E2FS_NATIVE/e2fsck" -fy /dev/mmcblk0p2
sudo "$E2FS_NATIVE/resize2fs" /dev/mmcblk0p2
sudo "$E2FS_NATIVE/e2fsck" -fn /dev/mmcblk0p2
sudo fsck.vfat -n /dev/mmcblk0p1

# 13. Mount and inspect the actual card contents.
udisksctl mount -b /dev/mmcblk0p1
udisksctl mount -b /dev/mmcblk0p2
ls -lh /media/$USER/boot
ls -lh /media/$USER/root/usr/lib/firmware/xilinx/base/{pl.dtbo,qcrate_kv260.bit.bin}
readlink /media/$USER/root/etc/systemd/system/multi-user.target.wants/qcrate-pl-load.service
df -hT /media/$USER/boot /media/$USER/root

# Put the audited A/B update in the target user's home directory.
sudo install -d -o 1000 -g 1000 \
  /media/$USER/root/home/petalinux/qcrate/bootfw
sudo install -m 0644 -o 1000 -g 1000 \
  images/linux/qcrate-kv260-bootfw.bin \
  images/linux/qcrate-kv260-bootfw.bin.sha256 \
  /media/$USER/root/home/petalinux/qcrate/bootfw/
(cd /media/$USER/root/home/petalinux/qcrate/bootfw && \
  sha256sum -c qcrate-kv260-bootfw.bin.sha256)

# 14. Unmount before moving the card to the KV260.
udisksctl unmount -b /dev/mmcblk0p1
udisksctl unmount -b /dev/mmcblk0p2
```

### First boot and board test

Connect the KV260 serial console, insert the card, and boot. Do not access APB
until the automatic loader and both fabric clocks have passed.

On the first boot, install the XSA-matched boot firmware into the inactive A/B
QSPI slot. This is required once for this platform-firmware version, rather
than after every userspace or PL-only change:

```bash
# 15. Verify the transferred image and update only the inactive QSPI slot.
cd ~/qcrate/bootfw
sha256sum -c qcrate-kv260-bootfw.bin.sha256
sudo xmutil bootfw_status
sudo xmutil bootfw_update -i "$PWD/qcrate-kv260-bootfw.bin"
sudo xmutil bootfw_status
sudo poweroff
```

Remove power after shutdown, wait several seconds, then power the KV260 on.
Once the candidate slot boots successfully, mark it healthy immediately:

```bash
# 16. Commit the successfully booted A/B candidate.
sudo xmutil bootfw_update -v
sudo xmutil bootfw_status
```

```bash
# 17. Prove that Linux booted from persistent ext4 and loaded Q-Crate itself.
findmnt -no SOURCE,FSTYPE,OPTIONS /
systemctl is-enabled dfx-mgr.service
systemctl is-enabled kria-dashboard.service
systemctl is-enabled qcrate-pl-load.service
systemctl status qcrate-pl-load.service --no-pager
sudo journalctl -b -u qcrate-pl-load.service --no-pager -l
cat /sys/class/fpga_manager/fpga0/state
cat /configfs/device-tree/overlays/full/status
tr -d '\0' </proc/device-tree/fpga-region/firmware-name; echo

# 18. Verify the required PS-to-PL clocks before the first APB transaction.
sudo sh -c 'mountpoint -q /sys/kernel/debug || mount -t debugfs none /sys/kernel/debug'
sudo sh -c "grep -E 'pl[01]_ref' /sys/kernel/debug/clk/clk_summary"
cat /sys/devices/platform/axi/axi:clocking0/set_rate
cat /sys/devices/platform/axi/axi:clocking1/set_rate

# 19. Test read-only APB first, then writes and the complete register dump.
sudo qcrate-apb smoke --skip-writes
sudo qcrate-apb smoke
sudo qcrate-apb dump

# 20. Reboot and repeat the acceptance checks to prove persistence.
sudo reboot
```

Expected board results are masked `dfx-mgr.service`, disabled
`kria-dashboard.service`,
`firmware-name=qcrate_kv260.bit.bin`, `pl0_ref=200000000`,
`pl1_ref=100000000`, FPGA Manager `operating`, overlay `applied`,
`DEVICE_ID=0x51435254`, and `PASS qcrate_apb_smoke` without any `xmutil` or
manual `fpgautil` command.

## R5 FreeRTOS/OpenAMP extension

The fixed platform now has a separately documented heterogeneous-control
extension. See `../openamp/README.md` before building it: the R5 ELF must be
generated and staged by the Vitis flow before BitBake can build the
`qcrate-openamp` recipe.
