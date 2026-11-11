from app.core.config import Settings
from app.providers.mock import MockProvider
from app.providers.openai_compatible import OpenAICompatibleProvider


def create_provider(settings: Settings):
    provider = settings.llm_provider.lower()
    if provider == "mock":
        return MockProvider()
    if provider == "openai_compatible":
        return OpenAICompatibleProvider(settings)
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
