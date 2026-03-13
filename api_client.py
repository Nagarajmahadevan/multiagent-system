"""
API client for routing requests to the correct model provider.
Supports DeepSeek, Google Gemini, and OpenAI — all via direct HTTP calls.
No third-party agent frameworks used.
"""

import os
import time
import logging
import requests
import yaml

logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load the master config.yaml file."""
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_api_key(provider: str, config: dict) -> str:
    """Retrieve the actual API key from environment variables."""
    env_var = config["api_keys"][provider]
    key = os.environ.get(env_var)
    if not key:
        raise ValueError(
            f"API key not found. Set the environment variable '{env_var}' "
            f"for provider '{provider}'."
        )
    return key


class APIClient:
    """Routes agent requests to the correct provider API."""

    def __init__(self, config: dict):
        self.config = config
        self.base_urls = config["api_base_urls"]
        self.caching = config.get("caching", {})

    def call_agent(
        self,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> dict:
        """
        Call the appropriate model API for the given agent.

        Returns:
            dict with keys: content, input_tokens, output_tokens, model
        """
        agent_cfg = self.config["agents"][agent_name]
        provider = agent_cfg["provider"]
        model = agent_cfg["model"]

        if provider == "deepseek":
            return self._call_deepseek(model, system_prompt, user_prompt, max_tokens)
        elif provider == "gemini":
            return self._call_gemini(model, system_prompt, user_prompt, max_tokens)
        elif provider == "openai":
            return self._call_openai(model, system_prompt, user_prompt, max_tokens)
        else:
            raise ValueError(f"Unknown provider '{provider}' for agent '{agent_name}'")

    # ─────────────────────────────────────────────────────────────────────
    # DeepSeek (OpenAI-compatible API)
    # ─────────────────────────────────────────────────────────────────────

    def _call_deepseek(
        self, model: str, system_prompt: str, user_prompt: str, max_tokens: int
    ) -> dict:
        api_key = get_api_key("deepseek", self.config)
        url = f"{self.base_urls['deepseek']}/chat/completions"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Enable prompt caching for DeepSeek if configured
        if self.caching.get("deepseek", False):
            messages[0]["cache_control"] = {"type": "ephemeral"}

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        # DeepSeek R1 may return reasoning_content alongside content
        content = choice["message"].get("content") or ""
        usage = data.get("usage", {})

        return {
            "content": content,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "model": model,
        }

    # ─────────────────────────────────────────────────────────────────────
    # Google Gemini
    # ─────────────────────────────────────────────────────────────────────

    def _call_gemini(
        self, model: str, system_prompt: str, user_prompt: str, max_tokens: int
    ) -> dict:
        api_key = get_api_key("gemini", self.config)
        url = (
            f"{self.base_urls['gemini']}/models/{model}:generateContent"
            f"?key={api_key}"
        )

        payload = {
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {
                    "parts": [{"text": user_prompt}]
                }
            ],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.7,
            },
        }

        headers = {"Content-Type": "application/json"}

        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        content = ""
        if "candidates" in data and data["candidates"]:
            parts = data["candidates"][0].get("content", {}).get("parts", [])
            content = "".join(p.get("text", "") for p in parts)

        usage = data.get("usageMetadata", {})

        return {
            "content": content,
            "input_tokens": usage.get("promptTokenCount", 0),
            "output_tokens": usage.get("candidatesTokenCount", 0),
            "model": model,
        }

    # ─────────────────────────────────────────────────────────────────────
    # OpenAI
    # ─────────────────────────────────────────────────────────────────────

    def _call_openai(
        self, model: str, system_prompt: str, user_prompt: str, max_tokens: int
    ) -> dict:
        api_key = get_api_key("openai", self.config)
        url = f"{self.base_urls['openai']}/chat/completions"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        return {
            "content": content,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "model": model,
        }
