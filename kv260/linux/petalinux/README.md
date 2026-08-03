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
Next milestone: project creation and toolchain preflight
Validated so far: Vivado XSA, bitstream, APB hardware, and Ubuntu smoke test
Not started here: PetaLinux project generation or image build
```

Do not add commands to this README merely because they look plausible. Add each
command when it is run, record the tool version, and state its observed output
or success condition. This keeps the document usable as a reproducible build
record rather than a collection of unverified snippets.

## Stage 1: fixed-platform acceptance

The first PetaLinux milestone is complete only when all of these are true:

1. PetaLinux 2024.2 accepts `build/artifacts/qcrate_kv260.xsa`.
2. The project builds FSBL, PMU firmware, TF-A, U-Boot, Linux, the device tree,
   and root filesystem without relying on GUI state.
3. `BOOT.BIN` contains the matching Q-Crate bitstream.
4. The KV260 boots from a separate SD card with serial-console logs captured.
5. Linux reports `pl_clk0` at 200 MHz and `pl_clk1` at 100 MHz before any
   Q-Crate userspace program runs.
6. `DEVICE_ID`, `VERSION`, and `SYS.SCRATCH` pass repeated APB tests.
7. Rebooting produces the same result without `xmutil`, FPGA Manager commands,
   or manual register writes.
8. The project can export an SDK/sysroot suitable for an A53 Linux application.

DMA is intentionally outside this first acceptance gate. Boot and control-path
correctness must be stable before adding DMA buffer ownership, cache coherency,
and driver questions.

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
