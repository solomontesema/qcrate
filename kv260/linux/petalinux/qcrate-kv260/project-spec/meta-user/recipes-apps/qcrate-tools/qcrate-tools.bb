SUMMARY = "Q-Crate Linux diagnostic, sequencer, and acceptance tools"
DESCRIPTION = "Installs Q-Crate APB, pulse-sequencer, and first-boot tools."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://qcrate-apb \
           file://qcrate-sequence \
           file://qcrate-first-boot \
          "

S = "${WORKDIR}"

RDEPENDS:${PN} += "python3 python3-ctypes"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/qcrate-apb ${D}${bindir}/qcrate-apb
    install -m 0755 ${WORKDIR}/qcrate-sequence ${D}${bindir}/qcrate-sequence
    install -m 0755 ${WORKDIR}/qcrate-first-boot ${D}${bindir}/qcrate-first-boot
}
