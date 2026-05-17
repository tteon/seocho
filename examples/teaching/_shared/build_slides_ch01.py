"""Build chapter-01 Reveal.js slide deck.

Run:
    python -m _shared.build_slides_ch01
"""

from __future__ import annotations

from pathlib import Path

from _shared.slide_template import Slide, build_deck, slide


SECTIONS: list[Slide] = [
    slide(
        title="왜 이 챕터부터인가?",
        bullets=[
            "그래프 품질·라우팅·debate 모두 인덱싱의 결과물 위에 얹힌다.",
            "여기서 만든 (:Source)-(:Chunk)-(:Entity) 구조와 community_id가 Ch 2~5의 입력.",
            "4-provider 비교는 인덱싱 단계가 가장 명확 — 같은 청크에 대한 추출 결과 직접 비교.",
        ],
        callout="이 노트북이 통과하지 않으면 다음 4개 챕터가 모두 막힌다.",
    ),
    slide(
        title="1.1 Source → Chunk → Entity (3-layer)",
        body="""<div class="twocol">
<div>
<h3>분리하지 않으면</h3>
<ul>
<li>출처 추적 불가</li>
<li>청크 단위 재계산 불가</li>
<li>동일 엔터티 중복 노드 폭증</li>
<li>cascade 정책 모호</li>
</ul>
</div>
<div>
<h3>분리하면</h3>
<ul>
<li>(:Source)에 메타데이터 응집</li>
<li>(:Chunk) 부분 재인덱싱</li>
<li>(:Entity) dedup + 다대다 MENTIONS</li>
<li>DETACH DELETE 명확한 lineage</li>
</ul>
</div>
</div>""",
        notes="RDF로 같은 표현을 하려면 reification이 필요. LPG는 edge property로 끝.",
    ),
    slide(
        title="Cypher: 3-layer 카운트",
        code=(
            "cypher",
            "MATCH (src:Source)\n"
            "OPTIONAL MATCH (src)-[:HAS_CHUNK]->(c:Chunk)\n"
            "OPTIONAL MATCH (c)-[m:MENTIONS]->(e)\n"
            "RETURN count(DISTINCT src) AS sources,\n"
            "       count(DISTINCT c)   AS chunks,\n"
            "       count(DISTINCT e)   AS entities,\n"
            "       count(m)            AS mentions",
        ),
        code_caption="회사명이 청크 5개에 등장 → Entity 1개 / MENTIONS 5개",
    ),
    slide(
        title="1.2 Ontology Slice — 왜 발췌하는가",
        bullets=[
            "전체 ontology를 prompt에 부으면 token + attention 낭비.",
            "intent별로 관련 class/property만 선별 → 정확도 ↑, 비용 ↓.",
            "Ch 3(text2cypher)에서 다시 등장 — query intent별 slice.",
        ],
        code=(
            "python",
            "from seocho.ontology_slice import slice_ontology\n"
            "sliced = slice_ontology(ontology, intent='risk', max_classes=8)",
        ),
    ),
    slide(
        title="1.2 Extraction Prompt — 3 블록",
        bullets=[
            "Block 1: ontology slice (허용 class/property만)",
            "Block 2: 강제 JSON schema {entities: [{class, name, evidence_span}]}",
            "Block 3: evidence_span은 원문의 verbatim substring",
        ],
        callout="evidence_span 강제는 hallucination을 가장 효과적으로 차단한다.",
    ),
    slide(
        title="왜 4-provider 동시 시연인가",
        body="""<table>
<thead><tr><th>provider</th><th>model</th><th>강점</th><th>주의</th></tr></thead>
<tbody>
<tr><td>OpenAI</td><td>gpt-4o-mini</td><td>tool-use 생태계, 기본 baseline</td><td>비용 중간</td></tr>
<tr><td>Kimi</td><td>kimi-k2.5</td><td>long-context, 한국어/중국어</td><td>temp=1.0 고정</td></tr>
<tr><td>DeepSeek</td><td>deepseek-chat</td><td>저비용, JSON-mode 안정</td><td>영문 응답 경향</td></tr>
<tr><td>Grok</td><td>grok-4.20-reasoning</td><td>fresh web grounding, reasoning</td><td>스타일 발산 큼</td></tr>
</tbody>
</table>""",
        notes="강의 청중이 \"왜 굳이 4개?\" 라 물어보면 — 모델별 quirk 자체가 학습 컨텐츠.",
    ),
    slide(
        title="1.3 LPG Metadata 우위",
        code=(
            "cypher",
            "MATCH (c:Chunk)-[r:MENTIONS]->(e)\n"
            "WHERE r.confidence > 0.8\n"
            "  AND r.extracted_by STARTS WITH 'gpt-4o'\n"
            "RETURN e.name, r.confidence, r.extracted_at",
        ),
        callout="edge property — RDF는 reification 없이는 못함.",
    ),
    slide(
        title="1.4 Community Detection (Louvain)",
        bullets=[
            "Entity 그래프 위에 community 자동 분해.",
            "결과를 community_id property로 노드에 write-back.",
            "Ch 4 라우팅에서 \"이 질문은 어느 community?\" 판단의 근거.",
        ],
        code=(
            "cypher",
            "CALL gds.graph.project.cypher('ch01-finder',\n"
            "  'MATCH (e) WHERE NOT e:Source AND NOT e:Chunk RETURN id(e) AS id',\n"
            "  'MATCH (e1)<-[:MENTIONS]-(c:Chunk)-[:MENTIONS]->(e2)\n"
            "   WHERE id(e1)<id(e2) RETURN id(e1) AS source, id(e2) AS target')\n"
            "CALL gds.louvain.write('ch01-finder', {writeProperty: 'community_id'})\n"
            "  YIELD communityCount, modularity",
        ),
    ),
    slide(
        title="Opik에서 확인할 것",
        bullets=[
            "프로젝트: teaching-ch01-{본인}",
            "trace: client.add() · 4-provider extraction · GDS Louvain.",
            "metadata에서 provider/model/total_tokens/latency_ms 비교.",
            "workspace 'seocho' 안에서 멤버 간 결과 공유 가능.",
        ],
        warn="OPIK_API_KEY가 없으면 JSONL만 기록됨 — 강의 후 동기화 권장.",
    ),
    slide(
        title="체크포인트",
        bullets=[
            "Source 삭제 시 cascade 범위와 그 정책의 위치(코드/제약/노트북)?",
            "evidence_span 강제로 차단되는 hallucination 유형은?",
            "community_id를 Entity property로 둘 때와 별도 노드로 둘 때의 트레이드오프?",
        ],
    ),
    slide(
        title="다음 챕터 (Ch 2 — Qualification)",
        bullets=[
            "오늘 만든 그래프 위에 GDS 4종 지표를 측정.",
            "각 지표를 @function_tool로 노출해 agent가 자율적으로 호출.",
            "4-provider agent의 도구 선택 reasoning을 Opik에서 비교.",
        ],
        callout="\"그래프 품질 평가해줘\" — 한 줄 prompt로 agent가 도구 4개를 어떻게 조합하는지 보기.",
    ),
]


def main() -> None:
    html = build_deck(
        title="Chapter 1 — Knowledge Graph Indexing",
        subtitle="Source · Chunk · Entity 3-layer + Ontology-aware Extraction",
        author="seocho · ontology lab · 2026-S03",
        sections=SECTIONS,
        page_title="Ch 1 · Knowledge Graph Indexing",
    )
    out = Path(__file__).resolve().parent.parent / "chapter-01-knowledge-graph-indexing-slides.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html)} bytes, {len(SECTIONS)+1} slides)")


if __name__ == "__main__":
    main()
