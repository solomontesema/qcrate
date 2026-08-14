# Q-Crate KV260 platform

This is the starting point for understanding how Q-Crate is built, booted, and
run on the KV260. It records both the experiments used to learn the platform and
the deployment flow intended for the finished system.

## Current direction

Q-Crate uses a fixed hardware platform. The processing-system configuration,
PL clocks, bitstream, Linux device tree, and software must describe the same
hardware contract.

```text
Vivado RTL and block design
        |
        v
Q-Crate XSA and bitstream
        |
        v
PetaLinux platform
  - FSBL and PS initialization
  - PMU firmware and TF-A
  - U-Boot
  - Linux kernel and device tree
  - root filesystem
  - Q-Crate bitstream in BOOT.BIN
        |
        v
Vitis platform
  - A53 Linux application
  - R5 FreeRTOS/OpenAMP application
```

The PetaLinux stage is not throwaway work. Its initial APB-only bring-up is a
bounded learning stage, but the resulting boot configuration, device tree,
kernel configuration, root filesystem, and sysroot become part of the final
PetaLinux plus Vitis platform.

## Deployment stages

| Stage | Status | Purpose | Normal long-term use? |
|---|---|---|---|
| Vivado Tcl/RTL build | Active | Reproduce the hardware and export the XSA | Yes |
| Direct FPGA Manager/`fpgautil` load | Closed experiment | Learn the lowest-level runtime bitstream path | No |
| Kria Ubuntu/`xmutil` package | Closed experiment | Learn bitstream plus device-tree-overlay deployment | No |
| Fixed PetaLinux boot platform | Complete | Boot the matching bitstream, PS clocks, and static device tree | Yes |
| Linux DMAEngine capture | Complete | Move stream frames into coherent DDR buffers and verify every word | Yes |
| Finite SG multi-frame capture | Complete | Pre-arm framed buffers and receive increasing frame IDs from one stream start | Yes |
| R5 FreeRTOS/OpenAMP vertical slice | Complete | Linux remoteproc/RPMsg control service with bounded R5 APB diagnostics | Yes |
| PetaLinux plus Vitis applications | Intended deliverable | Integrate A53 Linux and later R5/OpenAMP software | Yes |

Closed experiments remain documented because their commands and failure modes
are useful for diagnosis. A closed experiment is not the current deployment
recommendation.

## Clock ownership decision

The Q-Crate hardware contract is:

```text
pl_clk0 = 200 MHz  stream, DMA payload, and DSP domain
pl_clk1 = 100 MHz  AXI-Lite, APB, and control domain
```

For the fixed platform, the FSBL/processing-system initialization generated from
the Q-Crate hardware handoff owns these PS-to-PL clocks. Runtime software must
not assume that loading a bitstream also reapplies the complete PS
configuration.

An MMCM or PLL is appropriate when the design genuinely needs PL clock
synthesis, phase control, jitter filtering, or a clock not available from the
PS. It is not the default workaround for a boot platform that initialized the
wrong PS fabric-clock rate.

For a future DFX platform, the static shell must own the clock contract and each
reconfigurable module must obey it.

## Software access progression

Q-Crate deliberately progresses through increasingly complete software paths:

1. `/dev/mem` APB access proves the physical address path.
2. A fixed PetaLinux image proves boot, clocks, reset, device tree, and repeated
   APB access.
3. A Linux driver or UIO-style interface replaces unrestricted `/dev/mem`
   access.
4. DMA buffer ownership and cache coherency are handled by a kernel-supported
   interface.
5. Vitis packages the A53 Linux application and R5 FreeRTOS/OpenAMP
   firmware against the same platform definition.

Passing an early stage does not make its diagnostic access method the final
software architecture.

## Documentation map

- `hw/README.md`: RTL simulation, Vivado diagnostics, clocks, resets, and ILA.
- `linux/README.md`: completed Kria Ubuntu, FPGA Manager, and `xmutil` chapter.
- `linux/petalinux/README.md`: accepted fixed-platform bring-up and its
  relationship to the final Vitis platform.
- `linux/dma/README.md`: DMAEngine driver, coherent capture buffer, userspace
  verification, and target diagnostics.
- `linux/network/README.md`: wired topology, addressing, throughput baseline,
  packet capture, and recorded network acceptance evidence.
- `linux/openamp/README.md`: R5-0 FreeRTOS, Linux remoteproc, VirtIO RPMsg,
  shared protocol, reproducible Vitis flow, and board acceptance.
- `../docs/ARCHITECTURE.md`: system architecture and hardware data/control paths.
- `../PLANS.md`: active implementation checklist.
