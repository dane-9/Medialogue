from app.core.security import digest_token, hash_password, verify_password


def test_password_hash_round_trip() -> None:
    encoded = hash_password("a strong test password")
    assert encoded.startswith("scrypt$")
    assert verify_password("a strong test password", encoded)
    assert not verify_password("wrong password", encoded)


def test_token_digest_is_stable_and_not_plaintext() -> None:
    assert digest_token("secret") == digest_token("secret")
    assert digest_token("secret") != "secret"
