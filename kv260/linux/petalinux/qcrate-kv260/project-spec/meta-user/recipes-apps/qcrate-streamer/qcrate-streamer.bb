SUMMARY = "Q-Crate finite and sustained UDP data-plane sender"
DESCRIPTION = "Transmits one-shot or R5-triggered DMA bank-pool captures using the versioned Q-Crate Data Plane v1 protocol."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../kv260/linux/data_plane:"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../common/data_plane:"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../common/dma:"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../common/protocol:"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../kv260/linux/openamp:"

SRC_URI = "file://qcrate_streamer.c \
           file://qcrate_data_protocol.c \
           file://qcrate_data_protocol.h \
           file://qcrate_data_packetizer.c \
           file://qcrate_data_packetizer.h \
           file://qcrate_stream_profiles.h \
           file://qcrate_dma_uapi.h \
           file://qcrate_protocol.h \
           file://qcrate_rpmsg_client.c \
           file://qcrate_rpmsg_client.h \
          "

S = "${WORKDIR}"

do_compile() {
    ${CC} ${CPPFLAGS} ${CFLAGS} -std=c11 -Wall -Wextra -Werror \
        -I${S} \
        ${S}/qcrate_streamer.c \
        ${S}/qcrate_data_protocol.c \
        ${S}/qcrate_data_packetizer.c \
        ${S}/qcrate_rpmsg_client.c \
        -o qcrate-streamer ${LDFLAGS} -pthread
}

do_install() {
    install -d ${D}${bindir}
    install -m 0755 qcrate-streamer ${D}${bindir}/qcrate-streamer
}
