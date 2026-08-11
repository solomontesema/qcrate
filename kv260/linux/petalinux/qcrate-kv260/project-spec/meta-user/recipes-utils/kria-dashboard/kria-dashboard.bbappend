# The vendor dashboard has Wants=dfx-mgr.service. Autostarting it would bypass
# dfx-mgr's disabled state and race Q-Crate for the full FPGA region.
SYSTEMD_AUTO_ENABLE:${PN} = "disable"
