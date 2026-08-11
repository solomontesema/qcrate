#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/../../../.." && pwd)
project_dir="${repo_root}/kv260/linux/petalinux/qcrate-kv260"
images_dir="${project_dir}/images/linux"
sdt_psu_init="${repo_root}/build/petalinux/sdt/qcrate-kv260/psu_init.c"
output="${images_dir}/qcrate-kv260-bootfw.bin"
checksum="${output}.sha256"
bif="${images_dir}/bootgen.bif"

command -v petalinux-package >/dev/null || {
    echo "error: source PetaLinux 2024.2 settings before running this script" >&2
    exit 2
}

for input in \
    "${images_dir}/zynqmp_fsbl.elf" \
    "${images_dir}/pmufw.elf" \
    "${images_dir}/bl31.elf" \
    "${images_dir}/system.dtb" \
    "${images_dir}/u-boot.elf" \
    "${sdt_psu_init}"; do
    test -s "${input}" || {
        echo "error: missing input: ${input}" >&2
        exit 2
    }
done

grep -Fq '0x013F3F07U, 0x01010203U' "${sdt_psu_init}" || {
    echo "error: SDT does not configure PL0 as DPLL / 2 / 1" >&2
    exit 2
}
grep -Fq '0x013F3F07U, 0x01010403U' "${sdt_psu_init}" || {
    echo "error: SDT does not configure PL1 as DPLL / 4 / 1" >&2
    exit 2
}

rm -f "${output}" "${checksum}" "${bif}"
(
    cd "${project_dir}"
    petalinux-package boot --u-boot --output "${output}"
)

for partition in zynqmp_fsbl.elf pmufw.elf bl31.elf system.dtb u-boot.elf; do
    grep -Fq "${partition}" "${bif}" || {
        echo "error: BOOT.BIN is missing ${partition}" >&2
        exit 2
    }
done

if grep -Eq '\.(bit|bit\.bin)([[:space:]]|$)' "${bif}"; then
    echo "error: fixed boot firmware must not contain the PL bitstream" >&2
    exit 2
fi

test -s "${output}"
(
    cd "${images_dir}"
    sha256sum "$(basename -- "${output}")" > "$(basename -- "${checksum}")"
)
cat "${checksum}"
echo "INFO: generated checked Kria A/B boot firmware: ${output}"
