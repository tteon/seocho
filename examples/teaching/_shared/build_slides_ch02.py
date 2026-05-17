"""Build chapter-02 Reveal.js slide deck."""

from __future__ import annotations

from pathlib import Path

from _shared.slide_template import build_deck, slide


SECTIONS = [
    slide(
        title="2.1 GDS 4지표 — 진단 신호로 읽기",
        body="""<table>
<thead><tr><th>지표</th><th>본다</th><th>품질 신호</th></tr></thead>
<tbody>
<tr><td>Node Similarity</td><td>이웃 집합 겹침</td><td>중복 후보 → 디듀프 트리거</td></tr>
<tr><td>Degree Centrality</td><td>연결 수</td><td>hub 식별 · 핵심 개체 누락</td></tr>
<tr><td>Clustering Coef.</td><td>삼각형 closure</td><td>도메인 응집성 · 관계 추출 부실</td></tr>
<tr><td>Link Prediction</td><td>누락 가능 엣지</td><td>재추출 큐</td></tr>
</tbody>
</table>""",
        callout="\"수치가 이상하다\" = 인덱싱의 어느 단계가 깨진 것인지 식별하는 진단 신호.",
    ),
    slide(
        title="2.2 GDS Pipeline 한 사이클",
        code=(
            "cypher",
            "CALL gds.graph.project.cypher('ch02-quality',\n"
            "  'MATCH (e) WHERE NOT e:Source AND NOT e:Chunk RETURN id(e) AS id',\n"
            "  'MATCH (e1)<-[:MENTIONS]-(c:Chunk)-[:MENTIONS]->(e2)\n"
            "   WHERE id(e1)<id(e2) RETURN id(e1) AS source, id(e2) AS target')\n\n"
            "CALL gds.nodeSimilarity.stream('ch02-quality')\n"
            "  YIELD node1, node2, similarity WHERE similarity > 0.5\n"
            "  RETURN gds.util.asNode(node1).name AS a,\n"
            "         gds.util.asNode(node2).name AS b, similarity\n"
            "  ORDER BY similarity DESC LIMIT 10\n\n"
            "CALL gds.graph.drop('ch02-quality')",
        ),
        warn="projection은 사용 후 반드시 drop. CLAUDE.md §8에 따라 id() 사용 금지.",
    ),
    slide(
        title="진단 체크리스트",
        bullets=[
            "degree 분포가 power-law를 따르지 않는다 → hub 추출 누락",
            "node similarity > 0.9 다수 → 자동 머지 후보 (휴먼 확인 권장)",
            "mean clustering < 0.1 → 관계 추출 부실",
            "Adamic-Adar 상위 페어가 의미적으로 무관 → false positive",
        ],
    ),
    slide(
        title="2.3 @function_tool 4종",
        code=(
            "python",
            "@function_tool\n"
            "def compute_node_similarity(top_k: int = 10):\n"
            "    \"\"\"USE WHEN: 사용자가 '중복', '머지', '디듀프'를 언급할 때.\"\"\"\n"
            "    ...\n\n"
            "@function_tool\n"
            "def find_hub_entities(top_k: int = 10):\n"
            "    \"\"\"USE WHEN: '핵심 엔터티', '추출 누락' 진단할 때.\"\"\"\n"
            "    ...",
        ),
        callout="docstring 첫 줄에 USE WHEN 시나리오. agent 의 tool 선택 정확도는 여기서 결정.",
    ),
    slide(
        title="2.4 4-provider Agent — 같은 시나리오",
        bullets=[
            "USER: '이 그래프 품질을 4가지 관점에서 평가해줘.'",
            "각 provider 가 4개 도구를 어떤 순서/조합으로 호출하는지 비교.",
            "Opik trace 의 tool_call_item 으로 그대로 캡처.",
        ],
        notes="Kimi는 비교적 더 많은 도구를 호출, OpenAI는 가장 짧은 chain, Grok은 reasoning step이 길다 (대체로).",
    ),
    slide(
        title="Opik에서 보는 4가지",
        bullets=[
            "호출 수 — 4개를 다 부르는가?",
            "병렬화 — 독립 호출을 직렬화하지 않았는가?",
            "인용 — 도구 결과를 final answer에 실제 인용?",
            "reasoning depth ↔ 응답 품질 산점도",
        ],
        warn="호출만 하고 결과를 버리는 패턴 = 안티. prompt 보강 신호.",
    ),
    slide(
        title="다음 챕터 (Ch 3 — Text2Cypher)",
        bullets=[
            "Ch 2 도구는 수치 진단에 강함, 자연어 질의에는 약함.",
            "Ch 3: ontology slice + 3-block prompt 로 자연어 → Cypher 생성.",
            "4-provider Cypher 정확도와 실패 패턴 5종 차단.",
        ],
    ),
]


def main() -> None:
    html = build_deck(
        title="Chapter 2 — Knowledge Graph Qualification",
        subtitle="GDS 4지표 · @function_tool · 4-provider Agent",
        author="seocho · ontology lab · 2026-S03",
        sections=SECTIONS,
        page_title="Ch 2 · Knowledge Graph Qualification",
    )
    out = Path(__file__).resolve().parent.parent / "chapter-02-knowledge-graph-qualification-slides.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html)} bytes, {len(SECTIONS)+1} slides)")


if __name__ == "__main__":
    main()
