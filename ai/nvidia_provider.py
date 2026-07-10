from .openai_base_provider import OpenAIBaseProvider


class NvidiaProvider(OpenAIBaseProvider):
    """NVIDIA NIM 的 OpenAI 兼容接口提供者。"""

    def __init__(self):
        super().__init__(
            env_prefix="NVIDIA",
            default_model="openai/gpt-oss-120b",
            default_api_base="https://integrate.api.nvidia.com/v1",
        )
