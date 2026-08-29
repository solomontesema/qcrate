SUMMARY = "Q-Crate finite-shot UDP data-plane sender"
DESCRIPTION = "Captures a finite DMA SG chain and transmits the versioned Q-Crate Data Plane v1 protocol."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../kv260/linux/data_plane:"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../common/data_plane:"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../common/dma:"

SRC_URI = "file://qcrate_streamer.c \
           file://qcrate_data_protocol.c \
           file://qcrate_data_protocol.h \
           file://qcrate_data_packetizer.c \
           file://qcrate_data_packetizer.h \
           file://qcrate_stream_profiles.h \
           file://qcrate_dma_uapi.h \
          "

S = "${WORKDIR}"

do_compile() {
    ${CC} ${CPPFLAGS} ${CFLAGS} -std=c11 -Wall -Wextra -Werror \
        -I${S} \
        ${S}/qcrate_streamer.c \
        ${S}/qcrate_data_protocol.c \
        ${S}/qcrate_data_packetizer.c \
        -o qcrate-streamer ${LDFLAGS}
}

do_install() {
    install -d ${D}${bindir}
    install -m 0755 qcrate-streamer ${D}${bindir}/qcrate-streamer
}
