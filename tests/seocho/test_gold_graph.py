from seocho.eval.gold_graph import score_gold_graph


def test_gold_graph_scores_direction_separately_from_endpoint_overlap():
    score = score_gold_graph(
        [{"source": "Acme", "relation": "CEO_OF", "target": "Jane"}],
        [{"source": "Jane", "relation": "CEO_OF", "target": "Acme"}],
        required_slots=["company"], observed_slots=["company"],
        required_provenance=["source_span"], observed_provenance=[],
    )
    assert score.f1 == 0.0
    assert score.relation_direction_accuracy == 0.0
    assert score.required_slot_recall == 1.0
    assert score.provenance_recall == 0.0
