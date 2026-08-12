SUMMARY = "Q-Crate fixed-platform FPGA firmware"
DESCRIPTION = "Builds and installs the Q-Crate bitstream binary and matching full device-tree overlay from SDT artifacts."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

DEPENDS = "sdt-artifacts dtc-native bootgen-native"

PACKAGE_ARCH = "${MACHINE_ARCH}"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../configs:"
SRC_URI = "file://pl.dtsi \
           file://qcrate-clock-parents.dtsi \
           file://qcrate-dma-client.dtsi \
          "

S = "${WORKDIR}"
B = "${WORKDIR}/build"

QCRATE_SDT_DIR = "${RECIPE_SYSROOT}${datadir}/sdt/${MACHINE}"
QCRATE_BIT = "${QCRATE_SDT_DIR}/qcrate_kv260.bit"
QCRATE_PL_DTSI = "${WORKDIR}/pl.dtsi"
QCRATE_CLOCK_PARENTS_DTSI = "${WORKDIR}/qcrate-clock-parents.dtsi"
QCRATE_DMA_CLIENT_DTSI = "${WORKDIR}/qcrate-dma-client.dtsi"
QCRATE_COMBINED_PL_DTS = "${B}/qcrate-pl.dts"

do_configure[dirs] = "${B}"

do_configure() {
    test -s ${QCRATE_BIT}
    test -s ${QCRATE_PL_DTSI}
    test -s ${QCRATE_CLOCK_PARENTS_DTSI}
    test -s ${QCRATE_DMA_CLIENT_DTSI}

    grep -Eq 'xlnx,include-sg[[:space:]]*(;|=[[:space:]]*<[[:space:]]*(0x)?0*1[[:space:]]*>)' \
        ${QCRATE_PL_DTSI} || bbfatal "XSA-matched pl.dtsi does not enable AXI DMA scatter-gather"
    grep -Eq 'xlnx,sg-length-width[[:space:]]*=[[:space:]]*<[[:space:]]*(23|0x0*17)[[:space:]]*>' \
        ${QCRATE_PL_DTSI} || bbfatal "XSA-matched pl.dtsi does not use the required 23-bit SG length"
    grep -Eq 'xlnx,sg-include-stscntrl-strm[[:space:]]*=[[:space:]]*<[[:space:]]*(0x)?0+[[:space:]]*>' \
        ${QCRATE_PL_DTSI} || bbfatal "XSA-matched pl.dtsi enables the unused AXI DMA control/status stream"

    printf 'all:\n{\n  %s\n}\n' "${QCRATE_BIT}" > ${B}/qcrate_kv260.bif
    cat ${QCRATE_PL_DTSI} ${QCRATE_CLOCK_PARENTS_DTSI} \
        ${QCRATE_DMA_CLIENT_DTSI} \
        > ${QCRATE_COMBINED_PL_DTS}
}

do_compile() {
    bootgen -arch zynqmp -w \
        -image ${B}/qcrate_kv260.bif \
        -o ${B}/qcrate_kv260.bit.bin

    dtc -@ -I dts -O dtb \
        -o ${B}/pl.dtbo \
        ${QCRATE_COMBINED_PL_DTS}

    test -s ${B}/qcrate_kv260.bit.bin
    test -s ${B}/pl.dtbo
}

do_install() {
    install -d ${D}${nonarch_base_libdir}/firmware/xilinx/base
    install -m 0644 ${B}/qcrate_kv260.bit.bin \
        ${D}${nonarch_base_libdir}/firmware/xilinx/base/qcrate_kv260.bit.bin
    install -m 0644 ${B}/pl.dtbo \
        ${D}${nonarch_base_libdir}/firmware/xilinx/base/pl.dtbo
}

FILES:${PN} += "${nonarch_base_libdir}/firmware/xilinx/base"
