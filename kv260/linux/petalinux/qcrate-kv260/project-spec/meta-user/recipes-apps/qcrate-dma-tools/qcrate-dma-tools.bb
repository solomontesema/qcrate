SUMMARY = "Q-Crate DMA capture and verification tool"
DESCRIPTION = "Requests synchronous, triggered, or bank-pooled Q-Crate S2MM captures and verifies every deterministic stream word."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit python3-dir

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../host/dsp_model:"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../host/dsp_model/configs:"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../../../rtl/dsp/tables:"

SRC_URI = "file://qcrate-dma \
           file://qcrate_dsp_reference.py \
           file://tone_1mhz.json \
           file://sine_quarter_q1_15.mem \
           file://fir_decim16_q1_17.hex \
          "

S = "${WORKDIR}"

RDEPENDS:${PN} += "python3 qcrate-tools"

FILES:${PN} += "${PYTHON_SITEPACKAGES_DIR} \
                ${datadir}/qcrate \
               "

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/qcrate-dma ${D}${bindir}/qcrate-dma

    install -d ${D}${PYTHON_SITEPACKAGES_DIR}
    install -m 0644 ${WORKDIR}/qcrate_dsp_reference.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/qcrate_dsp_reference.py

    install -d ${D}${datadir}/qcrate/dsp
    install -m 0644 ${WORKDIR}/tone_1mhz.json \
        ${D}${datadir}/qcrate/dsp/tone_1mhz.json
    install -m 0644 ${WORKDIR}/sine_quarter_q1_15.mem \
        ${D}${datadir}/qcrate/dsp/sine_quarter_q1_15.mem
    install -m 0644 ${WORKDIR}/fir_decim16_q1_17.hex \
        ${D}${datadir}/qcrate/dsp/fir_decim16_q1_17.hex
}
