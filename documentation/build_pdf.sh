#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${script_dir}/output"
pdf_file="${output_dir}/Q-Crate_Design_Guide_v0.1.pdf"
build_dir="$(mktemp -d)"
trap 'rm -rf -- "${build_dir}"' EXIT

mkdir -p "${output_dir}"

pandoc "${script_dir}/Q-CRATE_DESIGN_GUIDE.md" \
  --from markdown+raw_tex \
  --pdf-engine=xelatex \
  --resource-path="${script_dir}" \
  --highlight-style=tango \
  --metadata mainfont="DejaVu Sans" \
  --metadata monofont="DejaVu Sans Mono" \
  --output "${pdf_file}"

printf '%s\n' "Built ${pdf_file}"
