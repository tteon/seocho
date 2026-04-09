import json
import logging
import time
from typing import Any, Dict, Optional

from openai import OpenAI

from prompt_manager import PromptManager
from tracing import wrap_openai_client
from exceptions import OpenAIAPIError, LinkingError
from retry_utils import openai_retry

logger = logging.getLogger(__name__)

class EntityLinker:
    def __init__(self, prompt_manager: PromptManager, api_key: str, model: str):
        self.prompt_manager = prompt_manager
        self.client = wrap_openai_client(OpenAI(api_key=api_key))
        self.model = model

    @openai_retry
    def link_entities(
        self,
        extracted_data: dict,
        category: str = "general",
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """
        Uses LLM to perform entity linking and resolution.

        Raises:
            OpenAIAPIError: On transient OpenAI failures (retried automatically).
            LinkingError: On non-retryable failures (e.g. bad JSON response).
        """
        nodes = extracted_data.get("nodes", [])
        if not nodes:
            return extracted_data

        context: Dict[str, Any] = {
            "category": category,
            "entities": json.dumps(nodes, indent=2),
        }
        if extra_context:
            context.update(extra_context)

        start_time = time.time()

        try:
             prompt = self.prompt_manager.render_linking_prompt(context)

             response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an entity linking assistant."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
             )
        except LinkingError:
            raise
        except Exception as e:
            raise OpenAIAPIError(f"OpenAI linking call failed: {e}") from e

        content = response.choices[0].message.content
        latency = time.time() - start_time
        self.prompt_manager.log_result("entity_linking", context["entities"], content, latency)

        try:
            linked_result = json.loads(content)
        except json.JSONDecodeError as e:
            raise LinkingError(
                f"Failed to parse linking response as JSON: {e}"
            ) from e

        # Merge back relationships if LLM dropped them
        if "relationships" not in linked_result:
            linked_result["relationships"] = extracted_data.get("relationships", [])

        return linked_result
