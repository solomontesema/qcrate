# Q-Crate Vivado batch driver.
# vivado -mode batch -source scripts/vivado_build.tcl \
#   -tclargs <manifest.tcl> <project|synth|impl|bitstream|xsa|export|all>

proc fail {msg} { puts stderr "ERROR: $msg"; exit 2 }
proc rank {stage} {
    switch -- $stage {
        project { return 0 }
        synth { return 1 }
        impl { return 2 }
        bitstream { return 3 }
        xsa { return 4 }
        export { return 5 }
        all { return 6 }
        default { fail "unsupported stage: $stage" }
    }
}
proc run_status {name} {
    return [get_property STATUS [get_runs $name]]
}
proc check_run {name} {
    set status [run_status $name]
    puts "INFO: $name status: $status"
    if {[string match "*ERROR*" $status] || [string match "*Failed*" $status]} {
        fail "$name failed: $status"
    }
}
proc run_needs_refresh {name} {
    if {[catch {set refresh [get_property NEEDS_REFRESH [get_runs $name]]}]} {
        return 0
    }
    return [expr {$refresh eq "1" || $refresh eq "true" || $refresh eq "TRUE"}]
}
proc run_not_current {name} {
    if {[catch {set current [get_property IS_CURRENT [get_runs $name]]}]} {
        return 0
    }
    return [expr {$current eq "0" || $current eq "false" || $current eq "FALSE"}]
}
proc run_status_matches {name patterns} {
    set status [run_status $name]
    foreach pattern $patterns {
        if {[string match $pattern $status]} {
            return 1
        }
    }
    return 0
}
proc run_bit_files {} {
    set impl_run [get_runs impl_1]
    set impl_run_dir [get_property DIRECTORY $impl_run]
    return [glob -nocomplain [file join $impl_run_dir "*.bit"]]
}
proc ensure_files_in_fileset {files fileset} {
    foreach file $files {
        if {[llength [get_files -quiet $file]] == 0} {
            puts "INFO: adding missing project file: $file"
            add_files -fileset $fileset -norecurse $file
        }
    }
}
proc configure_project {} {
    global cfg_board_part cfg_top cfg_verilog_defines

    if {$cfg_board_part ne ""} {
        set_property board_part $cfg_board_part [current_project]
    }
    set_property target_language Verilog [current_project]
    set_property simulator_language Mixed [current_project]
    set_property XPM_LIBRARIES {XPM_CDC} [current_project]
    set_property top $cfg_top [get_filesets sources_1]
    set_property verilog_define $cfg_verilog_defines [get_filesets sources_1]
}
proc create_project_from_manifest {} {
    global cfg_project_name cfg_part cfg_build_dir cfg_bd_tcl cfg_rtl_files cfg_data_files cfg_xdc_files

    set project_dir [file join $cfg_build_dir project]
    create_project -force $cfg_project_name $project_dir -part $cfg_part
    configure_project

    if {[llength $cfg_rtl_files] == 0} { fail "no RTL files in manifest" }
    add_files -norecurse $cfg_rtl_files
    if {[llength $cfg_data_files] > 0} {
        add_files -norecurse $cfg_data_files
    }
    if {[llength $cfg_xdc_files] > 0} {
        add_files -fileset constrs_1 -norecurse $cfg_xdc_files
    }

    # Recreate the validated IP Integrator block design.
    source $cfg_bd_tcl
    set root_bd_name [file rootname [file tail $cfg_bd_tcl]]
    set bd_files [get_files -quiet "${root_bd_name}.bd"]
    if {[llength $bd_files] == 0} { fail "BD Tcl created no root .bd file named ${root_bd_name}.bd" }
    if {[llength $bd_files] > 1} { fail "found multiple root .bd files named ${root_bd_name}.bd: $bd_files" }
    if {[current_bd_design -quiet] ne ""} {
        validate_bd_design
        save_bd_design
    }
    generate_target all $bd_files

    update_compile_order -fileset sources_1
    update_compile_order -fileset sim_1
}
proc open_or_create_project {} {
    global cfg_project_name cfg_build_dir cfg_rtl_files cfg_data_files cfg_xdc_files stage

    set project_dir [file join $cfg_build_dir project]
    set project_xpr [file join $project_dir "${cfg_project_name}.xpr"]
    if {$stage eq "export" && ![file exists $project_xpr]} {
        fail "export stage requires an existing Vivado project: $project_xpr"
    }
    if {$stage ne "all" && [file exists $project_xpr]} {
        puts "INFO: reusing existing Vivado project: $project_xpr"
        open_project $project_xpr
        configure_project
        ensure_files_in_fileset $cfg_rtl_files sources_1
        if {[llength $cfg_data_files] > 0} {
            ensure_files_in_fileset $cfg_data_files sources_1
        }
        if {[llength $cfg_xdc_files] > 0} {
            ensure_files_in_fileset $cfg_xdc_files constrs_1
        }
        update_compile_order -fileset sources_1
        update_compile_order -fileset sim_1
    } else {
        if {$stage eq "all"} {
            puts "INFO: clean build requested; recreating Vivado project"
        }
        create_project_from_manifest
    }
}
proc run_synthesis_if_needed {} {
    set complete [run_status_matches synth_1 {"*Complete!*"}]
    set stale [expr {[run_needs_refresh synth_1] || [run_not_current synth_1]}]
    if {$complete && !$stale} {
        puts "INFO: synth_1 already complete; skipping synthesis"
        return 0
    }

    if {$stale} {
        puts "INFO: synth_1 is stale; resetting synthesis run"
        reset_run synth_1
    }
    launch_runs synth_1 -jobs $::cfg_jobs
    wait_on_run synth_1
    check_run synth_1
    return 1
}
proc run_implementation_if_needed {need_bitstream synth_ran} {
    set impl_complete [run_status_matches impl_1 {"*route_design Complete!*" "*write_bitstream Complete!*"}]
    set bit_complete [expr {[llength [run_bit_files]] == 1}]
    set stale [expr {$synth_ran || [run_needs_refresh impl_1] || [run_not_current impl_1]}]

    if {$impl_complete && !$stale && (!$need_bitstream || $bit_complete)} {
        puts "INFO: impl_1 already complete; skipping implementation"
        return 0
    }

    if {$stale} {
        puts "INFO: impl_1 is stale; resetting implementation run"
        reset_run impl_1
    }

    if {$need_bitstream} {
        launch_runs impl_1 -to_step write_bitstream -jobs $::cfg_jobs
    } else {
        launch_runs impl_1 -jobs $::cfg_jobs
    }
    wait_on_run impl_1
    check_run impl_1
    return 1
}
proc open_impl_and_write_reports {} {
    global cfg_artifact_dir

    open_run impl_1
    report_timing_summary -file [file join $cfg_artifact_dir timing_summary.rpt]
    report_utilization -file [file join $cfg_artifact_dir utilization.rpt]
    report_methodology -file [file join $cfg_artifact_dir methodology.rpt]
    report_drc -file [file join $cfg_artifact_dir drc.rpt]
}
proc copy_run_bitstream {} {
    global cfg_project_name cfg_artifact_dir

    set bit_file [file join $cfg_artifact_dir "${cfg_project_name}.bit"]
    set run_bit_files [run_bit_files]
    if {[llength $run_bit_files] == 0} {
        set impl_run_dir [get_property DIRECTORY [get_runs impl_1]]
        fail "implementation run completed without a .bit file in $impl_run_dir"
    }
    if {[llength $run_bit_files] > 1} {
        fail "implementation run produced multiple .bit files: $run_bit_files"
    }
    file copy -force [lindex $run_bit_files 0] $bit_file
    return $bit_file
}
proc export_debug_probes {} {
    global cfg_project_name cfg_artifact_dir

    set ltx_file [file join $cfg_artifact_dir "${cfg_project_name}.ltx"]
    if {[llength [get_debug_cores -quiet]] == 0} {
        if {[file exists $ltx_file]} {
            puts "INFO: removing stale debug probes artifact: $ltx_file"
            file delete -force $ltx_file
        }
        return ""
    }

    write_debug_probes -force $ltx_file
    return $ltx_file
}
proc export_xsa {} {
    global cfg_project_name cfg_artifact_dir

    set xsa_file [file join $cfg_artifact_dir "${cfg_project_name}.xsa"]
    write_hw_platform -fixed -include_bit -force -file $xsa_file
    return $xsa_file
}

if {$argc != 2} { fail "expected <manifest.tcl> <stage>" }
set manifest [file normalize [lindex $argv 0]]
set stage [lindex $argv 1]
set stage_rank [rank $stage]
if {![file exists $manifest]} { fail "missing manifest: $manifest" }
source $manifest
if {![info exists cfg_verilog_defines]} {
    set cfg_verilog_defines [list]
}

file mkdir $cfg_build_dir
file mkdir $cfg_artifact_dir
open_or_create_project

if {$stage_rank == 0} {
    puts "INFO: project generated; synthesis not requested"
    exit 0
}

if {$stage eq "export"} {
    if {[llength [run_bit_files]] != 1} {
        puts "INFO: no run-owned bitstream found; running impl_1 to write_bitstream"
        run_implementation_if_needed 1 0
    }
    open_run impl_1
    set bit_file [copy_run_bitstream]
    set ltx_file [export_debug_probes]
    set xsa_file [export_xsa]
    puts "INFO: export complete: $bit_file $xsa_file $ltx_file"
    exit 0
}

set synth_ran [run_synthesis_if_needed]
if {$stage_rank == 1} { exit 0 }

set need_bitstream [expr {$stage_rank >= 3}]
run_implementation_if_needed $need_bitstream $synth_ran
open_impl_and_write_reports
if {$stage_rank == 2} { exit 0 }

set bit_file [copy_run_bitstream]
set ltx_file [export_debug_probes]
if {$stage_rank == 3} { exit 0 }

set xsa_file [export_xsa]
puts "INFO: build complete: $bit_file $xsa_file $ltx_file"
