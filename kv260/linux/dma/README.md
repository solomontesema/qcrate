# Q-Crate Linux DMA capture

## Purpose and status

This milestone moves deterministic frames from `qcrate_stream_engine` through
AXI DMA S2MM into DDR and verifies every received word from Linux userspace.
It replaces direct AXI DMA register programming and ad hoc physical-buffer
management with the Linux DMAEngine ownership model.

```text
qcrate_stream_engine (200 MHz)
        | AXI4-Stream: data, keep, valid, ready, last
        v
AXI DMA S2MM simple mode
        | AXI HP0
        v
kernel coherent DDR buffer
        | mmap, read-only in userspace
        v
qcrate-dma verifier
```

Current scope:

- receive-only AXI DMA simple mode;
- one frame per DMA descriptor;
- kernel-owned Q-Crate control registers and DMA channel;
- one exclusive userspace owner;
- timeout, stream reset, and DMA termination recovery;
- complete verification of `{frame_id[15:0], sample_index[15:0]}`;
- repeat testing of independent one-frame captures.

Scatter-gather rings, uninterrupted multi-frame acquisition, and sustained
throughput measurement are later milestones. `--repeat 100` currently starts
100 independent one-frame commands, so every capture intentionally has frame
ID zero.

Status: accepted on KV260. The acceptance run completed 100 independent
captures of 4095 words without timeout or stale data, verified every word from
`0x00000000` through `0x00000ffe`, and reported zero stream stall cycles.

## Why DMAEngine

The Xilinx AXI DMA kernel driver already owns the DMA registers and interrupt.
The Q-Crate driver is its client: it requests the S2MM channel by device tree,
allocates coherent memory through the DMA API, submits descriptors, and waits
for completion callbacks. Userspace never receives a physical address and
does not need to perform cache maintenance.

This separates responsibilities cleanly:

| Owner | Responsibility |
| --- | --- |
| `xilinx_dma` | AXI DMA registers, descriptor programming, IRQ, channel reset |
| `qcrate_dma` | stream configuration, arm-before-start ordering, buffer, timeout |
| `qcrate-dma` | capture request, `mmap`, pattern verification, optional file output |

The buffer is mapped read-only by the supplied tool. `/dev/qcrate-dma` is mode
`0600`, and the driver permits one open file at a time to prevent concurrent
capture or buffer reuse.

## Source map

All deployable files live in the tracked PetaLinux `meta-user` layer:

```text
recipes-apps/qcrate-firmware/files/qcrate-dma-client.dtsi
    Adds the client to the same PL overlay as axi_dma_0.

recipes-modules/qcrate-dma/
    Out-of-tree platform driver, UAPI header, and module recipe.

recipes-apps/qcrate-dma-tools/
    Python capture and verification utility.

conf/petalinuxbsp.conf
    Installs the module and userspace package in the image.
```

The client node uses `dmas = <&axi_dma_0 1>`. In the Xilinx AXI DMA Linux
driver, channel zero is MM2S and channel one is S2MM. Q-Crate synthesizes only
S2MM, but its channel identifier remains one.

The client belongs in the full PL overlay rather than `system-user.dtsi`.
`axi_dma_0` is itself created by `pl.dtbo`; keeping provider and client in one
overlay gives correct dependency and removal ordering.

The client node also declares both PS fabric clocks. The driver enables those
clock dependencies and acquires the S2MM provider before its first APB read.
This ordering is essential on a runtime-loaded PL overlay: probing must never
touch `0xA0010000` while the control clock or interconnect is unavailable.

## Capture ordering

For each ioctl the driver performs this sequence:

1. Lock the device and terminate any stale DMA transaction.
2. Soft-reset the stream generator and wait for its CDC command to complete.
3. Program frame length, one frame, pattern mode, and disabled Q-Crate IRQs.
4. Prepare and submit one `DMA_DEV_TO_MEM` descriptor.
5. Call `dma_async_issue_pending()` so S2MM is armed.
6. Write `STREAM.CONTROL.START` only after the DMA is ready.
7. Wait for the DMA completion callback with a bounded timeout.
8. Check DMA result/residue and Q-Crate done/error/status registers.
9. Expose the coherent buffer for userspace verification.

Arm-before-start is a required protocol rule. The stream engine does obey
backpressure and would hold `TVALID`, `TDATA`, and `TLAST` stable while DMA
keeps `TREADY` low, but deliberately relying on that stall adds avoidable
latency and obscures failures.

## Current AXI DMA length limit

The current XSA reports:

```text
xlnx,sg-length-width = <14>
maximum byte count   = (2^14)-1 = 16383
maximum whole words  = floor(16383/4) = 4095
```

The property controls the buffer-length register in simple mode too; its name
does not mean it matters only when scatter-gather is enabled. A 4096-word frame
is 16384 bytes and therefore does not fit today.

The driver reads this property from the live DMA controller. The userspace
default is `min(4096, reported_limit)`, so it selects 4095 words now and will
select 4096 automatically after the hardware width becomes 23 or 26 bits.

When that hardware change is scheduled, add this property to the AXI DMA
configuration in `kv260/hw/bd/design_1.tcl`:

```tcl
CONFIG.c_sg_length_width {23}
```

Use 26 instead when the planned maximum single descriptor genuinely needs it.
A wider field raises the legal byte count; it does not allocate a buffer or
enable scatter-gather. Rebuild Vivado/XSA and then regenerate PetaLinux SDT so
the hardware and `xlnx,sg-length-width` remain matched.

## Host build

Start with cheap source checks. These do not build an image:

```bash
cd /tools/fpga_projects/qcrate
python3 \
  kv260/linux/petalinux/qcrate-kv260/project-spec/meta-user/recipes-apps/\
qcrate-dma-tools/files/qcrate-dma --help
git diff --check
```

Compile only the new recipes first. This is the fastest way to find a kernel
API, recipe, or packaging error while reusing the existing Yocto build state:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
cd /tools/fpga_projects/qcrate/kv260/linux/petalinux/qcrate-kv260
petalinux-build -c qcrate-dma
petalinux-build -c qcrate-dma-tools
petalinux-build -c qcrate-firmware
```

Success means BitBake finishes each named recipe without an `ERROR:` line.
For a module compile failure, return the first compiler `error:` and several
lines around it; the final BitBake task summary is usually secondary.

An immediate `make: *** No targets. Stop.` means the external-module Makefile
did not provide the `all` target that delegates to kernel Kbuild. Merely setting
`obj-m := qcrate_dma.o` is sufficient inside Kbuild, but not when BitBake's
`module` class initially invokes ordinary `make`; the tracked Makefile contains
the required `all`, `modules_install`, and `clean` wrapper targets.

Linux 6.6 makes `vm_area_struct.vm_flags` read-only to drivers. Memory-mapping
code must call `vm_flags_set()` rather than modifying `vma->vm_flags` directly;
the DMA driver follows that API for `VM_DONTEXPAND` and `VM_DONTDUMP`.

Yocto names the emitted module RPM with the exact kernel release, for example
`kernel-module-qcrate-dma-6.6.40-xilinx-g...`. The stable `qcrate-dma` recipe
package depends on that exact generated name. `IMAGE_INSTALL` therefore uses
`qcrate-dma`, not an unversioned `kernel-module-qcrate-dma` request that DNF
cannot resolve and not a hardcoded kernel-versioned package name.

After the focused builds pass, produce the updated image using the existing
reproducible flow. No Vivado stage is needed for the present 4095-word test:

```bash
cd /tools/fpga_projects/qcrate
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
python3 kv260/linux/petalinux/scripts/petalinux_flow.py build
python3 kv260/linux/petalinux/scripts/petalinux_flow.py package
```

Write a known disposable SD card only after identifying it by model, size, and
transport. Replace the example device with the whole-card path, never a
partition such as `/dev/mmcblk0p2`:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,FSTYPE,LABEL,MOUNTPOINTS
python3 kv260/linux/petalinux/scripts/petalinux_flow.py deploy \
  --device /dev/mmcblk0
```

The PetaLinux build and SD deployment are intentionally left as user-run
operations because they are long and the device selection is destructive.

## Target acceptance

Boot the card and first confirm that the previously accepted fixed platform
still passes:

```bash
sudo qcrate-first-boot test
```

Confirm provider, client module, character device, and probed limits:

```bash
sudo dmesg | grep -Ei 'xilinx.*dma|qcrate.*dma'
lsmod | grep qcrate_dma
ls -l /dev/qcrate-dma
sudo qcrate-dma info
```

Expected first-build limits are 1 MiB of coherent memory, 16383 DMA bytes, and
4095 whole stream words.

Run one complete capture. With the present hardware, omitting `--words`
selects 4095 automatically:

```bash
sudo qcrate-dma capture
```

Expected final lines include:

```text
PASS 1 capture(s), 4095 words each; every word verified
first/last         : 0x00000000 / 0x00000ffe
completed frames   : 1
```

Exercise repeated channel teardown, rearming, stream reset, and buffer reuse:

```bash
sudo qcrate-dma capture --repeat 100
```

Save the final captured frame when offline inspection is useful:

```bash
sudo qcrate-dma capture --output /tmp/qcrate-frame.bin
od -Ax -tx4 -N 64 /tmp/qcrate-frame.bin
```

After increasing the Vivado length width and deploying the matching XSA-derived
overlay, the planned frame command becomes:

```bash
sudo qcrate-dma info
sudo qcrate-dma capture --words 4096
```

The final word should then be `0x00000fff`.

## Diagnostics

If `/dev/qcrate-dma` is absent, inspect module loading and deferred probe:

```bash
sudo modprobe qcrate_dma
sudo dmesg | tail -100
find /proc/device-tree -name 'qcrate-dma@a0010000' -print
```

If `qcrate-pl-load.service` reports `Failed to apply Overlay`, diagnose the
overlay before inspecting the driver. A platform-driver probe failure does not
normally reject the overlay itself. Verify that every external fixup in the
deployed overlay is exported by the live base DT:

```bash
fdtdump /boot/system.dtb 2>/dev/null | sed -n '/__symbols__ {/,/};/p'
fdtdump /lib/firmware/xilinx/base/pl.dtbo 2>/dev/null | \
  sed -n '/__fixups__ {/,/};/p'
```

The KV260 base tree exports `amba = "/axi"`; it does not export `amba_pl`.
Consequently, Q-Crate's DMAEngine client node targets `&amba`. The `amba_pl`
name generated in intermediate SDT source is a build-time label, not a stable
runtime-overlay ABI. A missing target symbol causes overlay resolution to fail
before `qcrate_dma` can probe.

If the driver reports that it cannot request `rx`, confirm both DMA nodes and
the channel number in the live tree:

```bash
find /proc/device-tree -path '*dma@a0000000*' -print
od -An -tu4 /proc/device-tree/axi/qcrate-dma@a0010000/dmas
```

If capture times out, gather the interrupt count and driver messages before
rebooting or retrying repeatedly:

```bash
grep -Ei 'xilinx|qcrate|dma' /proc/interrupts
sudo dmesg | tail -100
sudo qcrate-apb dump
```

The timeout path soft-resets the stream and synchronously terminates DMA, so a
subsequent capture should start from a defined state. A repeating timeout still
indicates a hardware, interrupt, device-tree, or stream-handshake problem and
should be diagnosed rather than hidden by retries.

## Acceptance boundary

This milestone is complete when:

1. The Xilinx AXI DMA provider and `qcrate_dma` client probe without errors.
2. `qcrate-dma info` reports limits matching the deployed device tree.
3. A single maximum legal frame completes without residue.
4. Every captured word matches the deterministic pattern.
5. One hundred independent captures pass without timeout or stale data.
6. The fixed-platform clock and APB regression still passes.

The next extension is true consecutive multi-frame acquisition: start the
stream generator once with increasing frame IDs and keep capture service ready
across each `TLAST`. That step will determine whether simple-mode descriptor
rearming is sufficient or whether Q-Crate should enable scatter-gather and use
a descriptor ring.

## References

- [Linux DMAEngine client API](https://docs.kernel.org/6.6/driver-api/dmaengine/client.html)
- [AMD: Linux DMA From User Space](https://xilinx-wiki.atlassian.net/wiki/spaces/A/pages/18842418/Linux+DMA+From+User+Space)
- [AMD/Xilinx 2024.2 AXI DMA Linux driver](https://github.com/Xilinx/linux-xlnx/blob/xlnx_rebase_v6.6_LTS_2024.2/drivers/dma/xilinx/xilinx_dma.c)
