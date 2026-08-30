SUMMARY = "Q-Crate OpenAMP control service"
DESCRIPTION = "Installs the R5-0 FreeRTOS firmware, remoteproc service, and rpmsg_char userspace client."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

PACKAGE_ARCH = "${MACHINE_ARCH}"
# The firmware is a Cortex-R5 ELF intentionally carried in an AArch64 package.
INSANE_SKIP:${PN} += "arch"
INHIBIT_PACKAGE_STRIP = "1"

SRC_URI = "file://qcrate_control.c \
           file://qcrate_protocol.h \
           file://qcrate_rpmsg_client.c \
           file://qcrate_rpmsg_client.h \
           file://qcrate-r5.elf \
           file://qcrate-r5-remoteproc \
           file://qcrate-r5.service \
          "

S = "${WORKDIR}"

inherit systemd

RDEPENDS:${PN} += "kernel-module-zynqmp-r5-remoteproc \
                   kernel-module-virtio-rpmsg-bus \
                   kernel-module-rpmsg-ns \
                   kernel-module-rpmsg-ctrl \
                   kernel-module-rpmsg-char \
                  "

SYSTEMD_SERVICE:${PN} = "qcrate-r5.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_compile() {
    ${CC} ${CFLAGS} ${CPPFLAGS} -Wall -Wextra -Werror \
        -I${WORKDIR} ${WORKDIR}/qcrate_control.c \
        ${WORKDIR}/qcrate_rpmsg_client.c \
        -o qcrate-control ${LDFLAGS}
}

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${B}/qcrate-control ${D}${bindir}/qcrate-control
    install -m 0755 ${WORKDIR}/qcrate-r5-remoteproc \
        ${D}${bindir}/qcrate-r5-remoteproc

    install -d ${D}${nonarch_base_libdir}/firmware/qcrate
    install -m 0644 ${WORKDIR}/qcrate-r5.elf \
        ${D}${nonarch_base_libdir}/firmware/qcrate/qcrate-r5.elf

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/qcrate-r5.service \
        ${D}${systemd_system_unitdir}/qcrate-r5.service
}

FILES:${PN} += "${nonarch_base_libdir}/firmware/qcrate/qcrate-r5.elf \
                ${systemd_system_unitdir}/qcrate-r5.service \
               "
