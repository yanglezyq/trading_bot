"""Claude API client with prompt caching and JSON extraction/retry."""

import json
import re
from typing import Optional, Union

from ..core.config import ClaudeConfig


class ClaudeClient:
    """Anthropic Claude wrapper: generates JSON with prompt caching and parse retry."""

    def __init__(self, config: ClaudeConfig):
        self.config = config
        self._client = None  # lazy init

    @property
    def is_configured(self) -> bool:
        """True when ANTHROPIC_API_KEY is set."""
        return bool(self.config.api_key)

    def _get_client(self):
        if self._client is None:
            if not self.is_configured:
                raise ValueError(
                    "Anthropic API key not configured. "
                    "Set ANTHROPIC_API_KEY in your .env file."
                )
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.config.api_key)
        return self._client

    def _extract_json(self, text: str) -> dict:
        """Extract the first JSON object from a text response."""
        stripped = text.strip()

        # 1. Try direct parse
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # 2. Try markdown code fence  ```json ... ```
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        if fence:
            try:
                return json.loads(fence.group(1))
            except json.JSONDecodeError:
                pass

        # 3. Try first { ... } block (greedy from first { to last })
        brace = re.search(r"(\{.*\})", stripped, re.DOTALL)
        if brace:
            try:
                return json.loads(brace.group(1))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"No valid JSON found in response (first 200 chars): {stripped[:200]!r}")

    def _build_system_content(self, system: str, cache: bool) -> Union[list, str]:
        """Wrap system text in cache_control block if caching is enabled."""
        if cache and self.config.cache_enabled:
            return [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        return system

    def generate_json(
        self,
        system: str,
        user: str,
        max_retries: int = 1,
        cache_system: bool = True,
    ) -> dict:
        """Call Claude and return parsed JSON; retries once with repair hint on parse failure."""
        client = self._get_client()
        system_content = self._build_system_content(system, cache_system)

        last_response_text = ""
        last_error: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            if attempt == 0:
                messages = [{"role": "user", "content": user}]
            else:
                messages = [
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": last_response_text},
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response could not be parsed as valid JSON. "
                            f"Error: {last_error}. "
                            "Please respond with ONLY a valid JSON object, no other text, "
                            "no markdown fences."
                        ),
                    },
                ]

            response = client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=system_content,
                messages=messages,
            )
            last_response_text = response.content[0].text

            try:
                return self._extract_json(last_response_text)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= max_retries:
                    raise ValueError(
                        f"Failed to parse JSON after {max_retries + 1} attempt(s). "
                        f"Last error: {exc}. "
                        f"Last response: {last_response_text[:300]!r}"
                    ) from exc

        raise RuntimeError("Unreachable")
