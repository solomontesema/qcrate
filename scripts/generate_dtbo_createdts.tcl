if {$argc < 4 || $argc > 5} {
    puts stderr "usage: xsct -nodisp generate_dtbo_createdts.tcl <xsa> <outdir> <platform-name> <git-branch> ?<local-dtg-repo>?"
    exit 2
}

set xsa [file normalize [lindex $argv 0]]
set outdir [file normalize [lindex $argv 1]]
set platform_name [lindex $argv 2]
set git_branch [lindex $argv 3]
set local_dtg_repo ""
if {$argc == 5} {
    set local_dtg_repo [file normalize [lindex $argv 4]]
}

if {![file exists $xsa]} {
    puts stderr "ERROR: missing XSA: $xsa"
    exit 2
}
if {$local_dtg_repo ne "" && ![file isdirectory $local_dtg_repo]} {
    puts stderr "ERROR: missing local DTG repo: $local_dtg_repo"
    exit 2
}

file delete -force $outdir
file mkdir $outdir

set cmd [list createdts -hw $xsa -zocl -platform-name $platform_name -overlay -compile -out $outdir]
if {$local_dtg_repo ne ""} {
    lappend cmd -local-repo $local_dtg_repo
} else {
    lappend cmd -git-branch $git_branch
}

puts "INFO: running: $cmd"
eval $cmd
