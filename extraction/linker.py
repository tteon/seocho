import json
import logging
import time
from openai import OpenAI
from prompt_manager import PromptManager
from jinja2 import Template
from tracing import wrap_openai_client

logger = logging.getLogger(__name__)

class EntityLinker:
    def __init__(self, prompt_manager: PromptManager, api_key: str, model: str):
        self.prompt_manager = prompt_manager
        self.client = wrap_openai_client(OpenAI(api_key=api_key))
        self.model = model

    def link_entities(self, extracted_data: dict, category: str = "general") -> dict:
        """
        Uses LLM to perform entity linking and resolution.
        """
        nodes = extracted_data.get("nodes", [])
        if not nodes:
            return extracted_data

        try:
             template_str = self.prompt_manager.cfg.linking_prompt.linking
             template = Template(template_str)
             prompt = template.render(category=category, entities=json.dumps(nodes, indent=2))

             response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an entity linking assistant."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
             )

             content = response.choices[0].message.content
             linked_result = json.loads(content)

             # Merge back relationships if LLM dropped them
             if "relationships" not in linked_result:
                 linked_result["relationships"] = extracted_data.get("relationships", [])

             return linked_result

        except json.JSONDecodeError as e:
            logger.error("Failed to parse linking response as JSON: %s", e)
            return extracted_data
        except Exception as e:
            logger.error("Error during entity linking: %s", e)
            return extracted_data
