import json
import os
from datetime import datetime
from jinja2 import Template
from omegaconf import DictConfig

class PromptManager:
    def __init__(self, cfg: DictConfig):
        self.cfg = cfg
        self.history_file = "prompt_history.json"
        
    def render_system_prompt(self, context: dict) -> str:
        template = Template(self.cfg.prompts.system)
        return template.render(**context)

    def render_user_prompt(self, context: dict) -> str:
        template = Template(self.cfg.prompts.user)
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
