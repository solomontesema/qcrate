# Q-Crate Linux DMA capture and frame chaining

## Purpose and status

This milestone moves deterministic frames from `qcrate_stream_engine` through
AXI DMA S2MM into DDR and verifies every received word from Linux userspace.
It replaces direct AXI DMA register programming and ad hoc physical-buffer
management with the Linux DMAEngine ownership model.

```text
qcrate_stream_engine (200 MHz)
        | AXI4-Stream: data, keep, valid, ready, last
        v
AXI DMA S2MM scatter-gather
        | one pre-armed descriptor per frame
        | AXI HP0
        v
kernel coherent DDR buffer
        | mmap, read-only in userspace
        v
qcrate-dma verifier
```

Current source scope:

- receive-only AXI DMA scatter-gather;
- one frame per DMA descriptor;
- one finite RTL `START` for an entire descriptor chain;
- frame-major coherent-buffer layout with increasing frame IDs;
- kernel-owned Q-Crate control registers and DMA channel;
- one exclusive userspace owner;
- timeout, stream reset, and DMA termination recovery;
- complete verification of `{frame_id[15:0], sample_index[15:0]}`;
- backward-compatible one-frame capture and repeat testing.

Cyclic rings, indefinite acquisition, concurrent userspace consumption, and
sustained network streaming remain later milestones. `capture --repeat 100`
still starts 100 independent one-frame commands. `capture-frames --frames N`
starts the stream once and receives frame IDs `0` through `N-1` through a
pre-armed SG chain.

Status:

- Simple-mode baseline accepted on KV260: 100 independent captures of 4095
  words, every word verified, zero timeouts, stale data, or stall cycles.
- Finite SG-chain capture accepted on KV260: the 4096-word one-frame regression,
  one 8-frame chain, one 64-frame chain filling the complete 1 MiB buffer, and
  100 repeated 8-frame chains all passed with every word and frame ID verified.

## Multi-frame architecture decision

Three implementations were considered before changing the accepted baseline:

| Option | Benefit | Technical consequence | Decision |
| --- | --- | --- | --- |
| One aggregate S2MM descriptor | Small software change | Intermediate `TLAST` values terminate AXI DMA packets before the aggregate byte count; several framed packets are not one S2MM transfer | Rejected |
| Simple-mode callback rearming | Reuses the existing bitstream | Linux must program the next transfer after every interrupt, so `TREADY` drops for software latency between frames | Kept only as a diagnostic experiment |
| Finite scatter-gather chain | All destinations are armed before `START`; hardware follows descriptors across `TLAST` boundaries | Requires SG logic, an `M_AXI_SG` DDR connection, a new XSA, and descriptor memory | Approved |

The exact Xilinx 2024.2 DMAEngine driver reinforces this choice. In simple mode
it programs only the first pending descriptor. The pending software list then
becomes active, but there are no SG completion bits with which to distinguish
the remaining entries. Pre-queuing simple-mode descriptors is therefore not a
valid substitute for hardware SG. Callback rearming is safe because Q-Crate
obeys AXI4-Stream backpressure, but it deliberately places Linux scheduling in
the frame-to-frame data path.

The approved implementation is finite rather than cyclic. The driver submits
one destination descriptor per frame into consecutive slots of one coherent
buffer, submits the complete chain, calls `dma_async_issue_pending()`, and only
then writes one RTL `START`. AXI DMA consumes each `TLAST` into the corresponding
descriptor and fetches the next descriptor without userspace intervention.

The chain is capped at 255 frames. This matches AXI DMA's interrupt coalescing
counter and leaves cyclic buffer ownership for a later streaming milestone.

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

Hardware and deployable software sources are tracked in these locations:

```text
kv260/hw/bd/design_1.tcl
    Enables SG, routes M_AXI_SG through the data SmartConnect, and selects the
    23-bit descriptor length.

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

For a finite SG-chain ioctl the driver performs this sequence:

1. Lock the exclusively owned DMA client and soft-reset the stream generator.
2. Wait for the stream reset CDC command to complete.
3. Validate one descriptor's size and the complete chain against hardware and
   coherent-buffer limits.
4. Program frame length, finite frame count, pattern mode, and disabled Q-Crate
   IRQs.
5. Prepare and submit one `DMA_DEV_TO_MEM` descriptor per consecutive frame
   slot; attach the completion callback to the final descriptor.
6. Call `dma_async_issue_pending()` so the complete S2MM chain is armed.
7. Write `STREAM.CONTROL.START` only after the DMA is ready.
8. Wait for the final DMA completion callback with a bounded timeout.
9. Check DMA result/residue, completed-frame count, final frame/sample indexes,
   and Q-Crate done/error status.
10. Expose the frame-major coherent buffer for userspace verification.

The driver does not call `dmaengine_terminate_sync()` before a normal capture.
Requesting the channel gives Q-Crate exclusive DMAEngine ownership, so there is
no previous client's transaction to discard. More importantly, the Xilinx
2024.2 SG provider can remain non-halted while it has no submitted descriptor;
trying to halt that initial state enters its atomic status poll before Q-Crate
has armed any work. Synchronous termination remains on timeout, submission
failure, remove, and other recovery paths where Q-Crate actually owns pending
or active descriptors.

Arm-before-start is a required protocol rule. The stream engine does obey
backpressure and would hold `TVALID`, `TDATA`, and `TLAST` stable while DMA
keeps `TREADY` low, but deliberately relying on that stall adds avoidable
latency and obscures failures. SG may still apply short hardware backpressure
while fetching descriptors; correctness requires no lost or duplicated words,
not a promise that `TREADY` can never deassert.

## AXI DMA SG and length limits

The accepted simple-mode XSA reported:

```text
xlnx,sg-length-width = <14>
maximum byte count   = (2^14)-1 = 16383
maximum whole words  = floor(16383/4) = 4095
```

The SG milestone changes the exported BD Tcl to:

```tcl
CONFIG.c_include_sg {1}
CONFIG.c_sg_include_stscntrl_strm {0}
CONFIG.c_sg_length_width {23}
```

After rebuilding the XSA and regenerating SDT, the expected provider properties
are enabled `xlnx,include-sg` and `xlnx,sg-length-width = <23>`. The maximum
single descriptor becomes `(2^23)-1 = 8388607` bytes. The current 1 MiB coherent
buffer is therefore the practical request limit, not the descriptor length.

The driver reads this property from the live DMA controller. The userspace
default is `min(4096, reported_limit)`, so it automatically moves from 4095 to
4096 words after deployment. At 4096 words per frame, the 1 MiB buffer holds 64
frames. Shorter frames may fit more slots, up to the explicit 255-frame chain
cap. A 26-bit field would add no useful capacity until the coherent-buffer
contract also grows, so 23 bits is the deliberate choice rather than a maximum
setting by habit.

Both `petalinux_flow.py configure` and the `qcrate-firmware` recipe reject an
XSA-derived `pl.dtsi` that does not report enabled SG, the 23-bit length, and a
disabled control/status stream. This prevents a stale or sideband-enabled XSA
from being packaged with the new driver.

## Host build

Start with cheap source checks. These do not build an image:

```bash
cd /tools/fpga_projects/qcrate
python3 \
  kv260/linux/petalinux/qcrate-kv260/project-spec/meta-user/recipes-apps/\
qcrate-dma-tools/files/qcrate-dma --help
git diff --check
```

Do not build the recipes against the old XSA. The firmware recipe now rejects
simple-mode SDT deliberately. Rebuild Vivado and run the PetaLinux `configure`
stage first, then use the focused recipe builds below. Success means BitBake
finishes each named recipe without an `ERROR:` line. For a module compile
failure, return the first compiler `error:` and several lines around it; the
final BitBake task summary is usually secondary.

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

This milestone changes the AXI DMA hardware, so first recreate the Vivado
project from the tracked BD Tcl and export a matching bitstream and XSA:

```bash
cd /tools/fpga_projects/qcrate
python3 scripts/build.py --stage all
```

Expected artifacts are `build/artifacts/qcrate_kv260.bit` and
`build/artifacts/qcrate_kv260.xsa`; synthesis and implementation must finish
without errors. Then regenerate SDT from that XSA. This stage intentionally
fails if SG or the 23-bit length is absent:

```bash
source /tools/Xilinx/PetaLinux/2024.2/settings.sh
python3 kv260/linux/petalinux/scripts/petalinux_flow.py configure

rg 'xlnx,(include-sg|sg-length-width)' \
  build/petalinux/sdt/qcrate-kv260/pl.dtsi
```

`configure` also transactionally invalidates the ignored PetaLinux
`components/plnx_workspace` and `project-spec/configs/pl.dtsi`. Lopper must
then regenerate the full `/plugin/` overlay from the new hardware description;
the flow audits its firmware name, SG enable, and 23-bit length. This avoids
silently packaging a workspace left over from an older XSA. If import fails,
the script restores the previous workspace and generated overlay for diagnosis.

Compile the three directly affected recipes before paying for a full image
build:

```bash
cd kv260/linux/petalinux/qcrate-kv260
petalinux-build -c qcrate-dma
petalinux-build -c qcrate-dma-tools
petalinux-build -c qcrate-firmware
cd /tools/fpga_projects/qcrate
```

After those focused builds pass, produce and audit the complete image:

```bash
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

For a fresh disposable card, the same process can be run after the Vivado build
as one host command. It repeats `configure`, so use it when end-to-end
reproducibility matters more than preserving the focused-stage time:

```bash
python3 kv260/linux/petalinux/scripts/petalinux_flow.py all \
  --device /dev/mmcblk0
```

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

Expected SG-build facts are:

```text
coherent buffer    : 1048576 bytes
DMA transfer limit : 8388607 bytes
maximum frame      : 262144 words
SG frame chaining  : yes
maximum SG frames  : 255
```

First rerun the backward-compatible one-frame path. Omitting `--words` now
selects 4096 automatically:

```bash
sudo qcrate-dma capture
```

Expected final lines include:

```text
PASS 1 capture(s), 4096 words each; every word verified
first/last         : 0x00000000 / 0x00000fff
completed frames   : 1
```

Exercise repeated channel teardown, rearming, stream reset, and buffer reuse:

```bash
sudo qcrate-dma capture --repeat 100
```

Then prove the new contract: one RTL start, eight pre-armed descriptors, eight
increasing frame IDs, and complete verification of all 32768 words:

```bash
sudo qcrate-dma capture-frames --words 4096 --frames 8
```

Expected final evidence includes:

```text
PASS 1 chain(s), 8 frames x 4096 words; every word verified
first/last         : 0x00000000 / 0x00070fff
completed frames   : 8
final frame/sample : 7 / 4095
```

Exercise the complete 1 MiB coherent buffer, then repeat smaller chains to test
descriptor reuse, termination cleanup, and frame-ID restart behavior:

```bash
sudo qcrate-dma capture-frames --words 4096 --frames 64
sudo qcrate-dma capture-frames --words 4096 --frames 8 --repeat 100
```

The 64-frame last word must be `0x003f0fff`. The accepted board run measured 35
stall cycles for 8 frames and 315 for 64 frames: exactly five cycles at each
inter-frame SG descriptor transition. This deterministic hardware backpressure
is protocol-legal because every word and boundary verifies. Future throughput
work should retain this measurement as the baseline and investigate any larger
or unstable count.

Save the final captured frame when offline inspection is useful:

```bash
sudo qcrate-dma capture-frames --words 4096 --frames 8 \
  --output /tmp/qcrate-frames.bin
od -Ax -tx4 -N 64 /tmp/qcrate-frames.bin
```

## Diagnostics

If `/dev/qcrate-dma` is absent, inspect module loading and deferred probe:

```bash
sudo modprobe qcrate_dma
sudo dmesg | tail -100
find /proc/device-tree -name 'qcrate-dma@a0010000' -print
```

If `qcrate-dma info` reports `SG frame chaining : no`, do not run
`capture-frames`. Confirm that the card contains the newly built bitstream and
that the live provider advertises SG and the 23-bit length:

```bash
find /proc/device-tree -path '*dma@a0000000*' -print
od -An -tx1 /proc/device-tree/axi/dma@a0000000/xlnx,sg-length-width
test -e /proc/device-tree/axi/dma@a0000000/xlnx,include-sg && echo SG-present
sudo dmesg | grep -Ei 'qcrate.*SG|xilinx.*dma'
```

Missing SG indicates a stale XSA, `pl.dtbo`, bitstream, or SD-card image. The
build audits are intended to catch the first two cases before deployment.

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

One observed SG failure accepted the complete Q-Crate frame (`DONE`, no stream
stalls) but never completed the DMA descriptor. The exported HWH showed
`C_SG_INCLUDE_STSCNTRL_STRM=1` although no AXI DMA control/status stream was
connected. Q-Crate does not use those Ethernet-oriented sideband channels, so
the tracked BD explicitly disables the feature and the SDT audits enforce that
hardware contract.

An RCU stall whose first provider frame is `xilinx_dma_stop_transfer` and whose
Q-Crate frame is at the beginning of `qcrate_run_capture` indicates an obsolete
driver that still terminates the idle SG channel before descriptor submission.
Rebuild and deploy `qcrate-dma`; do not treat this as a stream timeout because
the RTL `START` has not occurred yet.

## Acceptance boundary

The finite multi-frame milestone is complete when:

1. Vivado exports SG-enabled AXI DMA with a 23-bit length and a connected
   `M_AXI_SG` DDR path.
2. The generated DT reports SG, and both `xilinx_dma` and `qcrate_dma` probe
   without errors.
3. The accepted one-frame regression still passes at 4096 words.
4. One eight-frame command returns frame IDs `0..7`, correct `TLAST` boundaries,
   zero residue, and every expected word.
5. A 64-by-4096-word chain fills and verifies the complete 1 MiB buffer.
6. One hundred eight-frame chains pass without timeout, stale data, loss,
   duplication, or frame-order errors.
7. The fixed-platform clock and APB regression still passes.

All seven conditions were accepted on the KV260. The final run passed 100
independent 4096-word captures, an 8-frame chain ending at `0x00070fff`, a
64-frame chain ending at `0x003f0fff`, and 100 repeated 8-frame chains without
timeouts, stale data, loss, duplication, or frame-order errors.

## Deferred sustained-acquisition architectures

Finite SG capture is sufficient for the current triggered-shot workflow and is
the permanent regression interface. Q-Crate will proceed to OpenAMP, sequencing,
DSP, and UDP before adding another DMA ownership model. The UDP milestone must
measure whether capture and transmission can meet the selected operating point;
only that evidence should reopen this decision.

The maximum synthetic source rate is deliberately a stress case rather than a
system requirement:

```text
200 MHz x 32 bits      = 800 MB/s
4096-word frame        = 16 KiB every 20.48 us
maximum frame rate     = 48,828 frames/s
current 1 MiB buffer   = 64 frames, or about 1.31 ms at maximum rate
```

A 1 GbE link cannot continuously transport an 800 MB/s producer. No finite
buffer can repair a permanent rate mismatch; the eventual design must use an
appropriate sample rate, decimation, duty-cycled shots, backpressure, or an
explicit drop policy. The Q-Crate pattern source can pause safely on `TREADY`.
A real unthrottleable ADC would additionally require PL elasticity and a defined
overflow response.

The following alternatives were discussed and intentionally retained:

| Option | Appropriate use | Main benefit | Main cost or risk | Current decision |
| --- | --- | --- | --- | --- |
| Synchronous finite SG | Triggered shots and diagnostics | Proven ownership, recovery, and complete-buffer verification | Capture and userspace processing do not overlap | Accepted baseline |
| Asynchronous finite-SG bank pool | Overlapped capture, processing, and UDP without silent overwrite | Explicit `FREE -> FILLING -> READY -> USER_OWNED -> FREE` ownership; can use `poll()` and read-only `mmap()` | Inter-batch rearming gap; several banks provide only milliseconds at the stress rate | Recommended first extension if UDP measurements require overlap |
| DMAEngine cyclic ring | Truly gapless, audio-like sustained acquisition | No software rearming at ring wrap | DMA may overwrite unread data; torn-read prevention, period callbacks, interrupt rate, and coalescing require careful design | Deferred experiment, not the default production path |
| Linux IIO buffered device | A later real ADC/DDC instrument with channels, triggers, and timestamps | Standard Linux acquisition ABI and ecosystem | Framework integration is premature for the current pattern/shot model | Reconsider when real IQ channels exist |
| Custom AXI DMA ring provider | A measured requirement unsupported by `xilinx_dma` | Full descriptor, tail-pointer, and interrupt control | Reimplements vendor DMA ownership and recovery with substantial maintenance risk | Rejected unless DMAEngine is proven inadequate |

The exact Xilinx 2024.2 `xilinx_dma` provider advertises `DMA_CYCLIC` and
implements `dmaengine_prep_dma_cyclic()`. That confirms feasibility, not system
correctness. At one callback per 4096-word frame, the stress case approaches
48,828 callbacks per second, while the current ring wraps in about 1.31 ms.
Period sizing, interrupt coalescing, overwrite detection, userspace ownership,
and stop/recovery behavior would therefore require explicit characterization.

If the asynchronous finite-SG bank pool is selected later, preserve the current
synchronous ioctls and add a separate API with `START`, `DEQUEUE`, `RELEASE`,
`STATUS`, and `STOP`. Required counters include produced and consumed batches,
consumer-starvation events, bytes, elapsed time, stream stalls, and recovery
events. A free-bank shortage should pause before starting a new finite batch;
it is not a DMA overrun and must not be reported as one.

## References

- [Linux DMAEngine client API](https://docs.kernel.org/6.6/driver-api/dmaengine/client.html)
- [AMD: Linux DMA From User Space](https://xilinx-wiki.atlassian.net/wiki/spaces/A/pages/18842418/Linux+DMA+From+User+Space)
- [AMD/Xilinx 2024.2 AXI DMA Linux driver](https://github.com/Xilinx/linux-xlnx/blob/xlnx_rebase_v6.6_LTS_2024.2/drivers/dma/xilinx/xilinx_dma.c)
