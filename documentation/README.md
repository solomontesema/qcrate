# Q-Crate documentation

This directory contains the Git-tracked source of the Q-Crate Design Guide.

## Build the PDF

On Ubuntu, install Pandoc, XeLaTeX, and Inkscape if they are not already
available:

```bash
sudo apt install pandoc texlive-xetex inkscape
```

Then run:

```bash
./build_pdf.sh
```

The generated file is written to:

```text
output/Q-Crate_Design_Guide_v0.1.pdf
```

The Markdown remains the canonical source. The SVG diagram is used directly by
Git hosting and converted automatically during the PDF build.

## Updating the guide

- Keep implemented and planned features clearly distinguished.
- Update the revision-history table when publishing a meaningful edition.
- Change the version in the Markdown metadata, PDF header, build-script output
  filename, and revision history together.
- Commit source files to Git. Committing generated PDFs is optional and depends
  on the repository release policy.
