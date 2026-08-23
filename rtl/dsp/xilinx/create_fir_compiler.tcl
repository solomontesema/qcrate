# Recreate the Q-Crate FIR Compiler IP from tracked numerical artifacts.

proc qcrate_read_signed_hex {path width} {
    set stream [open $path r]
    set values [list]
    set modulus [expr {1 << $width}]
    set sign_bit [expr {1 << ($width - 1)}]
    foreach line [split [read $stream] "\n"] {
        set token [string trim $line]
        if {$token eq ""} { continue }
        if {[scan $token %x value] != 1} {
            close $stream
            error "invalid hexadecimal coefficient '$token' in $path"
        }
        if {$value & $sign_bit} { set value [expr {$value - $modulus}] }
        lappend values $value
    }
    close $stream
    return $values
}

proc qcrate_validate_coefficients {values} {
    if {[llength $values] != 217} {
        error "Q-Crate FIR requires 217 coefficients, got [llength $values]"
    }
    set sum 0
    for {set index 0} {$index < 217} {incr index} {
        set value [lindex $values $index]
        incr sum $value
        if {$value != [lindex $values [expr {216 - $index}]]} {
            error "Q-Crate FIR coefficients are not symmetric at index $index"
        }
    }
    if {$sum != 131072} {
        error "Q-Crate FIR coefficient sum is $sum, expected 131072"
    }
}

if {[llength [get_projects -quiet]] == 0} {
    error "create_fir_compiler.tcl requires an open Vivado project"
}

set qcrate_fir_module qcrate_fir_decim16_ip
set qcrate_script_dir [file dirname [file normalize [info script]]]
set qcrate_repo_root [file normalize [file join $qcrate_script_dir ../../..]]
set qcrate_table [file join $qcrate_repo_root \
    rtl dsp tables fir_decim16_q1_17.hex]
set qcrate_coefficients [qcrate_read_signed_hex $qcrate_table 18]
qcrate_validate_coefficients $qcrate_coefficients

# FIR Compiler pads this 217-tap symmetric filter to its four-coefficient
# implementation boundary. Make that padding explicit at the tail so it cannot
# shift the decimator's first output from input index 15 to input index 12.
set qcrate_ip_coefficients [concat $qcrate_coefficients {0 0 0}]

if {[llength [get_ips -quiet $qcrate_fir_module]] == 0} {
    if {[info exists qcrate_ip_output_dir]} {
        set qcrate_ip_dir [file normalize $qcrate_ip_output_dir]
    } else {
        set qcrate_project_dir [get_property DIRECTORY [current_project]]
        set qcrate_ip_dir [file join $qcrate_project_dir generated_ip]
    }
    set qcrate_xci [file join $qcrate_ip_dir $qcrate_fir_module \
        "${qcrate_fir_module}.xci"]
    if {[file exists $qcrate_xci]} {
        read_ip $qcrate_xci
    } else {
        file mkdir $qcrate_ip_dir
        create_ip -name fir_compiler -vendor xilinx.com -library ip \
            -version 7.2 -module_name $qcrate_fir_module -dir $qcrate_ip_dir
    }
}

set qcrate_fir_ip [get_ips $qcrate_fir_module]
set_property -dict [list \
    CONFIG.Filter_Type {Decimation} \
    CONFIG.Decimation_Rate {16} \
    CONFIG.Rate_Change_Type {Integer} \
    CONFIG.CoefficientSource {Vector} \
    CONFIG.CoefficientVector [join $qcrate_ip_coefficients ,] \
    CONFIG.Coefficient_Sign {Signed} \
    CONFIG.Coefficient_Width {18} \
    CONFIG.Coefficient_Structure {Inferred} \
    CONFIG.Data_Sign {Signed} \
    CONFIG.Data_Width {18} \
    CONFIG.Data_Fractional_Bits {17} \
    CONFIG.Number_Paths {2} \
    CONFIG.Clock_Frequency {200} \
    CONFIG.Sample_Frequency {200} \
    CONFIG.Optimization_Goal {Area} \
    CONFIG.Output_Rounding_Mode {Full_Precision} \
    CONFIG.M_DATA_Has_TREADY {true} \
    CONFIG.S_DATA_Has_FIFO {true} \
    CONFIG.Has_ARESETn {true} \
    CONFIG.Reset_Data_Vector {true}] $qcrate_fir_ip

if {[get_property CONFIG.Output_Width $qcrate_fir_ip] != 36} {
    error "Q-Crate FIR expected a 36-bit full-precision output"
}
puts "INFO: configured $qcrate_fir_module from 217 tracked Q1.17 coefficients plus 3 trailing phase-alignment zeros"
