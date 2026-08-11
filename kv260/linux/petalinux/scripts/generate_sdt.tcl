# Generate the KV260-aware System Device Tree consumed by PetaLinux 2024.2.

proc fail {message} {
    puts stderr "ERROR: $message"
    exit 2
}

if {[llength $argv] < 2 || [llength $argv] > 3} {
    fail "usage: xsct generate_sdt.tcl <input.xsa> <output-dir> ?board-dts?"
}

set xsa_file [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set board_dts "zynqmp-smk-k26-reva"

if {[llength $argv] == 3} {
    set board_dts [lindex $argv 2]
}

if {![file isfile $xsa_file]} {
    fail "XSA does not exist: $xsa_file"
}

if {[file exists $output_dir]} {
    fail "output directory already exists: $output_dir"
}

package require sdtgen
sdtgen set_dt_param \
    -xsa $xsa_file \
    -dir $output_dir \
    -board_dts $board_dts
sdtgen generate_sdt

if {![file isfile [file join $output_dir system-top.dts]]} {
    fail "SDT generation did not create system-top.dts"
}

puts "INFO: generated KV260 SDT in $output_dir"
exit 0
