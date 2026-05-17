"""Build chapter-05 Reveal.js slide deck."""

from __future__ import annotations

from pathlib import Path

from _shared.slide_template import build_deck, slide


SECTIONS = [
    slide(
        title="5.1 Single — Baseline",
        bullets=[
            "1 model × 1 call.",
            "측정: 정확도 · 환각률 · 토큰비용 · latency.",
            "이후 전략들의 기준점.",
        ],
    ),
    slide(
        title="5.2 Self-Reflect — 2-Pass",
        code=(
            "text",
            "draft → critic (\"NO_ISSUES?\") → revise(if needed)",
        ),
        callout="Critic이 \"NO_ISSUES\"면 Pass 2 생략 — 비용 절감.",
        warn="Critic이 잘못된 비판을 하면 revise 답변이 더 나빠진다 → Pass 1과 비교 후 더 잘 인용된 쪽 채택.",
    ),
    slide(
        title="5.3 Multi-LLM Debate — 4 Provider",
        bullets=[
            "Round 0: 각 participant 독립 draft.",
            "Round 1: 다른 participant draft에 cross-critique.",
            "Moderator: 합성 + citation 통합 + uncertainty 명시.",
        ],
        notes="participants = Kimi, DeepSeek, OpenAI, Grok 4개 동시. Moderator는 OpenAI (가장 안정적인 합성).",
    ),
    slide(
        title="5.4 Hybrid — Self-Reflect + Debate",
        body="""<pre><code class="language-text">[Model A] draft → A's critic → A's revised
[Model B] draft → B's critic → B's revised
                                  ↓
                          Debate Round 1
                                  ↓
                       Moderator final answer</code></pre>""",
        callout="이미 self-reflect 한 draft 끼리 debate → 발산 ↓, 수렴 속도 ↑.",
    ),
    slide(
        title="5.5 비용·정확도 트레이드오프 (기대값)",
        body="""<table>
<thead><tr><th>Strategy</th><th>정확도</th><th>환각률</th><th>토큰(상대)</th><th>Latency(상대)</th></tr></thead>
<tbody>
<tr><td>Single</td><td>baseline</td><td>baseline</td><td>1.0×</td><td>1.0×</td></tr>
<tr><td>Self-Reflect</td><td>↑</td><td>↓↓</td><td>2.0×</td><td>2.0×</td></tr>
<tr><td>Debate (4 prov)</td><td>↑↑</td><td>↓</td><td>3~5×</td><td>3×</td></tr>
<tr><td>Hybrid</td><td>↑↑↑</td><td>↓↓</td><td>4~6×</td><td>3~4×</td></tr>
</tbody>
</table>""",
    ),
    slide(
        title="Opik에서 본다",
        bullets=[
            "reasoning step 수 vs 정확도 산점도.",
            "모델별 token 비용 분포.",
            "debate round별 panel answer 유사도 — 수렴 곡선.",
        ],
        callout="자기 챕터-05 프로젝트(teaching-ch05-{본인})에서 비교 가능.",
    ),
    slide(
        title="시리즈 마무리",
        bullets=[
            "Ch 1 indexing → Ch 2 quality → Ch 3 text2cypher → Ch 4 routing → Ch 5 debate.",
            "동일한 4-provider 인터페이스, Opik per-user 프로젝트.",
            "FinDER 8 카테고리를 임의의 샘플링 패턴으로 활용.",
        ],
        warn="후속: BenchmarkRunner 통합, 카테고리별 win-rate, 비용 dashboard.",
    ),
]


def main() -> None:
    html = build_deck(
        title="Chapter 5 — Debate Pool",
        subtitle="Single · Self-Reflect · Multi-LLM Debate · Hybrid",
        author="seocho · ontology lab · 2026-S03",
        sections=SECTIONS,
        page_title="Ch 5 · Debate Pool",
    )
    out = Path(__file__).resolve().parent.parent / "chapter-05-debate-pool-slides.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html)} bytes, {len(SECTIONS)+1} slides)")


if __name__ == "__main__":
    main()
