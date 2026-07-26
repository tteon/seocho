# LoG 2026 Paper Build

The submission source is `paper.tex`. It uses the unmodified official
`log_2026.sty` in anonymous review mode. `PAPER.md` is a readable source draft,
while `MANUSCRIPT.md` retains the longer research record and is not intended for
submission.

Generate the two figures from frozen evaluation artifacts:

    UV_CACHE_DIR=/tmp/uv-cache uv run --with matplotlib \
      python examples/mdm/46_log2026_paper_figures.py

Build from this directory with a TeX distribution:

    latexmk -pdf paper.tex

The current host does not provide `latexmk`, `pdflatex`, or `tectonic`, so PDF
page count and font embedding must be checked in another TeX environment. The
body limit is nine pages; references and appendix do not count toward it.

Before OpenReview submission, follow `SUBMISSION_CHECKLIST.md` and publish code
or data only through an identity-scrubbed Anonymous GitHub repository.
