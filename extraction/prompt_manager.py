from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from jinja2 import Template

logger = logging.getLogger(__name__)


class PromptManager:
    def __init__(self, cfg: Any):
        self.cfg = cfg
        default_history = Path(__file__).resolve().parent / "prompt_history.json"
        self.history_file = Path(os.getenv("PROMPT_HISTORY_FILE", str(default_history)))
        self.user_prompts = self._load_user_prompts()

    def render_system_prompt(self, context: Dict[str, Any]) -> str:
        raw_template = self.user_prompts.get("system") or self.cfg.prompts.system
        return self._render_template(raw_template, context)

    def render_user_prompt(self, context: Dict[str, Any]) -> str:
        raw_template = self.user_prompts.get("user") or self.cfg.prompts.user
        return self._render_template(raw_template, context)

    def render_linking_prompt(self, context: Dict[str, Any]) -> str:
        raw_template = self.user_prompts.get("linking") or self.cfg.linking_prompt.linking
        return self._render_template(raw_template, context)

    def log_result(self, prompt_name: str, input_text: str, output: str, latency: float) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "prompt_version": prompt_name,
            "input_preview": (input_text[:200] + "...") if len(input_text) > 200 else input_text,
            "output": output,
            "latency": latency,
        }

        history = []
        if self.history_file.exists():
            try:
                history = json.loads(self.history_file.read_text(encoding="utf-8"))
                if not isinstance(history, list):
                    history = []
            except json.JSONDecodeError:
                logger.warning("Prompt history file contained invalid JSON: %s", self.history_file)
                history = []
            except OSError as exc:
                logger.warning("Failed to read prompt history file %s: %s", self.history_file, exc)
                history = []

        history.append(entry)

        try:
            self.history_file.write_text(
                json.dumps(history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Failed to write prompt history file %s: %s", self.history_file, exc)

    def _load_user_prompts(self) -> Dict[str, str]:
        user_prompts_path = Path(__file__).resolve().parent / "user_prompts.yaml"
        if not user_prompts_path.exists():
            return {}

        try:
            import yaml

            payload = yaml.safe_load(user_prompts_path.read_text(encoding="utf-8")) or {}
            if not isinstance(payload, dict):
                logger.warning("Ignoring invalid user prompt payload in %s", user_prompts_path)
                return {}
            logger.info("Loaded custom prompt overrides from %s", user_prompts_path)
            return {str(key): str(value) for key, value in payload.items()}
        except Exception as exc:
            logger.warning("Failed to load user prompt overrides from %s: %s", user_prompts_path, exc)
            return {}

    @staticmethod
    def _render_template(raw_template: str, context: Dict[str, Any]) -> str:
        template = Template(raw_template)
        return template.render(**context)
