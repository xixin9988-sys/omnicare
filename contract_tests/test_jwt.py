"""JWT/OIDC auth core: issuing, verification, tenants and revocation.

Run without the database stack so the security contract is checkable on its own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.core import jwt as jwt_module
from app.core.config import Settings
from app.core.jwt import JwtError, issue_jwt, verify_jwt

SECRET = "s" * 64
NOW = 1_700_000_000


def settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "local",
        "auth_mode": "jwt",
        "auth_secret": SECRET,
        "auth_jwt_algorithm": "HS256",
        "auth_jwt_issuer": "omnicare",
        "auth_jwt_audience": "omnicare-api",
        "auth_jwt_leeway_seconds": 0,
    }
    base.update(overrides)
    return Settings(**base)


def claims(**overrides) -> dict:
    base = {
        "sub": "alice",
        "iss": "omnicare",
        "aud": "omnicare-api",
        "role": "user",
        "tenant_id": "tenant-a",
        "iat": NOW,
        "exp": NOW + 3600,
        "jti": "token-1",
    }
    base.update(overrides)
    return base


def encode(claims_data: dict, *, key: str, algorithm: str) -> str:
    return str(pyjwt.encode(claims_data, key, algorithm=algorithm))


def segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def unsigned_token(claims_data: dict) -> str:
    header = segment(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8"))
    payload = segment(json.dumps(claims_data).encode("utf-8"))
    return f"{header}.{payload}."


def hand_signed_hs256_token(claims_data: dict, *, secret: bytes) -> str:
    """Build an HS256 token without PyJWT's asymmetric-key guard (attack simulation)."""
    header = segment(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload = segment(json.dumps(claims_data).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{segment(signature)}"


@pytest.fixture(scope="module")
def rsa_keys() -> tuple[str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@pytest.fixture(scope="module")
def ec_keys() -> tuple[str, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


# ------------------------------------------------------------------ HS* issuing
def test_hs256_round_trip_carries_the_full_identity() -> None:
    cfg = settings()
    token = issue_jwt(
        "alice",
        settings=cfg,
        role="admin",
        tenant_id="tenant-a",
        scopes=("omnicare.read", "products:read"),
        jti="jti-1",
        now=NOW,
    )
    verified = verify_jwt(token, settings=cfg, now=NOW + 1)
    assert verified.subject == "alice"
    assert verified.issuer == "omnicare"
    assert verified.audience == "omnicare-api"
    assert verified.role == "admin"
    assert verified.tenant_id == "tenant-a"
    assert verified.scopes == ("omnicare.read", "products:read")
    assert verified.jti == "jti-1"
    assert verified.expires_at == datetime.fromtimestamp(NOW + 3600, tz=UTC)
    assert verified.claims["sub"] == "alice"


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_hmac_algorithms_round_trip(algorithm: str) -> None:
    cfg = settings(auth_jwt_algorithm=algorithm)
    token = issue_jwt("alice", settings=cfg, now=NOW)
    header = pyjwt.get_unverified_header(token)
    assert header["alg"] == algorithm
    assert verify_jwt(token, settings=cfg, now=NOW + 1).subject == "alice"


def test_issue_jwt_generates_unique_jti_and_uses_configured_ttl() -> None:
    cfg = settings(auth_token_ttl_seconds=120)
    first = issue_jwt("alice", settings=cfg, now=NOW)
    second = issue_jwt("alice", settings=cfg, now=NOW)
    first_claims = verify_jwt(first, settings=cfg, now=NOW + 1)
    second_claims = verify_jwt(second, settings=cfg, now=NOW + 1)
    assert first_claims.jti and second_claims.jti
    assert first_claims.jti != second_claims.jti
    exp = int(first_claims.claims["exp"])
    assert exp == NOW + 120


def test_issue_jwt_defaults_tenant_role_and_scopes() -> None:
    cfg = settings(default_tenant_id="tenant-a")
    verified = verify_jwt(issue_jwt("alice", settings=cfg, now=NOW), settings=cfg, now=NOW + 1)
    assert verified.role == "user"
    assert verified.tenant_id == "tenant-a"
    assert verified.scopes == ()


def test_issue_jwt_refuses_an_empty_subject() -> None:
    with pytest.raises(JwtError) as error:
        issue_jwt("   ", settings=settings(), now=NOW)
    assert error.value.code == "missing_subject"


def test_issue_jwt_requires_a_configured_hmac_secret() -> None:
    with pytest.raises(JwtError) as error:
        issue_jwt("alice", settings=settings(auth_secret=""), now=NOW)
    assert error.value.code == "jwt_not_configured"


def test_issue_jwt_refuses_to_mint_asymmetric_tokens() -> None:
    cfg = settings(auth_jwt_algorithm="RS256", auth_jwt_public_key="ignored")
    with pytest.raises(JwtError) as error:
        issue_jwt("alice", settings=cfg, now=NOW)
    assert error.value.code == "issuing_not_supported"


# ------------------------------------------------------------------ signature integrity
def test_tampered_payload_and_signature_are_rejected() -> None:
    cfg = settings()
    token = issue_jwt("alice", settings=cfg, now=NOW)
    header, payload, signature = token.split(".")

    with pytest.raises(JwtError) as error:
        verify_jwt(f"{header}.{payload}x.{signature}", settings=cfg, now=NOW + 1)
    assert error.value.code == "bad_signature"

    with pytest.raises(JwtError):
        verify_jwt(f"{header}.{payload}.{signature[:-2]}aa", settings=cfg, now=NOW + 1)


def test_token_signed_with_another_secret_is_rejected() -> None:
    token = issue_jwt("alice", settings=settings(), now=NOW)
    with pytest.raises(JwtError) as error:
        verify_jwt(token, settings=settings(auth_secret="o" * 40), now=NOW + 1)
    assert error.value.code == "bad_signature"


def test_alg_none_is_refused() -> None:
    token = unsigned_token(claims())
    with pytest.raises(JwtError) as error:
        verify_jwt(token, settings=settings(), now=NOW + 1)
    assert error.value.code == "unsigned_token"


def test_algorithm_confusion_is_refused(rsa_keys: tuple[str, str]) -> None:
    private_pem, public_pem = rsa_keys
    rsa_cfg = settings(auth_jwt_algorithm="RS256", auth_jwt_public_key=public_pem)
    # HS256 token signed with the public key as if it were an HMAC secret must never verify.
    confused = hand_signed_hs256_token(claims(), secret=public_pem.encode("utf-8"))
    with pytest.raises(JwtError) as error:
        verify_jwt(confused, settings=rsa_cfg, now=NOW + 1)
    assert error.value.code == "unsupported_algorithm"

    # An RS256 token must not verify when the deployment is pinned to HS256.
    rsa_token = encode(claims(), key=private_pem, algorithm="RS256")
    with pytest.raises(JwtError) as error:
        verify_jwt(rsa_token, settings=settings(), now=NOW + 1)
    assert error.value.code == "unsupported_algorithm"


def test_blank_configured_algorithm_falls_back_to_hs256() -> None:
    cfg = settings(auth_jwt_algorithm="")
    token = issue_jwt("alice", settings=cfg, now=NOW)
    assert pyjwt.get_unverified_header(token)["alg"] == "HS256"
    assert verify_jwt(token, settings=cfg, now=NOW + 1).subject == "alice"


def test_unknown_configured_algorithm_is_refused() -> None:
    cfg = settings(auth_jwt_algorithm="HS1024")
    with pytest.raises(JwtError) as error:
        verify_jwt(issue_jwt("alice", settings=settings(), now=NOW), settings=cfg, now=NOW + 1)
    assert error.value.code == "unsupported_algorithm"


# ------------------------------------------------------------------ time checks
def test_expired_token_is_rejected() -> None:
    cfg = settings()
    token = issue_jwt("alice", settings=cfg, ttl_seconds=10, now=NOW)
    assert verify_jwt(token, settings=cfg, now=NOW + 10).subject == "alice"
    with pytest.raises(JwtError) as error:
        verify_jwt(token, settings=cfg, now=NOW + 11)
    assert error.value.code == "expired_token"


def test_expiration_honours_the_configured_leeway() -> None:
    cfg = settings(auth_jwt_leeway_seconds=30)
    token = issue_jwt("alice", settings=cfg, ttl_seconds=10, now=NOW)
    assert verify_jwt(token, settings=cfg, now=NOW + 35).subject == "alice"
    with pytest.raises(JwtError):
        verify_jwt(token, settings=cfg, now=NOW + 45)


def test_not_yet_valid_token_is_rejected() -> None:
    cfg = settings()
    token = encode(claims(nbf=NOW + 60), key=SECRET, algorithm="HS256")
    with pytest.raises(JwtError) as error:
        verify_jwt(token, settings=cfg, now=NOW)
    assert error.value.code == "token_not_yet_valid"
    lenient = settings(auth_jwt_leeway_seconds=60)
    assert verify_jwt(token, settings=lenient, now=NOW).subject == "alice"


def test_token_without_exp_is_rejected() -> None:
    payload = claims()
    payload.pop("exp")
    token = encode(payload, key=SECRET, algorithm="HS256")
    with pytest.raises(JwtError) as error:
        verify_jwt(token, settings=settings(), now=NOW + 1)
    assert error.value.code == "missing_claim"


# ------------------------------------------------------------------ iss / aud / sub
def test_wrong_issuer_is_rejected() -> None:
    cfg = settings(auth_jwt_issuer="omnicare")
    token = encode(claims(iss="evil"), key=SECRET, algorithm="HS256")
    with pytest.raises(JwtError) as error:
        verify_jwt(token, settings=cfg, now=NOW + 1)
    assert error.value.code == "wrong_issuer"


def test_wrong_audience_is_rejected() -> None:
    cfg = settings(auth_jwt_audience="omnicare-api")
    token = encode(claims(aud="another-api"), key=SECRET, algorithm="HS256")
    with pytest.raises(JwtError) as error:
        verify_jwt(token, settings=cfg, now=NOW + 1)
    assert error.value.code == "wrong_audience"


def test_audience_list_is_matched_and_normalised() -> None:
    cfg = settings(auth_jwt_audience="omnicare-api")
    token = encode(claims(aud=["other-api", "omnicare-api"]), key=SECRET, algorithm="HS256")
    verified = verify_jwt(token, settings=cfg, now=NOW + 1)
    assert verified.audience == "omnicare-api"


def test_missing_issuer_or_audience_claim_is_rejected_when_configured() -> None:
    cfg = settings(auth_jwt_issuer="omnicare")
    payload = claims()
    payload.pop("iss")
    token = encode(payload, key=SECRET, algorithm="HS256")
    with pytest.raises(JwtError):
        verify_jwt(token, settings=cfg, now=NOW + 1)


def test_empty_or_missing_subject_is_rejected() -> None:
    for subject in ("", "   "):
        token = encode(claims(sub=subject), key=SECRET, algorithm="HS256")
        with pytest.raises(JwtError) as error:
            verify_jwt(token, settings=settings(), now=NOW + 1)
        assert error.value.code == "missing_subject"


def test_custom_claims_are_preserved() -> None:
    token = encode(claims(department="sales"), key=SECRET, algorithm="HS256")
    verified = verify_jwt(token, settings=settings(), now=NOW + 1)
    assert verified.claims["department"] == "sales"


# ------------------------------------------------------------------ tenants / revocation
def test_default_tenant_is_used_when_the_claim_is_missing() -> None:
    cfg = settings(default_tenant_id="tenant-a")
    payload = claims()
    payload.pop("tenant_id")
    token = encode(payload, key=SECRET, algorithm="HS256")
    assert verify_jwt(token, settings=cfg, now=NOW + 1).tenant_id == "tenant-a"


def test_legacy_tenant_claim_is_accepted() -> None:
    cfg = settings()
    payload = claims()
    payload.pop("tenant_id")
    payload["tenant"] = "tenant-b"
    token = encode(payload, key=SECRET, algorithm="HS256")
    assert verify_jwt(token, settings=cfg, now=NOW + 1).tenant_id == "tenant-b"


def test_disallowed_tenant_is_rejected() -> None:
    cfg = settings(auth_allowed_tenants="tenant-a, tenant-b")
    allowed = issue_jwt("alice", settings=cfg, tenant_id="tenant-b", now=NOW)
    assert verify_jwt(allowed, settings=cfg, now=NOW + 1).tenant_id == "tenant-b"

    denied = issue_jwt("alice", settings=settings(), tenant_id="tenant-c", now=NOW)
    with pytest.raises(JwtError) as error:
        verify_jwt(denied, settings=cfg, now=NOW + 1)
    assert error.value.code == "tenant_not_allowed"


def test_revoked_jti_is_rejected() -> None:
    cfg = settings(auth_revoked_jtis="revoked-1, revoked-2")
    revoked = issue_jwt("alice", settings=cfg, jti="revoked-1", now=NOW)
    with pytest.raises(JwtError) as error:
        verify_jwt(revoked, settings=cfg, now=NOW + 1)
    assert error.value.code == "token_revoked"

    live = issue_jwt("alice", settings=cfg, jti="still-valid", now=NOW)
    assert verify_jwt(live, settings=cfg, now=NOW + 1).jti == "still-valid"


# ------------------------------------------------------------------ scopes
def test_required_scopes_are_enforced() -> None:
    cfg = settings(auth_allowed_tenants="")
    token = issue_jwt("alice", settings=cfg, scopes=("omnicare.read", "products:read"), now=NOW)
    verified = verify_jwt(token, settings=cfg, now=NOW + 1, required_scopes=("omnicare.read",))
    assert verified.scopes == ("omnicare.read", "products:read")
    assert verify_jwt(token, settings=cfg, now=NOW + 1, required_scopes="omnicare.read")

    with pytest.raises(JwtError) as error:
        verify_jwt(token, settings=cfg, now=NOW + 1, required_scopes=("omnicare.read", "admin"))
    assert error.value.code == "insufficient_scope"


def test_scopes_claim_accepts_the_oidc_scope_string() -> None:
    token = encode(claims(scope="omnicare.read products:read"), key=SECRET, algorithm="HS256")
    verified = verify_jwt(
        token, settings=settings(), now=NOW + 1, required_scopes=("products:read",)
    )
    assert verified.scopes == ("omnicare.read", "products:read")


# ------------------------------------------------------------------ malformed input
@pytest.mark.parametrize("token", ["", "   ", "garbage", "a.b", "a.b.c", "not.a.token"])
def test_malformed_tokens_are_rejected(token: str) -> None:
    with pytest.raises(JwtError):
        verify_jwt(token, settings=settings(), now=NOW + 1)


# ------------------------------------------------------------------ asymmetric tokens
def test_rs256_round_trip_with_inline_public_key(rsa_keys: tuple[str, str]) -> None:
    private_pem, public_pem = rsa_keys
    cfg = settings(auth_jwt_algorithm="RS256", auth_jwt_public_key=public_pem)
    token = encode(claims(), key=private_pem, algorithm="RS256")
    verified = verify_jwt(token, settings=cfg, now=NOW + 1)
    assert verified.subject == "alice"
    assert verified.tenant_id == "tenant-a"


def test_rs256_round_trip_with_public_key_file(rsa_keys: tuple[str, str], tmp_path) -> None:
    private_pem, public_pem = rsa_keys
    key_file = tmp_path / "public.pem"
    key_file.write_text(public_pem, encoding="utf-8")
    cfg = settings(
        auth_jwt_algorithm="RS256",
        auth_jwt_public_key="",
        auth_jwt_public_key_path=str(key_file),
    )
    token = encode(claims(), key=private_pem, algorithm="RS256")
    assert verify_jwt(token, settings=cfg, now=NOW + 1).subject == "alice"


def test_es256_round_trip(ec_keys: tuple[str, str]) -> None:
    private_pem, public_pem = ec_keys
    cfg = settings(auth_jwt_algorithm="ES256", auth_jwt_public_key=public_pem)
    token = encode(claims(), key=private_pem, algorithm="ES256")
    assert verify_jwt(token, settings=cfg, now=NOW + 1).subject == "alice"


def test_asymmetric_mode_without_key_material_is_refused(rsa_keys: tuple[str, str]) -> None:
    private_pem, _ = rsa_keys
    cfg = settings(auth_jwt_algorithm="RS256")
    token = encode(claims(), key=private_pem, algorithm="RS256")
    with pytest.raises(JwtError) as error:
        verify_jwt(token, settings=cfg, now=NOW + 1)
    assert error.value.code == "jwt_not_configured"


def test_jwks_url_uses_a_cached_pyjwkclient(
    monkeypatch: pytest.MonkeyPatch, rsa_keys: tuple[str, str]
) -> None:
    private_pem, public_pem = rsa_keys
    created: list[SimpleNamespace] = []

    class FakeJWKClient:
        def __init__(self, url: str, **_: object) -> None:
            self.url = url
            self.calls = 0
            created.append(self)

        def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
            self.calls += 1
            return SimpleNamespace(key=public_pem)

    monkeypatch.setattr(jwt_module, "PyJWKClient", FakeJWKClient)
    jwt_module.clear_jwks_cache()
    try:
        cfg = settings(
            auth_jwt_algorithm="RS256",
            auth_jwks_url="https://issuer.example/.well-known/jwks.json",
        )
        token = encode(claims(), key=private_pem, algorithm="RS256")
        assert verify_jwt(token, settings=cfg, now=NOW + 1).subject == "alice"
        assert verify_jwt(token, settings=cfg, now=NOW + 2).subject == "alice"
        assert len(created) == 1, "the PyJWKClient must be cached per JWKS URL"
        assert created[0].calls == 2
    finally:
        jwt_module.clear_jwks_cache()


# ------------------------------------------------------------------ cross-module contract
def test_preflight_algorithm_sets_match_the_jwt_module() -> None:
    from app.core import preflight

    assert preflight._JWT_ALGORITHMS == jwt_module.SUPPORTED_ALGORITHMS
    assert preflight.SUPPORTED_AUTH_MODES == {"dev", "token", "jwt", "oidc"}
