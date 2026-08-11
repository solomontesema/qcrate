# Q-Crate is a fixed platform whose full FPGA region is owned by
# qcrate-pl-load.service. Keep the DFX tools installed without enabling the
# vendor daemon. qcrate-pl-loader installs the explicit systemd mask because
# the 2024.2 systemd class accepts "mask" but does not create the mask link.
SYSTEMD_AUTO_ENABLE:${PN} = "disable"
