#!/usr/bin/env python3
"""Create and build the generated Q-Crate R5-0 FreeRTOS Vitis workspace."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import vitis


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT / "build" / "vitis" / "workspace"
XSA = REPO_ROOT / "build" / "artifacts" / "qcrate_kv260.xsa"
OPENAMP_IPI_DTSI = REPO_ROOT / "kv260" / "vitis" / "qcrate_openamp_ipi.dtsi"
PLATFORM_NAME = "qcrate_r5_platform"
DOMAIN_NAME = "freertos_r5_0"
APP_NAME = "qcrate_r5"
OPENAMP_LIBRARIES = ("xiltimer", "libmetal", "openamp")
GENERATED_IPI_IRQ = "#define IPI_IRQ_VECT_ID         0x41"
SDT_IPI_IRQ = (
    "#define IPI_IRQ_VECT_ID         "
    "(XPAR_IPI1_INTERRUPTS & 0x0FFFU)"
)


def ensure_domain_libraries(domain: Any) -> None:
    """Add OpenAMP dependencies not already supplied by the domain BSP."""
    configured = {library["name"] for library in domain.get_libs()}
    for library in OPENAMP_LIBRARIES:
        if library not in configured:
            domain.set_lib(library)
            configured.add(library)


def replace_template_application() -> None:
    """Keep AMD's generated machine layer and replace only echo app logic."""
    app_root = WORKSPACE / APP_NAME
    candidates = list(app_root.rglob("rpmsg-echo.c"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one generated rpmsg-echo.c, found {len(candidates)}"
        )

    source_dir = candidates[0].parent
    shutil.copy2(
        REPO_ROOT / "kv260" / "r5_freertos" / "qcrate_rpmsg_service.c",
        source_dir / "rpmsg-echo.c",
    )
    shutil.copy2(
        REPO_ROOT / "kv260" / "r5_freertos" / "qcrate_rpmsg_service.h",
        source_dir / "rpmsg-echo.h",
    )
    shutil.copy2(
        REPO_ROOT / "common" / "protocol" / "qcrate_protocol.h",
        source_dir / "qcrate_protocol.h",
    )


def correct_generated_ipi_interrupt() -> None:
    """Derive the SPI-relative IPI vector expected by FreeRTOS and libmetal."""
    app_root = WORKSPACE / APP_NAME
    candidates = list(app_root.rglob("amd_platform_info.h"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one generated amd_platform_info.h, found {len(candidates)}"
        )

    header = candidates[0]
    contents = header.read_text(encoding="utf-8")
    if contents.count(GENERATED_IPI_IRQ) != 1:
        raise RuntimeError(
            "generated OpenAMP IPI interrupt definition changed; "
            "review amd_platform_info.h before updating the guarded correction"
        )

    include_guard = "#define _AMD_GENERATED_H_\n"
    if contents.count(include_guard) != 1:
        raise RuntimeError("generated OpenAMP header guard changed")

    contents = contents.replace(
        include_guard,
        f'{include_guard}\n#include "xparameters.h"\n',
        1,
    )
    contents = contents.replace(GENERATED_IPI_IRQ, SDT_IPI_IRQ, 1)
    header.write_text(contents, encoding="utf-8")


def main() -> None:
    if not XSA.is_file():
        raise FileNotFoundError(f"accepted XSA is missing: {XSA}")
    if not OPENAMP_IPI_DTSI.is_file():
        raise FileNotFoundError(
            f"OpenAMP IPI description is missing: {OPENAMP_IPI_DTSI}"
        )

    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    WORKSPACE.mkdir(parents=True)

    client = vitis.create_client()
    try:
        client.set_workspace(str(WORKSPACE))

        advanced_options = client.create_advanced_options_dict(
            user_dtsi=str(OPENAMP_IPI_DTSI)
        )
        platform = client.create_platform_component(
            name=PLATFORM_NAME,
            hw_design=str(XSA),
            advanced_options=advanced_options,
        )
        domain = platform.add_domain(
            name=DOMAIN_NAME,
            cpu="psu_cortexr5_0",
            # Unified Vitis uses this alias and resolves the freertos10_xilinx BSP.
            os="freertos",
        )
        ensure_domain_libraries(domain)
        platform.build()

        platform_xpfm = client.find_platform_in_repos(PLATFORM_NAME)
        if not platform_xpfm:
            raise RuntimeError("generated R5 platform was not found in Vitis repos")

        application = client.create_app_component(
            name=APP_NAME,
            platform=platform_xpfm,
            domain=DOMAIN_NAME,
            template="openamp_echo_test",
        )
        replace_template_application()
        correct_generated_ipi_interrupt()
        application.set_app_config(
            key="USER_COMPILE_WARNINGS_AS_ERRORS", values="TRUE"
        )
        application.build()
    finally:
        vitis.dispose()

    print(f"Q-Crate R5 workspace built at {WORKSPACE}")


if __name__ == "__main__":
    main()
