from seocho.prompt_ir import (
    PromptSection,
    PromptSectionKind,
    PromptSource,
    PromptStage,
    StagePromptSpec,
)


def test_stable_prefix_hash_ignores_user_input_and_noncacheable_sections() -> None:
    base_system = PromptSection(
        section_id="contract",
        kind=PromptSectionKind.CONTRACT,
        source=PromptSource.SYSTEM_CONTRACT,
        title="Task",
        content="Return one valid JSON object.",
    )
    request_override_v1 = PromptSection(
        section_id="override",
        kind=PromptSectionKind.DEVELOPER_INSTRUCTIONS,
        source=PromptSource.REQUEST_PROMPT_CONTEXT,
        title="Developer Instructions",
        content="Prefer finance labels.",
        cacheable=False,
    )
    request_override_v2 = PromptSection(
        section_id="override",
        kind=PromptSectionKind.DEVELOPER_INSTRUCTIONS,
        source=PromptSource.REQUEST_PROMPT_CONTEXT,
        title="Developer Instructions",
        content="Prefer legal labels.",
        cacheable=False,
    )
    user_v1 = PromptSection(
        section_id="input",
        kind=PromptSectionKind.USER_INPUT,
        source=PromptSource.USER_INPUT,
        title="Question",
        content="Who works at Samsung?",
    )
    user_v2 = PromptSection(
        section_id="input",
        kind=PromptSectionKind.USER_INPUT,
        source=PromptSource.USER_INPUT,
        title="Question",
        content="Who works at OpenAI?",
    )

    spec_v1 = StagePromptSpec(
        stage=PromptStage.INTENT_CLASSIFICATION,
        task_hint="intent_classification",
        reasoning_mode=False,
        system_sections=[base_system, request_override_v1],
        user_sections=[user_v1],
        output_schema='{"intent": "string"}',
        verification_rules=["Return only valid JSON."],
    )
    spec_v2 = StagePromptSpec(
        stage=PromptStage.INTENT_CLASSIFICATION,
        task_hint="intent_classification",
        reasoning_mode=False,
        system_sections=[base_system, request_override_v2],
        user_sections=[user_v2],
        output_schema='{"intent": "string"}',
        verification_rules=["Return only valid JSON."],
    )

    assert spec_v1.stable_prefix_hash() == spec_v2.stable_prefix_hash()


def test_receipt_reports_semantic_precedence_in_contract_order() -> None:
    spec = StagePromptSpec(
        stage=PromptStage.ENTITY_EXTRACTION,
        task_hint="json_extraction",
        reasoning_mode=False,
        system_sections=[
            PromptSection(
                section_id="runtime",
                kind=PromptSectionKind.METADATA,
                source=PromptSource.RUNTIME_CANDIDATE,
                title="Runtime Candidate",
                content="fallback=true",
            ),
            PromptSection(
                section_id="artifacts",
                kind=PromptSectionKind.ONTOLOGY,
                source=PromptSource.APPROVED_ARTIFACTS,
                title="Approved Artifacts",
                content="Company, Person",
            ),
            PromptSection(
                section_id="graph",
                kind=PromptSectionKind.METADATA,
                source=PromptSource.GRAPH_TARGET_METADATA,
                title="Graph Target",
                content="graph_id=finance",
            ),
            PromptSection(
                section_id="request",
                kind=PromptSectionKind.DEVELOPER_INSTRUCTIONS,
                source=PromptSource.REQUEST_PROMPT_CONTEXT,
                title="Prompt Context Override",
                content="Prefer canonical terms.",
                cacheable=False,
            ),
        ],
    )

    receipt = spec.build_receipt(provider="kimi", query_mode="semantic")

    assert receipt.precedence_sources == [
        "graph_target_metadata",
        "approved_artifacts",
        "request_prompt_context",
        "runtime_candidate",
    ]


def test_receipt_and_spec_serialize_enum_values() -> None:
    spec = StagePromptSpec(
        stage=PromptStage.ANSWER_SYNTHESIS,
        task_hint="answer_synthesis",
        reasoning_mode=False,
        system_sections=[
            PromptSection(
                section_id="evidence",
                kind=PromptSectionKind.EVIDENCE,
                source=PromptSource.RETRIEVAL_EVIDENCE,
                title="Evidence",
                content='[{"name":"Alice"}]',
            )
        ],
        response_format={"type": "json_object"},
        adapter_hints={"openai": {"temperature": 0.0}},
    )

    spec_payload = spec.to_dict()
    receipt_payload = spec.build_receipt(provider="openai").to_dict()

    assert spec_payload["stage"] == "answer_synthesis"
    assert spec_payload["system_sections"][0]["kind"] == "evidence"
    assert spec_payload["system_sections"][0]["source"] == "retrieval_evidence"
    assert receipt_payload["stage"] == "answer_synthesis"
    assert receipt_payload["provider"] == "openai"
    assert receipt_payload["adapter_hint_keys"] == ["openai"]
