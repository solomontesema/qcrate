# Out-of-context synthesis check for the generated DSP-2A FIR wrapper.

proc fail {message} {
    puts stderr "ERROR: $message"
    exit 2
}

if {$argc > 1} {
    fail "usage: synth_fir_ooc.tcl ?part?"
}

set part [expr {$argc == 1 ? [lindex $argv 0] : "xck26-sfvc784-2LV-c"}]
set script_dir [file dirname [file normalize [info script]]]
set repo_root [file normalize [file join $script_dir ../../..]]
set output_dir [file join $repo_root build vivado dsp_fir_ooc]
set report_dir [file join $output_dir reports]

# This directory contains only generated products from this focused check.
file delete -force $output_dir
file mkdir $report_dir
cd $repo_root

# An in-memory project gives IP Tcl a target part without creating a tracked
# Vivado project. synth_ip then creates the FIR Compiler OOC checkpoint.
create_project -in_memory qcrate_dsp_fir_ooc -part $part
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set qcrate_ip_output_dir [file join $output_dir generated_ip]
source [file join $script_dir create_fir_compiler.tcl]

set fir_ip [get_ips qcrate_fir_decim16_ip]
set fir_xci_path [file join $qcrate_ip_output_dir qcrate_fir_decim16_ip \
    qcrate_fir_decim16_ip.xci]
set fir_xci [get_files -quiet [file normalize $fir_xci_path]]
if {[llength $fir_xci] != 1} {
    fail "FIR Compiler XCI was not registered with the in-memory project"
}
set_property GENERATE_SYNTH_CHECKPOINT true $fir_xci
synth_ip $fir_ip -force

add_files -norecurse [list \
    [file join $repo_root rtl dsp qcrate_fir_quantizer.sv] \
    [file join $script_dir qcrate_fir_decim16.sv]]
read_xdc [file join $repo_root rtl dsp qcrate_dsp_ooc.xdc]
update_compile_order -fileset sources_1

synth_design -mode out_of_context -top qcrate_fir_decim16 -part $part

set black_boxes [get_cells -quiet -hierarchical -filter {IS_BLACKBOX == 1}]
if {[llength $black_boxes] != 0} {
    fail "synthesized FIR wrapper contains black boxes: $black_boxes"
}

set dsp_cells [get_cells -quiet -hierarchical -filter {REF_NAME =~ DSP48*}]
if {[llength $dsp_cells] == 0} {
    fail "synthesized FIR wrapper contains no DSP48 primitives"
}
set bram_cells [get_cells -quiet -hierarchical \
    -filter {REF_NAME =~ RAMB18* || REF_NAME =~ RAMB36*}]

report_utilization -hierarchical -hierarchical_depth 10 \
    -file [file join $report_dir utilization.rpt]
report_timing_summary -delay_type max -max_paths 10 \
    -file [file join $report_dir timing_summary.rpt]
write_checkpoint -force [file join $output_dir qcrate_fir_decim16.dcp]

set timing_path [get_timing_paths -delay_type max -max_paths 1]
if {[llength $timing_path] == 0} {
    fail "no setup timing path was reported"
}
set worst_slack [get_property SLACK $timing_path]
puts [format "INFO: DSP-2A FIR OOC worst setup slack: %.3f ns" $worst_slack]
puts "INFO: DSP-2A FIR OOC DSP48 primitives: [llength $dsp_cells]"
puts "INFO: DSP-2A FIR OOC block-RAM primitives: [llength $bram_cells]"
if {$worst_slack < 0.0} {
    fail "DSP-2A FIR does not meet the 200 MHz OOC timing target"
}

puts "PASS: DSP-2A FIR OOC synthesis and 200 MHz timing"
exit 0
