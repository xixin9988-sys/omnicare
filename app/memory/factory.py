from app.core.config import Settings
from app.memory.local_adapter import LocalFileMemoryAdapter
from app.memory.nanomemory_http import NanoMemoryHTTPAdapter


def create_memory_adapter(settings: Settings):
    backend = settings.memory_backend.lower()
    if backend == "local":
        return LocalFileMemoryAdapter()
    if backend == "nanomemory_http":
        return NanoMemoryHTTPAdapter(settings)
    raise ValueError(f"Unsupported MEMORY_BACKEND: {settings.memory_backend}")
