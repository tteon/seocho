"""Build chapter-03 Reveal.js slide deck."""

from __future__ import annotations

from pathlib import Path

from _shared.slide_template import build_deck, slide


SECTIONS = [
    slide(
        title="3.1 3-블록 Prompt",
        bullets=[
            "Block 1: ontology slice (관련 class/property만)",
            "Block 2: few-shot 예제 3개 (easy / medium / hard)",
            "Block 3: 출력 제약 — read-only, elementId, LIMIT 강제",
        ],
        callout="3-블록 구조는 token 효율과 정확도를 동시에 끌어올린다.",
    ),
    slide(
        title="Block 3 — Output Constraints",
        code=(
            "text",
            "- Read-only: NO CREATE/MERGE/DELETE/SET\n"
            "- Use elementId() instead of deprecated id()\n"
            "- MANDATORY: every query must end with LIMIT (default 25)\n"
            "- Output a single fenced cypher block, nothing else",
        ),
        warn="LLM이 unbounded path (`*..`)를 생성하면 graph OOM. LIMIT 강제는 비용 안전장치.",
    ),
    slide(
        title="3.2 4-provider Cypher 생성 — 5종 의도",
        body="""<table>
<thead><tr><th>Intent</th><th>예시</th></tr></thead>
<tbody>
<tr><td>lookup</td><td>위험 요인 수가 가장 많은 5개 회사</td></tr>
<tr><td>aggregation</td><td>community별 entity 수 평균/최대</td></tr>
<tr><td>comparison</td><td>두 community 공통 entity</td></tr>
<tr><td>explanation</td><td>metadata category=Risk 인 Source 개수</td></tr>
<tr><td>hard</td><td>최근 추출 entity 와 출처 Source 조인</td></tr>
</tbody>
</table>""",
        notes="각 provider 의 executable rate(실행 가능률)와 의미 정확도를 분리해서 측정.",
    ),
    slide(
        title="3.3 실패 패턴 5종",
        body="""<table>
<thead><tr><th>패턴</th><th>처방</th></tr></thead>
<tbody>
<tr><td>Label hallucination</td><td>Block 1에 허용 라벨 명시 + \"이외 금지\"</td></tr>
<tr><td>Property 오타</td><td>Block 1에 property 이름 verbatim 인용</td></tr>
<tr><td>양방향 관계 누락</td><td>관계 방향성 의미 명시</td></tr>
<tr><td>LIMIT 누락</td><td>Block 3 강제 + post-validation</td></tr>
<tr><td>Cypher injection</td><td>동적 라벨 화이트리스트 검증 (CLAUDE.md §8)</td></tr>
</tbody>
</table>""",
        callout="injection 시도는 prompt에서 1차, 백엔드 write-guard에서 2차로 차단.",
    ),
    slide(
        title="3.4 TTL Korean Labels — 효과",
        code=(
            "turtle",
            "fibo-be:Company\n"
            "    rdfs:label \"Company\"@en ,\n"
            "               \"회사\"@ko ,\n"
            "               \"기업\"@ko ;\n"
            "    skos:altLabel \"Corporation\"@en ,\n"
            "                  \"법인\"@ko ;\n"
            "    rdfs:comment \"법인격을 갖는 사업체...\"@ko .",
        ),
        callout="ontology governance plane에서 label/synonym 풍부도가 한국어 질의 정확도를 결정한다.",
        notes="TTL 수정은 data plane 인덱스에 영향 없음 (governance vs data 분리).",
    ),
    slide(
        title="다음 챕터 (Ch 4 — Routing Agent)",
        bullets=[
            "Ch 3 Cypher 생성기는 단독 도구.",
            "Ch 4: 4-axis augmentation → vector/fulltext/Cypher 병렬 → RRF.",
            "라우팅 결정 자체를 Opik trace로 추적.",
        ],
    ),
]


def main() -> None:
    html = build_deck(
        title="Chapter 3 — Text2Cypher",
        subtitle="3-Block Prompt · Failure Patterns · Korean Labels",
        author="seocho · ontology lab · 2026-S03",
        sections=SECTIONS,
        page_title="Ch 3 · Text2Cypher",
    )
    out = Path(__file__).resolve().parent.parent / "chapter-03-text2cypher-slides.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html)} bytes, {len(SECTIONS)+1} slides)")


if __name__ == "__main__":
    main()
