#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${script_dir}/output"
pdf_file="${output_dir}/Q-Crate_Design_Guide.pdf"
manifest="${script_dir}/chapters.txt"

for tool in pandoc xelatex; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    printf 'Missing required tool: %s. See documentation/README.md.\n' "${tool}" >&2
    exit 1
  fi
done

[[ -s "${manifest}" && -s "${script_dir}/metadata.yaml" ]] || {
  printf 'Missing or empty chapter manifest or metadata.yaml\n' >&2
  exit 1
}

chapters=()
while IFS= read -r chapter || [[ -n "${chapter}" ]]; do
  chapter="${chapter%$'\r'}"
  [[ -z "${chapter}" || "${chapter}" == \#* ]] && continue
  [[ "${chapter}" == chapters/*.md && "${chapter}" != *..* ]] || {
    printf 'Invalid chapter path: %s\n' "${chapter}" >&2
    exit 1
  }
  [[ -s "${script_dir}/${chapter}" ]] || {
    printf 'Missing or empty chapter: %s\n' "${chapter}" >&2
    exit 1
  }
  for existing in "${chapters[@]}"; do
    [[ "${existing}" != "${script_dir}/${chapter}" ]] || {
      printf 'Duplicate chapter: %s\n' "${chapter}" >&2
      exit 1
    }
  done
  chapters+=("${script_dir}/${chapter}")
done < "${manifest}"
(( ${#chapters[@]} > 0 )) || {
  printf 'No chapters listed in %s\n' "${manifest}" >&2
  exit 1
}

mkdir -p "${output_dir}"
# Build beside the destination so publication is one rename on success.
build_dir="$(mktemp -d "${output_dir}/.pdf-build.XXXXXX")"
trap 'rm -rf -- "${build_dir}"' EXIT

pandoc "${chapters[@]}" \
  --metadata-file="${script_dir}/metadata.yaml" \
  --from markdown+raw_tex \
  --top-level-division=chapter \
  --toc --number-sections \
  --pdf-engine=xelatex \
  --resource-path="${script_dir}/chapters:${script_dir}" \
  --highlight-style=tango \
  --metadata mainfont="DejaVu Sans" \
  --metadata monofont="DejaVu Sans Mono" \
  --output "${build_dir}/guide.pdf"

mv -- "${build_dir}/guide.pdf" "${pdf_file}"
printf 'Built %s (%s chapters)\n' "${pdf_file}" "${#chapters[@]}"
