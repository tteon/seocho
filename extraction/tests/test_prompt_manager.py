import json
from pathlib import Path
from typing import Any

from config import PromptTemplates, LinkingPromptTemplates
from prompt_manager import PromptManager


class DummyConfig:
    def __init__(self):
        self.prompts = PromptTemplates(system="test sys", user="test user")
        self.linking_prompt = LinkingPromptTemplates(linking="test linking")


def test_prompt_manager_log_result_appends_jsonl(tmp_path: Path, monkeypatch: Any):
    history_file = tmp_path / "prompt_history.jsonl"
    monkeypatch.setenv("PROMPT_HISTORY_FILE", str(history_file))

    manager = PromptManager(cfg=DummyConfig())

    manager.log_result(
        prompt_name="test_prompt_1",
        input_text="input 1",
        output="output 1",
        latency=0.1,
    )

    manager.log_result(
        prompt_name="test_prompt_2",
        input_text="input 2",
        output="output 2",
        latency=0.2,
    )

    lines = history_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    entry1 = json.loads(lines[0])
    assert entry1["prompt_version"] == "test_prompt_1"
    assert entry1["input_preview"] == "input 1"
    assert entry1["output"] == "output 1"
    assert entry1["latency"] == 0.1

    entry2 = json.loads(lines[1])
    assert entry2["prompt_version"] == "test_prompt_2"
    assert entry2["input_preview"] == "input 2"
    assert entry2["output"] == "output 2"
    assert entry2["latency"] == 0.2
