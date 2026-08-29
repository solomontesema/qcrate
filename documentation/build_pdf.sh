#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${script_dir}/output"
pdf_file="${output_dir}/Q-Crate_Design_Guide_v0.1.pdf"
build_dir="$(mktemp -d)"
trap 'rm -rf -- "${build_dir}"' EXIT

mkdir -p "${output_dir}"

# Keep SVG in the Markdown for Git hosting, but convert it for LaTeX/PDF.
mkdir -p "${build_dir}/images"
inkscape "${script_dir}/images/qcrate-architecture.svg" \
  --export-type=pdf \
  --export-filename="${build_dir}/images/qcrate-architecture.pdf"
sed 's#images/qcrate-architecture\.svg#images/qcrate-architecture.pdf#' \
  "${script_dir}/Q-CRATE_DESIGN_GUIDE.md" > "${build_dir}/Q-CRATE_DESIGN_GUIDE.md"

pandoc "${build_dir}/Q-CRATE_DESIGN_GUIDE.md" \
  --from markdown+raw_tex \
  --pdf-engine=xelatex \
  --resource-path="${build_dir}:${script_dir}" \
  --highlight-style=tango \
  --metadata mainfont="DejaVu Sans" \
  --metadata monofont="DejaVu Sans Mono" \
  --output "${pdf_file}"

printf '%s\n' "Built ${pdf_file}"
