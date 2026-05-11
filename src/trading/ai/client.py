"""Claude API client with prompt caching and JSON extraction/retry.

Supports two backends:
1. Anthropic Python SDK (requires ANTHROPIC_API_KEY)
2. Claude Code CLI (`claude -p`, uses CLI's own authentication)
"""

import json
import os
import re
import subprocess
from typing import Optional, Union

from ..core.config import ClaudeConfig


class ClaudeClient:
    """Anthropic Claude wrapper: generates JSON with prompt caching and parse retry."""

    def __init__(self, config: ClaudeConfig):
        self.config = config
        self._client = None  # lazy init

    @property
    def use_cli(self) -> bool:
        """True when CLI mode is active (explicit config or no API key)."""
        return self.config.use_cli or not self.config.api_key

    @property
    def is_configured(self) -> bool:
        """True when either API key is set or CLI mode is available."""
        if self.use_cli:
            return self._cli_available()
        return bool(self.config.api_key)

    def _cli_available(self) -> bool:
        """Check if claude CLI is installed and accessible."""
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _get_client(self):
        if self._client is None:
            if not self.config.api_key:
                raise ValueError(
                    "Anthropic API key not configured. "
                    "Set ANTHROPIC_API_KEY in your .env file or enable use_cli in config."
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

    def _generate_json_cli(
        self,
        system: str,
        user: str,
        max_retries: int = 1,
        model_override: Optional[str] = None,
    ) -> dict:
        """Call Claude via CLI subprocess and return parsed JSON."""
        model = model_override or self.config.model
        combined_prompt = (
            f"{system}\n\n---\n\n{user}\n\n"
            "IMPORTANT: Respond with ONLY a valid JSON object. "
            "No markdown fences, no explanatory text, just the raw JSON."
        )

        last_response_text = ""
        last_error: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            if attempt == 0:
                prompt_text = combined_prompt
            else:
                prompt_text = (
                    f"{combined_prompt}\n\n"
                    f"[Previous attempt failed to parse as JSON. Error: {last_error}. "
                    f"Previous response: {last_response_text[:300]}]\n\n"
                    "Please respond with ONLY a valid JSON object."
                )

            cmd = [
                "claude",
                "-p",
                "--model", model,
                "--output-format", "text",
                "--allowedTools", "",
                "--no-session-persistence",
            ]

            # Remove CLAUDECODE env var to allow nested CLI invocation
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

            result = subprocess.run(
                cmd,
                input=prompt_text,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                raise RuntimeError(f"Claude CLI failed (exit {result.returncode}): {stderr}")

            last_response_text = result.stdout

            try:
                return self._extract_json(last_response_text)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= max_retries:
                    raise ValueError(
                        f"Failed to parse JSON after {max_retries + 1} CLI attempt(s). "
                        f"Last error: {exc}. "
                        f"Last response: {last_response_text[:300]!r}"
                    ) from exc

        raise RuntimeError("Unreachable")

    def generate_json(
        self,
        system: str,
        user: str,
        max_retries: int = 1,
        cache_system: bool = True,
        model_override: Optional[str] = None,
    ) -> dict:
        """Call Claude and return parsed JSON.

        Automatically uses CLI mode when use_cli is enabled or no API key is set.
        """
        if self.use_cli:
            return self._generate_json_cli(
                system=system,
                user=user,
                max_retries=max_retries,
                model_override=model_override,
            )

        # SDK path
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
                model=model_override or self.config.model,
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
