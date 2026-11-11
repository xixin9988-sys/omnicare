from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class LLMResult:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_source: str = "unknown"


class LLMProvider(Protocol):
    provider_name: str
    model_name: str

    async def reply(self, *, system: str, user: str, memory_context: str = "") -> LLMResult: ...
