from app.providers.base import LLMResult


class MockProvider:
    provider_name = "mock"
    model_name = "mock-deterministic"

    async def reply(self, *, system: str, user: str, memory_context: str = "") -> LLMResult:
        prompt_tokens = max(1, (len(system) + len(user) + len(memory_context)) // 4)
        text = (
            "我已收到你的问题。当前 OmniCare 使用离线 Mock Provider，"
            "因此这条回复用于验证 API、记忆和治理链路；配置真实模型后即可启用自然语言生成。"
        )
        return LLMResult(
            text=text,
            provider=self.provider_name,
            model=self.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=max(1, len(text) // 4),
            usage_source="mock",
        )
