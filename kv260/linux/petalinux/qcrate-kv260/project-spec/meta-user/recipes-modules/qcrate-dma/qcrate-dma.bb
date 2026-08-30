SUMMARY = "Q-Crate DMAEngine capture driver"
DESCRIPTION = "Receive-only DMAEngine client for synchronous, triggered, and asynchronous bank-pooled finite SG Q-Crate capture."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../common/dma:"

SRC_URI = "file://Makefile \
           file://qcrate_dma.c \
           file://qcrate_dma_uapi.h \
          "

S = "${WORKDIR}"

inherit module

KERNEL_MODULE_AUTOLOAD += "qcrate_dma"
