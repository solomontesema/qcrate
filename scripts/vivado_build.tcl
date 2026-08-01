# Q-Crate Vivado batch driver.
# vivado -mode batch -source scripts/vivado_build.tcl \
#   -tclargs <manifest.tcl> <project|synth|impl|bitstream|xsa|all>

proc fail {msg} { puts stderr "ERROR: $msg"; exit 2 }
proc rank {stage} {
    switch -- $stage {
        project { return 0 }
        synth { return 1 }
        impl { return 2 }
        bitstream { return 3 }
        xsa - all { return 4 }
        default { fail "unsupported stage: $stage" }
    }
}
proc check_run {name} {
    set status [get_property STATUS [get_runs $name]]
    puts "INFO: $name status: $status"
    if {[string match "*ERROR*" $status] || [string match "*Failed*" $status]} {
        fail "$name failed: $status"
    }
}

if {$argc != 2} { fail "expected <manifest.tcl> <stage>" }
set manifest [file normalize [lindex $argv 0]]
set stage [lindex $argv 1]
set stage_rank [rank $stage]
if {![file exists $manifest]} { fail "missing manifest: $manifest" }
source $manifest

file mkdir $cfg_build_dir
file mkdir $cfg_artifact_dir
set project_dir [file join $cfg_build_dir project]
create_project -force $cfg_project_name $project_dir -part $cfg_part
if {$cfg_board_part ne ""} {
    set_property board_part $cfg_board_part [current_project]
}
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]

if {[llength $cfg_rtl_files] == 0} { fail "no RTL files in manifest" }
add_files -norecurse $cfg_rtl_files
if {[llength $cfg_xdc_files] > 0} {
    add_files -fileset constrs_1 -norecurse $cfg_xdc_files
}

# Recreate the validated IP Integrator block design.
source $cfg_bd_tcl
set bd_files [get_files -quiet *.bd]
if {[llength $bd_files] == 0} { fail "BD Tcl created no .bd file" }
if {[current_bd_design -quiet] ne ""} {
    validate_bd_design
    save_bd_design
}
generate_target all $bd_files

# qcrate_top directly instantiates design_1; the BD wrapper is not top-level.
set_property top $cfg_top [get_filesets sources_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1

if {$stage_rank == 0} {
    puts "INFO: project generated; synthesis not requested"
    exit 0
}

launch_runs synth_1 -jobs $cfg_jobs
wait_on_run synth_1
check_run synth_1
if {$stage_rank == 1} { exit 0 }

launch_runs impl_1 -jobs $cfg_jobs
wait_on_run impl_1
check_run impl_1
open_run impl_1

report_timing_summary -file [file join $cfg_artifact_dir timing_summary.rpt]
report_utilization -file [file join $cfg_artifact_dir utilization.rpt]
report_methodology -file [file join $cfg_artifact_dir methodology.rpt]
report_drc -file [file join $cfg_artifact_dir drc.rpt]
if {$stage_rank == 2} { exit 0 }

set bit_file [file join $cfg_artifact_dir "${cfg_project_name}.bit"]
write_bitstream -force $bit_file
if {$stage_rank == 3} { exit 0 }

set xsa_file [file join $cfg_artifact_dir "${cfg_project_name}.xsa"]
write_hw_platform -fixed -include_bit -force -file $xsa_file
puts "INFO: build complete: $bit_file and $xsa_file"
