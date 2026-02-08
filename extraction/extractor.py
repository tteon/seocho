import json
import logging
import time
from typing import Optional, Dict, Any
from openai import OpenAI
from prompt_manager import PromptManager

logger = logging.getLogger(__name__)

class EntityExtractor:
    def __init__(self, prompt_manager: PromptManager, api_key: str, model: str):
        self.prompt_manager = prompt_manager
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def extract_entities(
        self,
        text: str,
        category: str = "general",
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """
        Extracts entities from the text using the configured prompt and OpenAI.

        Args:
            text: Raw text to extract entities from.
            category: Data category for prompt routing.
            extra_context: Additional template variables (e.g. ontology context).
        """
        context = {"text": text, "category": category}
        if extra_context:
            context.update(extra_context)
        system_prompt = self.prompt_manager.render_system_prompt(context)
        user_prompt = self.prompt_manager.render_user_prompt(context)

        start_time = time.time()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"}
            )

            latency = time.time() - start_time
            content = response.choices[0].message.content

            # Log result
            self.prompt_manager.log_result("default", text, content, latency)

            return json.loads(content)

        except json.JSONDecodeError as e:
            logger.error("Failed to parse extraction response as JSON: %s", e)
            return {"nodes": [], "relationships": []}
        except Exception as e:
            logger.error("Error during extraction: %s", e)
            return {"nodes": [], "relationships": []}
