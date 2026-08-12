SUMMARY = "Q-Crate DMA capture and verification tool"
DESCRIPTION = "Requests single or finite SG-chained S2MM captures from qcrate-dma and verifies every deterministic stream word."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://qcrate-dma"

S = "${WORKDIR}"

RDEPENDS:${PN} += "python3"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/qcrate-dma ${D}${bindir}/qcrate-dma
}
