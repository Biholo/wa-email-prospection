import os

import anthropic


MODEL = "claude-haiku-4-5-20251001"


class ClaudeService:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = MODEL

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> tuple[str, anthropic.types.Usage]:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.7,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text.strip(), response.usage
