SUMMARY = "Q-Crate fixed-platform FPGA Manager boot loader"
DESCRIPTION = "Loads the XSA-derived Q-Crate bitstream and matching full device-tree overlay during boot."
LICENSE = "CLOSED"

SRC_URI = "file://qcrate-load-pl \
           file://qcrate-pl-load.service \
          "

S = "${WORKDIR}"

inherit systemd

RDEPENDS:${PN} += "fpga-manager-script"

SYSTEMD_SERVICE:${PN} = "qcrate-pl-load.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/qcrate-load-pl ${D}${bindir}/qcrate-load-pl

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/qcrate-pl-load.service \
        ${D}${systemd_system_unitdir}/qcrate-pl-load.service

    install -d ${D}${sysconfdir}/systemd/system
    ln -s /dev/null ${D}${sysconfdir}/systemd/system/dfx-mgr.service
}

FILES:${PN} += "${systemd_system_unitdir}/qcrate-pl-load.service \
                ${sysconfdir}/systemd/system/dfx-mgr.service \
               "
