"""Tunn leverantörswrapper för LLM-anrop.

Modulär enligt CLAUDE.md: `LLMProvider` är det enda gränssnitt resten av
pipelinen (Fas 4+) känner till. Att byta leverantör (DeepSeek -> OpenAI,
Anthropic, ett lokalt Ollama-anrop, ...) innebär att lägga till en klass
här, aldrig att röra `answer.py` eller uppströms kod.

DeepSeeks API är OpenAI-kompatibelt (se CLAUDE.md), så `DeepSeekProvider`
återanvänder `openai`-paketets klient mot DeepSeeks `base_url` istället för
att skriva ett eget HTTP-lager.
"""

import os
from dataclasses import dataclass
from typing import Protocol

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class LLMProvider(Protocol):
    """Gränssnittet varje leverantör måste uppfylla."""

    def complete(self, system: str, user: str) -> str:
        """Skickar system- och användarprompt, returnerar modellens svarstext."""
        ...


@dataclass
class DeepSeekProvider:
    model: str = "deepseek-chat"
    api_key: str | None = None
    base_url: str = "https://api.deepseek.com"
    # Deterministiska svar väger tyngre än variation här: samma fråga mot
    # samma underlag ska ge samma svar, både för testbarhet och för att
    # användaren ska kunna lita på siffrorna.
    temperature: float = 0.0
    # Utan explicit gräns kan ett UI-anrop hänga länge om DeepSeek är
    # långsamt eller nätverket strular. Fas 6 fångar openai.APITimeoutError
    # och visar ett tydligt felmeddelande istället för en frusen skärm.
    timeout: float = 30.0

    def __post_init__(self) -> None:
        key = self.api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY saknas. Lägg den i en .env-fil i "
                "projektroten (se .env.example) eller sätt miljövariabeln."
            )
        self._client = OpenAI(api_key=key, base_url=self.base_url, timeout=self.timeout)

    def complete(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


_PROVIDERS = {"deepseek": DeepSeekProvider}


def get_provider(name: str = "deepseek", **kwargs) -> LLMProvider:
    try:
        cls = _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"okänd LLM-leverantör {name!r}, tillgängliga: {list(_PROVIDERS)}") from None
    return cls(**kwargs)
