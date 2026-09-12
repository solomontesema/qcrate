SUMMARY = "Q-Crate instrument device access policy"
DESCRIPTION = "Grants the dedicated Q-Crate operator group access to instrument DMA and RPMsg endpoints."
LICENSE = "CLOSED"

SRC_URI = "file://70-qcrate-access.rules"

S = "${WORKDIR}"

RDEPENDS:${PN} += "udev"

do_install() {
    install -d ${D}${sysconfdir}/udev/rules.d
    install -m 0644 ${WORKDIR}/70-qcrate-access.rules \
        ${D}${sysconfdir}/udev/rules.d/70-qcrate-access.rules
}

FILES:${PN} += "${sysconfdir}/udev/rules.d/70-qcrate-access.rules"
