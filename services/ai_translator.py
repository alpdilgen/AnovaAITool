import openai
import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

# Reasoning models that do not support temperature and use max_completion_tokens
REASONING_MODELS = {'o1', 'o1-mini', 'o1-preview', 'o3', 'o3-mini', 'o4-mini'}

class AITranslator:
    def __init__(self, provider, api_key, model):
        self.provider = provider
        self.api_key = api_key
        self.model = model

        if provider == "OpenAI":
            self.client = openai.OpenAI(api_key=api_key)
        elif provider == "Anthropic":
            self.client = anthropic.Anthropic(api_key=api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def translate_batch(self, prompt: str):
        """Sends prompt to AI with retries"""
        # Debug guard - catch bad prompt before it reaches the API
        if not isinstance(prompt, str):
            raise ValueError(f"translate_batch received non-string prompt: type={type(prompt).__name__}, value={repr(prompt)}")
        if not prompt.strip():
            raise ValueError(f"translate_batch received empty/whitespace prompt (len={len(prompt)})")
        try:
            if self.provider == "OpenAI":
                is_reasoning = any(self.model.startswith(r) for r in REASONING_MODELS)
                params = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "You are a helpful translation assistant."},
                        {"role": "user", "content": prompt}
                    ],
                }
                if is_reasoning:
                    params["max_completion_tokens"] = 8192
                else:
                    params["temperature"] = 0.1
                    params["max_tokens"] = 8192

                response = self.client.chat.completions.create(**params)
                return response.choices[0].message.content, response.usage.total_tokens

            elif self.provider == "Anthropic":
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=8192,
                    temperature=0.1,
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
                return response.content[0].text, response.usage.input_tokens + response.usage.output_tokens

        except Exception as e:
            print(f"AI API Error ({type(e).__name__}): {e}")
            raise e
