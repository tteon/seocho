import json
import time
from openai import OpenAI
from prompt_manager import PromptManager

class EntityExtractor:
    def __init__(self, prompt_manager: PromptManager, api_key: str, model: str):
        self.prompt_manager = prompt_manager
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def extract_entities(self, text: str, category: str = "general") -> dict:
        """
        Extracts entities from the text using the configured prompt and OpenAI.
        """
        context = {"text": text, "category": category}
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
            
        except Exception as e:
            print(f"Error during extraction: {e}")
            return {"nodes": [], "relationships": []}
