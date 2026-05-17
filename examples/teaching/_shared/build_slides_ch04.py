"""Build chapter-04 Reveal.js slide deck."""

from __future__ import annotations

from pathlib import Path

from _shared.slide_template import build_deck, slide


SECTIONS = [
    slide(
        title="4.1 4-axis Question Augmentation",
        body="""<table>
<thead><tr><th>Axis</th><th>출력</th><th>후속 결정</th></tr></thead>
<tbody>
<tr><td>Intent</td><td>lookup / aggregation / comparison / explanation</td><td>backend 가중치</td></tr>
<tr><td>Entity</td><td>ontology class로 lift</td><td>Cypher 패턴</td></tr>
<tr><td>Topic</td><td>community_id 후보</td><td>검색 범위</td></tr>
<tr><td>Rewrite</td><td>sub-question 분해</td><td>multi-hop 처리</td></tr>
</tbody>
</table>""",
        callout="4축을 별 호출로 분리 → Opik에서 라우팅 근거를 따로 추적 가능.",
    ),
    slide(
        title="4.2 Routing Table",
        body="""<table>
<thead><tr><th>Intent</th><th>Vector</th><th>Fulltext</th><th>Cypher</th></tr></thead>
<tbody>
<tr><td>lookup (entity 식별)</td><td>low</td><td>low</td><td><b>high</b></td></tr>
<tr><td>lookup (모호)</td><td>mid</td><td><b>high</b></td><td>mid</td></tr>
<tr><td>aggregation</td><td>low</td><td>low</td><td><b>high</b></td></tr>
<tr><td>comparison</td><td><b>high</b></td><td><b>high</b></td><td><b>high</b></td></tr>
<tr><td>explanation</td><td><b>high</b></td><td>mid</td><td>low</td></tr>
</tbody>
</table>""",
    ),
    slide(
        title="4.3 RRF — score scale 무시, rank만",
        code=(
            "python",
            "def reciprocal_rank_fusion(per_backend, k=60, top_n=10):\n"
            "    scores = defaultdict(float)\n"
            "    for backend, ranked in per_backend.items():\n"
            "        for rank, item in enumerate(ranked, start=1):\n"
            "            scores[item['doc_id']] += 1.0 / (k + rank)\n"
            "    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_n]",
        ),
        callout="vector cosine 과 BM25 raw score 합산은 위험. RRF는 rank만 보므로 robust.",
    ),
    slide(
        title="4.4 Citation Envelope + Refusal",
        code=(
            "text",
            "<answer>본문 with [src:chunk] inline 인용</answer>\n"
            "<citations>\n"
            "  - src:finder-A, chunk:1 — \"...verbatim span...\"\n"
            "</citations>\n"
            "<confidence>high | medium | low</confidence>",
        ),
        warn="컨텍스트에 근거 없으면 fabrication 금지 — 표준 거절 응답 사용.",
    ),
    slide(
        title="인용 검증 — invalid count = fabrication 신호",
        code=(
            "python",
            "CITE_RE = re.compile(r'\\[src:([^,\\]]+),\\s*chunk:(\\d+)\\]')\n\n"
            "def validate_citations(answer, context):\n"
            "    found = set(CITE_RE.findall(answer))\n"
            "    valid = set(CITE_RE.findall(context))\n"
            "    return {'invalid': sorted(found - valid),\n"
            "            'all_valid': found.issubset(valid)}",
        ),
        notes="invalid 카운트가 0이 아니면 — Ch 5의 self-reflect 또는 debate가 필요한 시그널.",
    ),
    slide(
        title="다음 챕터 (Ch 5 — Debate Pool)",
        bullets=[
            "Ch 4 답변 = 단일 모델 + 인용 강제.",
            "Ch 5: 4 provider 동시 debate, self-reflect + debate hybrid.",
            "수렴 곡선 (Opik) 으로 reasoning depth vs 정확도 산점도.",
        ],
    ),
]


def main() -> None:
    html = build_deck(
        title="Chapter 4 — Routing Agent",
        subtitle="4-Axis Augmentation · Parallel Search · RRF · Citation Envelope",
        author="seocho · ontology lab · 2026-S03",
        sections=SECTIONS,
        page_title="Ch 4 · Routing Agent",
    )
    out = Path(__file__).resolve().parent.parent / "chapter-04-routing-agent-slides.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html)} bytes, {len(SECTIONS)+1} slides)")


if __name__ == "__main__":
    main()
