# OpenReview submission record — LoG 2026

Fill-in values for the submission form. Both tracks use the same form; the track
is chosen in **Submission Type**.

Deadlines (Anywhere On Earth):

| Stage | Deadline | What the form requires |
|---|---|---|
| Abstract submission | **July 29, 2026** | Title, Authors, Keywords, Abstract, Submission Type, License, the two consent checkboxes, Signatures. **PDF is not a required field**, so the abstract can be registered without it. |
| Full paper | **August 1, 2026** | Upload the PDF (appendix merged into the same file) and, optionally, the supplementary zip. |

Registering at the abstract stage is what creates the submission; the PDF is
added or replaced before the paper deadline.

---

## Title*

```
Routing, Not Synthesis, Is the Bottleneck: A Failure Analysis of Coalition Routing over Isolated Financial Graphs
```

## Authors*

`Yitaejeong` — every co-author needs an existing OpenReview profile before
submitting. Real names go in this field; OpenReview hides them from reviewers.
The PDF itself stays anonymous (`Anonymous Authors`), which it already is.

## Keywords*

```
knowledge graphs, graph retrieval-augmented generation, multi-agent systems, query routing, coalition formation, negative results, evaluation methodology, financial question answering
```

## TL;DR

```
On isolated organizational knowledge graphs, finding the right views rather than synthesizing the answer is the bottleneck: a routing miss that serves no evidence costs more than selecting views at random.
```

## Abstract*

Paste from `ABSTRACT_plaintext.txt` (LaTeX artifacts already resolved: `2{,}048`
became `2,048`, `$...$` unwrapped, em dashes normalized). The field supports TeX,
so the plain-text form is safe either way.

## PDF

`paper.pdf` — build with `latexmk -pdf paper.tex`. The appendix is already
`\input` into `paper.tex`, so the build is a single merged file as the form
requires. Body limit is 9 pages; references and appendix are unlimited.

> **Open item:** the last measured build had the body running to 87% of page 10.
> Roughly 0.8 of a page has since been moved into the appendix, but this has not
> been recompiled and verified. Check where References begins before uploading.

## Supplementary Materials

`supplementary.zip` — build with `python3 papers/log2026/build_artifact.py`.
5.4 MiB against the 50 MiB limit, `.zip` as required. Contains the 13 cited
artifact directories, the four analysis entry points, and a claim-to-artifact
map. Anonymized: home paths, usernames, and email addresses are redacted, and
the builder refuses to package if any identity marker survives.

Do not put the appendix here — the form requires it merged into the PDF.

## Submission Type*

**Proceedings** — up to 9 pages, archival, published in PMLR.

The alternative is Extended Abstract: 4 pages, non-archival, and its call
explicitly welcomes "insightful negative results". It would fit this paper's
subject matter, but compressing to 4 pages means dropping most of the eight
ablations, and it earns no archival record. Proceedings was the decision; this is
noted only so the dropdown is not mis-selected.

## Email Sharing*

Check — author emails shared with Program Chairs.

## Data Release*

Check — accepted submissions and author names released publicly after the
conference.

## License*

Select the CC BY 4.0 option unless the venue offers a PMLR-specific default.
PMLR proceedings are published open access.

## Readers* / Signatures*

Leave as prefilled (`LOG 2026 Conference`, author IDs; signature `Yitaejeong`).

---

## Subject areas

From the call, the ones that apply:

- Graphs, Agents and Multi-Agent Systems
- Knowledge Graphs and LLMs
- Graph ML Platforms and Systems

## Anonymity check before upload

- PDF author block reads `Anonymous Authors` — currently true.
- No acknowledgements or funding section naming an institution — currently none.
- Any repository link must go through Anonymous GitHub
  (`anonymous.4open.science`, log in with the real account; it serves an
  anonymized proxy view, so no throwaway account is needed). The paper currently
  cites artifacts by name rather than by URL, so a link is optional if the
  supplementary zip is attached.
- Non-anonymous preprints are explicitly allowed and need not be cited.
