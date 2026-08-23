# DSP-2B complete-chain simulation with generated FIR Compiler IP.

set script_dir [file dirname [file normalize [info script]]]
set repo_root [file normalize [file join $script_dir ../../..]]
set project_dir [file join $repo_root build vivado dsp2b_xsim]
set vector_dir [file join $repo_root build dsp fir_vectors]
set pass_file [file join $vector_dir dsp2b.pass]
set started_file [file join $vector_dir dsp2b.started]
set sine_lut_file [file join $repo_root rtl dsp tables sine_quarter_q1_15.mem]
if {![file exists $sine_lut_file]} {
    error "missing NCO sine lookup table: $sine_lut_file"
}
set open_waveform 0
if {$argc > 1} {
    error "usage: run_dsp2b_xsim.tcl ?open-waveform?"
}
if {$argc == 1} {
    if {[lindex $argv 0] ne "open-waveform"} {
        error "usage: run_dsp2b_xsim.tcl ?open-waveform?"
    }
    set open_waveform 1
}
file delete -force $pass_file
file delete -force $started_file

create_project -force qcrate_dsp2b_xsim $project_dir \
    -part xck26-sfvc784-2LV-c
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]

source [file join $script_dir create_fir_compiler.tcl]
add_files -norecurse [list \
    [file join $repo_root rtl dsp qcrate_nco.sv] \
    [file join $repo_root rtl dsp qcrate_ddc_mixer.sv] \
    [file join $repo_root rtl dsp qcrate_dsp_frontend.sv] \
    [file join $repo_root rtl dsp qcrate_synthetic_source.sv] \
    [file join $repo_root rtl dsp qcrate_fir_quantizer.sv] \
    [file join $script_dir qcrate_fir_decim16.sv] \
    [file join $repo_root rtl dsp qcrate_dsp_chain.sv] \
    [file join $repo_root kv260 hw rtl qcrate_stream_engine.sv]]
add_files -fileset sim_1 -norecurse \
    [file join $repo_root rtl tb qcrate_dsp_stream_tb.sv]
set_property top qcrate_dsp_stream_tb [get_filesets sim_1]
set_property generic [list "SINE_LUT_FILE=$sine_lut_file"] \
    [get_filesets sim_1]
set_property -name {xsim.simulate.xsim.more_options} \
    -value "-testplusarg VECTOR_DIR=$vector_dir" \
    -objects [get_filesets sim_1]
set_property xsim.elaborate.debug_level typical [get_filesets sim_1]
set_property xsim.simulate.runtime 0ns [get_filesets sim_1]

generate_target simulation [get_ips qcrate_fir_decim16_ip]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
launch_simulation -simset sim_1 -mode behavioral
run all
if {![file exists $pass_file]} {
    error "DSP-2B testbench did not create its success sentinel"
}
puts "PASS: DSP-2B complete-chain XSim flow"
if {$open_waveform} {
    puts "INFO: waveform session remains open in Vivado"
    return
}
close_sim
exit
