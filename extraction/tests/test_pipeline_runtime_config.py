"""Tests for non-Hydra pipeline runtime config loading."""

from pathlib import Path

from config import load_pipeline_runtime_config


def test_load_pipeline_runtime_config_from_prompt_files(tmp_path, monkeypatch):
    prompts_dir = Path(tmp_path)
    (prompts_dir / "default.yaml").write_text(
        "system: test-system\nuser: test-user\n",
        encoding="utf-8",
    )
    (prompts_dir / "linking.yaml").write_text(
        "linking: test-linking\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("EXTRACTION_MOCK_DATA", "false")
    monkeypatch.setenv("ENABLE_RULE_CONSTRAINTS", "true")

    cfg = load_pipeline_runtime_config(prompts_dir=prompts_dir)

    assert cfg.model == "gpt-test"
    assert cfg.openai_api_key == "k"
    assert cfg.mock_data is False
    assert cfg.enable_rule_constraints is True
    assert cfg.prompts.system == "test-system"
    assert cfg.prompts.user == "test-user"
    assert cfg.linking_prompt.linking == "test-linking"
