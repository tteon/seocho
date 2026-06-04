"""Unit tests for masked-alignment few-shot (ADR-0103 S10) — pure + fake embed."""

from __future__ import annotations

from seocho.query.fewshot import FewShotIndex, mask_cypher, mask_question


def test_mask_cypher_replaces_structure_and_literals():
    cy = ("MATCH (c:Company {cik: $cik})-[:HAS_OBSERVATION]->(o:Observation) "
          "WHERE o.concept_id = $concept_id AND o.value_num > 1000 "
          "RETURN o.value_num, o.period_key")
    m = mask_cypher(cy)
    # no concrete labels / property keys / literals survive
    assert "Company" not in m and "Observation" not in m
    assert "HAS_OBSERVATION" not in m
    assert "concept_id" not in m and "value_num" not in m
    assert "1000" not in m and "$cik" not in m
    # structural tokens present
    assert "[LABEL]" in m and "[REL]" in m and "[PROP]" in m and "[VALUE]" in m


def test_mask_question_replaces_surfaces():
    q = "What was Apple Inc.'s total revenue for fiscal year 2024?"
    m = mask_question(q, entity="Apple Inc.", metric="total revenue", period="fiscal year 2024")
    assert "[ENTITY]" in m and "[METRIC]" in m and "[PERIOD]" in m
    assert "Apple" not in m and "revenue" not in m and "2024" not in m


def test_mask_question_is_structure_invariant_across_surfaces():
    a = mask_question("What was Apple Inc.'s total revenue for fiscal year 2024?",
                      entity="Apple Inc.", metric="total revenue", period="fiscal year 2024")
    b = mask_question("What was Microsoft's operating income for fiscal year 2023?",
                      entity="Microsoft", metric="operating income", period="fiscal year 2023")
    assert a == b   # same skeleton → cross-entity/metric retrieval works


# fake embed: 2-dim vector keyed on masked-skeleton hash buckets so identical
# skeletons embed identically.
def _fake_embed(texts):
    out = []
    for t in texts:
        out.append([float(len(t)), float(sum(map(ord, t)) % 97)])
    return out


def test_fewshot_index_retrieves_structurally_similar():
    # structure-aware retrieval via the deterministic masked-skeleton overlap
    # (lexical path): a metric-lookup query retrieves the metric-lookup example,
    # not the board-members one, despite different entity/metric surfaces.
    idx = FewShotIndex(embed_fn=None)
    idx.add(question="What was Apple Inc.'s total revenue for fiscal year 2024?",
            cypher="MATCH (c:Company {cik:$cik})-[:HAS_OBSERVATION]->(o:Observation) RETURN o.value_num",
            entity="Apple Inc.", metric="total revenue", period="fiscal year 2024")
    idx.add(question="List Apple's board members",
            cypher="MATCH (c:Company)-[:HAS_MEMBER]->(p:Person) RETURN p.name",
            entity="Apple", metric="board members", period="")
    hits = idx.search("What was Microsoft's net income for fiscal year 2023?",
                      entity="Microsoft", metric="net income", period="fiscal year 2023", k=1)
    assert hits
    # the metric-lookup skeleton should win over the board-members one
    assert "HAS_OBSERVATION" in hits[0][0].cypher


def test_fewshot_index_lexical_fallback_without_embed():
    idx = FewShotIndex(embed_fn=None)   # no backend -> lexical overlap fallback
    idx.add(question="What was Apple's revenue for FY2024?",
            cypher="MATCH ... RETURN o.value_num", entity="Apple", metric="revenue", period="FY2024")
    hits = idx.search("What was Tesla's revenue for FY2023?",
                      entity="Tesla", metric="revenue", period="FY2023", k=1)
    assert hits and hits[0][1] > 0.0


def test_fewshot_index_empty():
    assert FewShotIndex(embed_fn=_fake_embed).search("anything") == []
