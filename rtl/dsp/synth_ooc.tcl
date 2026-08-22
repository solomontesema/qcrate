# Out-of-context synthesis check for the portable DSP-1 frontend.

proc fail {message} {
    puts stderr "ERROR: $message"
    exit 2
}

if {$argc > 1} {
    fail "usage: synth_ooc.tcl ?part?"
}

set part [expr {$argc == 1 ? [lindex $argv 0] : "xck26-sfvc784-2LV-c"}]
set script_dir [file dirname [file normalize [info script]]]
set repo_root [file normalize [file join $script_dir ../..]]
set output_dir [file join $repo_root build vivado dsp_ooc]

file mkdir $output_dir
cd $repo_root

read_verilog -sv [list \
    rtl/dsp/qcrate_nco.sv \
    rtl/dsp/qcrate_ddc_mixer.sv \
    rtl/dsp/qcrate_dsp_frontend.sv]
read_xdc rtl/dsp/qcrate_dsp_ooc.xdc

synth_design -mode out_of_context -top qcrate_dsp_frontend -part $part
report_utilization -file [file join $output_dir utilization.rpt]
report_timing_summary -delay_type max -max_paths 10 \
    -file [file join $output_dir timing_summary.rpt]
write_checkpoint -force [file join $output_dir qcrate_dsp_frontend.dcp]

set timing_path [get_timing_paths -delay_type max -max_paths 1]
if {[llength $timing_path] == 0} {
    fail "no setup timing path was reported"
}
set worst_slack [get_property SLACK $timing_path]
puts [format "INFO: DSP-1 OOC worst setup slack: %.3f ns" $worst_slack]
if {$worst_slack < 0.0} {
    fail "DSP-1 does not meet the 200 MHz OOC timing target"
}

puts "PASS: DSP-1 OOC synthesis and 200 MHz timing"
exit 0
