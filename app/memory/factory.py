"""Memory backend selection.

memguard_http is the production backend: every tenant boundary, safety check and quota is
enforced by the gateway. nanomemory_http talks to NanoMemory directly (integration tests and
trusted single-tenant deployments). local is the demo-only JSONL adapter and is flagged by the
production pre-flight check.
"""

from app.core.config import Settings
from app.memory.local_adapter import LocalFileMemoryAdapter
from app.memory.memguard_http import MemGuardMemoryAdapter
from app.memory.nanomemory_http import NanoMemoryHTTPAdapter

BACKENDS = ("memguard_http", "nanomemory_http", "local")


def create_memory_adapter(settings: Settings):
    backend = settings.memory_backend.lower()
    if backend == "local":
        return LocalFileMemoryAdapter()
    if backend == "memguard_http":
        return MemGuardMemoryAdapter(settings)
    if backend == "nanomemory_http":
        return NanoMemoryHTTPAdapter(settings)
    raise ValueError(f"Unsupported MEMORY_BACKEND: {settings.memory_backend}")
