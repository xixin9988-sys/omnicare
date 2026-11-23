from app.core.config import Settings


def create_governance_adapter(settings: Settings):
    """Select the governance backend.

    ``local``    - deterministic demo policy, no external dependency.
    ``memguard`` - the real mem-guard SDK (rules + trust vocabulary); business checks that
                   need the database remain in LocalPolicyGuard and are composed inside the
                   adapter.
    """
    backend = settings.governance_backend.lower()
    if backend == "local":
        from app.governance.local_guard import LocalPolicyGuard

        # The same high-value rule the 60-second demo exercises also protects the chat path in
        # demo environments; production keeps the historical behavior unless DEMO_MODE=true.
        threshold = settings.demo_high_value_threshold if settings.demo_enabled else None
        return LocalPolicyGuard(settings.mem_guard_policy_version, threshold)
    if backend == "memguard":
        from app.governance.memguard_adapter import MemGuardAdapter

        return MemGuardAdapter(settings.mem_guard_policy_version)
    raise ValueError(
        f"Unsupported GOVERNANCE_BACKEND: {settings.governance_backend} "
        "(supported: local, memguard)"
    )
