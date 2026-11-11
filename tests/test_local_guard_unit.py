from types import SimpleNamespace

from app.governance.local_guard import LocalPolicyGuard


def test_memory_without_provenance_is_quarantined():
    guard = LocalPolicyGuard()
    decision = guard.validate_memory(
        SimpleNamespace(state="ACTIVE", source="unknown", trust_level="unknown")
    )
    assert not decision.allowed
    assert decision.decision == "quarantine"
    assert decision.reason_code == "memory_provenance_missing"


def test_quarantined_memory_is_not_accepted():
    guard = LocalPolicyGuard()
    decision = guard.validate_memory(
        SimpleNamespace(state="QUARANTINED", source="user", trust_level="user_asserted")
    )
    assert not decision.allowed
    assert decision.reason_code == "memory_state_not_active"


def test_normal_input_is_allowed():
    guard = LocalPolicyGuard()
    assert guard.evaluate_input("请帮我查询订单物流").allowed


def test_abusive_language_is_intercepted():
    guard = LocalPolicyGuard()
    decision = guard.evaluate_input("你这个垃圾AI")
    assert not decision.allowed
    assert decision.reason_code == "abusive_language"


def test_contradictory_refund_intent_is_intercepted():
    guard = LocalPolicyGuard()
    decision = guard.evaluate_input("我不要退款，但是请帮我申请退款")
    assert not decision.allowed
    assert decision.reason_code == "contradictory_refund_intent"
