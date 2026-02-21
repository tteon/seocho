import json
import os
from datetime import datetime
from jinja2 import Template
from typing import Any

class PromptManager:
    def __init__(self, cfg: Any):
        self.cfg = cfg
        self.history_file = "prompt_history.json"
        
        # Load user override prompts
        self.user_prompts = {}
        user_prompts_path = os.path.join(os.path.dirname(__file__), "user_prompts.yaml")
        if os.path.exists(user_prompts_path):
            import yaml
            print(f"📖 Loading Custom User Prompts from {user_prompts_path}")
            with open(user_prompts_path, 'r') as f:
                self.user_prompts = yaml.safe_load(f) or {}

    def render_system_prompt(self, context: dict) -> str:
        # Check for user override
        raw_template = self.user_prompts.get("system") or self.cfg.prompts.system
        template = Template(raw_template)
        return template.render(**context)

    def render_user_prompt(self, context: dict) -> str:
        raw_template = self.user_prompts.get("user") or self.cfg.prompts.user
        template = Template(raw_template)
        return template.render(**context)

    def log_result(self, prompt_name: str, input_text: str, output: str, latency: float):
        """
        Logs the prompt execution result for comparison.
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "prompt_version": prompt_name,
            "input_preview": input_text[:50] + "...",
            "output": output,
            "latency": latency
        }
        
        history = []
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    history = json.load(f)
            except json.JSONDecodeError:
                pass
        
        history.append(entry)
        
        with open(self.history_file, 'w') as f:
            json.dump(history, f, indent=2)
