import json
import time
from openai import OpenAI
from prompt_manager import PromptManager
from jinja2 import Template

class EntityLinker:
    def __init__(self, prompt_manager: PromptManager, api_key: str, model: str):
        self.prompt_manager = prompt_manager
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def link_entities(self, extracted_data: dict, category: str = "general") -> dict:
        """
        Uses LLM to perform entity linking and resolution.
        """
        nodes = extracted_data.get("nodes", [])
        if not nodes:
            return extracted_data

        # Render prompt
        # Note: In a real app we might put this in PromptManager, but for simplicity constructing here
        # or assuming PromptManager handles the template loading via direct access or a new method.
        # Let's assume we added a method or use raw config if accessible, 
        # but better to use the pattern establish in PromptManager.
        
        # We need to access the linking prompt. 
        # Let's simply construct the context and render manually if PromptManager 
        # doesn't have a specific method for 'linking'.
        # Actually from previous step, we added 'linking' to config root 'linking_prompt' or via defaults.
        # Let's assume config structure: cfg.linking_prompt.linking
        
        try:
             # Just use the template directly from config if available or pass to manager
             # For strictly following the pattern, we'd add 'render_linking_prompt' to PromptManager.
             # But let's do it here for speed if PromptManager is pure Jinja.
             
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
             
             # Merge back relationships if LLM dropped them, or use LLM's full output
             # ideally prompt asks for full graph back.
             if "relationships" not in linked_result:
                 linked_result["relationships"] = extracted_data.get("relationships", [])
                 
             return linked_result

        except Exception as e:
            print(f"Error during entity linking: {e}")
            return extracted_data
