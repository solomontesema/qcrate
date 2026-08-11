SUMMARY = "Q-Crate Linux diagnostic and platform-acceptance tools"
DESCRIPTION = "Installs the Q-Crate APB utility and staged first-boot acceptance tool."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://qcrate-apb \
           file://qcrate-first-boot \
          "

S = "${WORKDIR}"

RDEPENDS:${PN} += "python3"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/qcrate-apb ${D}${bindir}/qcrate-apb
    install -m 0755 ${WORKDIR}/qcrate-first-boot ${D}${bindir}/qcrate-first-boot
}
