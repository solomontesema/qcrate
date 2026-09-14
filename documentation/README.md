# Q-Crate documentation

[Read the Design Guide](Q-CRATE_DESIGN_GUIDE.md).

The canonical book sources are the individual files in `chapters/`.
`chapters.txt` explicitly defines their reading order; `metadata.yaml` contains
the title, document revision and PDF layout. The former monolithic guide is now
an index, so existing links to its filename remain useful.

## Build the PDF

On Ubuntu, the build requires Pandoc, XeLaTeX, the LaTeX packages used by the
metadata, and DejaVu fonts:

```bash
sudo apt install pandoc texlive-xetex texlive-latex-extra fonts-dejavu
```

From the repository root:

```bash
bash documentation/build_pdf.sh
```

Or, from this directory:

```bash
./build_pdf.sh
```

The script resolves inputs relative to itself, so it also works when invoked
by absolute path from another working directory, including paths with spaces.
It creates one PDF, with chapter numbering and a table of contents, at
`documentation/output/Q-Crate_Design_Guide.pdf`. The edition is recorded inside
the document rather than duplicated in the output filename.

A failed build leaves the previous successful PDF intact. Missing tools,
missing chapters, an empty manifest, and duplicate chapter entries fail early.
Images are resolved relative to `chapters/`, matching their GitHub Markdown
paths. The tracked KV260 block-design image is reused without copying it.

`output/Q-Crate_Design_Guide_v0.1.pdf` is the historical edition retained from
before the split. It does not reflect the working draft. Build the current
sources to obtain the current PDF; review the rendered output before sharing.

## Editing and scope

Edit one chapter at a time. Add or reorder chapters in `chapters.txt`; an
unlisted Markdown file is not silently included in the book. Use one level-one
heading per chapter and let Pandoc number headings. Relative image and source
links in a chapter start from its directory (`../../` reaches the repository
root). Update this index and the guide entry page when changing the contents.

This first restructuring commit is **0.2-draft**, not a claim that the whole
book matches current implementation. Chapter 6 now describes the implemented
software stack; the inherited FPGA proposal is explicitly marked for rewrite.
Architecture and DSP detail still need source-by-source review.

| Former chapter | Treatment |
|---|---|
| 1-4 | Separate files; preface updated, architecture and DSP largely retained |
| 5: FPGA Implementation Plan | Separate implementation chapter; proposal status explicit; general verification material incorporated |
| 6: Firmware and Software Architecture | Reworked around implemented ownership, OpenAMP, configuration, DMA, UDP, recording, analysis and sweeps |
| 7: Verification Strategy | NCO checks moved into DSP; other material moved into FPGA; software validation discussed in chapter 6 |
| 8: Development Roadmap | Removed from the book; historical text remains in Git |
| 9: Glossary | Now chapter 7 |
| 10: Open Questions | Removed from the book; historical text remains in Git |
| 11: References | Now chapter 8 |

Keep descriptions of implemented behaviour separate from proposals. Detailed
register maps, fixed-point arithmetic and firmware mechanisms will be expanded
in their owning chapters, with evidence from the source. No physical converter
or analogue performance is implied by the digital-system acceptance record.
