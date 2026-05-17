"""Reveal.js deck builder for the teaching curriculum.

Matches the existing repo style (Nord palette, monospaced code blocks,
callout/warn boxes) so chapter slides feel consistent with
``examples/tutorial_slides.html`` and
``examples/teaching/seocho_finder_fibo_teaching.html``.

Quick start
-----------
    from _shared.slide_template import build_deck, slide

    html = build_deck(
        title="Chapter 1 — Knowledge Graph Indexing",
        subtitle="Source / Chunk / Entity 3-layer LPG",
        author="seocho · ontology lab",
        sections=[
            slide(title="Why 3-layer?", bullets=["출처 추적", "재계산", "다대다"]),
            slide(title="Code", code=("python", "from seocho import ...")),
        ],
    )
    Path("chapter-01-slides.html").write_text(html)
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Slide model
# ---------------------------------------------------------------------------


@dataclass
class Slide:
    title: Optional[str] = None
    subtitle: Optional[str] = None
    bullets: List[str] = field(default_factory=list)
    body_html: Optional[str] = None
    code: Optional[Tuple[str, str]] = None  # (language, source)
    code_caption: Optional[str] = None
    callout: Optional[str] = None  # success-style highlight
    warn: Optional[str] = None  # warning-style highlight
    notes: Optional[str] = None  # speaker notes (Reveal.js "notes" plugin)


def slide(
    *,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    bullets: Optional[Sequence[str]] = None,
    body: Optional[str] = None,
    code: Optional[Tuple[str, str]] = None,
    code_caption: Optional[str] = None,
    callout: Optional[str] = None,
    warn: Optional[str] = None,
    notes: Optional[str] = None,
) -> Slide:
    """Compact factory — keep notebook authoring concise."""
    return Slide(
        title=title,
        subtitle=subtitle,
        bullets=list(bullets) if bullets else [],
        body_html=body,
        code=code,
        code_caption=code_caption,
        callout=callout,
        warn=warn,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


_REVEAL_CDN = "https://cdn.jsdelivr.net/npm/reveal.js@5.1.0"

# Nord-leaning palette aligned with the repo's existing decks.
_THEME_CSS = """
:root {
  --r-background-color: #1d2329;
  --r-main-color: #d8dee9;
  --r-heading-color: #88c0d0;
  --r-link-color: #88c0d0;
  --r-selection-background-color: #88c0d0;
  --r-main-font: 'Inter', 'Pretendard', system-ui, sans-serif;
  --r-heading-font: 'Inter', 'Pretendard', system-ui, sans-serif;
  --r-code-font: 'JetBrains Mono', Menlo, monospace;
}
.reveal h1 { font-size: 2.2em; color: #88c0d0; }
.reveal h2 { font-size: 1.5em; color: #88c0d0; margin-bottom: 0.4em; }
.reveal h3 { font-size: 1.15em; color: #a3be8c; }
.reveal .subtitle { color: #d8dee9; opacity: 0.75; font-size: 0.9em; }
.reveal .meta { color: #d8dee9; opacity: 0.55; font-size: 0.7em; margin-top: 1.5em; }
.reveal pre { box-shadow: none; width: 100%; }
.reveal pre code {
  max-height: 460px;
  padding: 18px 22px;
  background: #2e3440;
  border-radius: 6px;
  font-size: 0.72em;
  line-height: 1.45;
}
.reveal .code-caption {
  color: #d8dee9; opacity: 0.6; font-size: 0.7em; margin-top: 6px;
}
.reveal blockquote {
  border-left: 3px solid #5e81ac;
  background: rgba(94, 129, 172, 0.12);
  padding: 12px 18px; font-style: normal;
}
.reveal .callout {
  background: rgba(163, 190, 140, 0.15);
  border-left: 3px solid #a3be8c;
  padding: 14px 20px; border-radius: 4px; margin: 18px 0;
  text-align: left; font-size: 0.85em;
}
.reveal .warn {
  background: rgba(208, 135, 112, 0.15);
  border-left: 3px solid #d08770;
  padding: 14px 20px; border-radius: 4px; margin: 18px 0;
  text-align: left; font-size: 0.85em;
}
.reveal ul { display: inline-block; text-align: left; }
.reveal li { margin-bottom: 0.35em; }
.reveal table { font-size: 0.7em; }
.reveal table th { color: #88c0d0; }
.reveal .twocol {
  display: grid; grid-template-columns: 1fr 1fr; gap: 24px; text-align: left;
}
.reveal .tiny { font-size: 0.6em; opacity: 0.7; }
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _render_bullets(items: Sequence[str]) -> str:
    if not items:
        return ""
    rows = "\n".join(f"  <li>{_esc(it)}</li>" for it in items)
    return f"<ul>\n{rows}\n</ul>"


def _render_code(language: str, source: str) -> str:
    lang = (language or "").strip() or "text"
    return (
        f'<pre><code class="language-{_esc(lang)}" data-trim data-noescape>'
        f"{_esc(source)}"
        "</code></pre>"
    )


def _render_slide(s: Slide) -> str:
    parts: List[str] = []
    if s.title:
        parts.append(f"<h2>{_esc(s.title)}</h2>")
    if s.subtitle:
        parts.append(f'<div class="subtitle">{_esc(s.subtitle)}</div>')
    if s.bullets:
        parts.append(_render_bullets(s.bullets))
    if s.body_html:
        parts.append(s.body_html)
    if s.code:
        lang, src = s.code
        parts.append(_render_code(lang, src))
        if s.code_caption:
            parts.append(f'<div class="code-caption">{_esc(s.code_caption)}</div>')
    if s.callout:
        parts.append(f'<div class="callout">{s.callout}</div>')
    if s.warn:
        parts.append(f'<div class="warn">{s.warn}</div>')
    inner = "\n".join(parts) if parts else "&nbsp;"
    notes_block = (
        f'\n<aside class="notes">{_esc(s.notes)}</aside>' if s.notes else ""
    )
    return f"<section>\n{inner}{notes_block}\n</section>"


def _render_cover(title: str, subtitle: Optional[str], author: Optional[str]) -> str:
    parts = [f"<h1>{_esc(title)}</h1>"]
    if subtitle:
        parts.append(f'<div class="subtitle">{_esc(subtitle)}</div>')
    if author:
        parts.append(f'<div class="meta">{_esc(author)}</div>')
    return "<section>\n" + "\n".join(parts) + "\n</section>"


def build_deck(
    *,
    title: str,
    subtitle: Optional[str] = None,
    author: Optional[str] = "seocho · ontology lab",
    sections: Iterable[Slide],
    include_cover: bool = True,
    page_title: Optional[str] = None,
) -> str:
    """Render a complete Reveal.js deck as a single HTML string.

    The output is self-contained except for CDN references to Reveal.js.
    """
    rendered_sections: List[str] = []
    if include_cover:
        rendered_sections.append(_render_cover(title, subtitle, author))
    rendered_sections.extend(_render_slide(s) for s in sections)
    body = "\n".join(rendered_sections)

    head_title = _esc(page_title or title)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{head_title}</title>
<link rel="stylesheet" href="{_REVEAL_CDN}/dist/reset.css">
<link rel="stylesheet" href="{_REVEAL_CDN}/dist/reveal.css">
<link rel="stylesheet" href="{_REVEAL_CDN}/dist/theme/black.css">
<link rel="stylesheet" href="{_REVEAL_CDN}/plugin/highlight/monokai.css">
<style>
{_THEME_CSS}
</style>
</head>
<body>
<div class="reveal">
  <div class="slides">
{body}
  </div>
</div>
<script src="{_REVEAL_CDN}/dist/reveal.js"></script>
<script src="{_REVEAL_CDN}/plugin/highlight/highlight.js"></script>
<script src="{_REVEAL_CDN}/plugin/notes/notes.js"></script>
<script src="{_REVEAL_CDN}/plugin/markdown/markdown.js"></script>
<script>
  Reveal.initialize({{
    hash: true,
    slideNumber: 'c/t',
    transition: 'slide',
    plugins: [ RevealHighlight, RevealNotes, RevealMarkdown ]
  }});
</script>
</body>
</html>
"""


__all__ = ["Slide", "slide", "build_deck"]
