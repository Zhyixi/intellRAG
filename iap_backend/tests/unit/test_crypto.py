"""Tests for common/crypto.py — Fernet encrypt/decrypt helpers."""
import pytest

pytestmark = pytest.mark.unit


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "test-key-for-unit-tests!!")
    from importlib import reload
    import configs.config as cfg
    reload(cfg)
    import common.crypto as crypto
    reload(crypto)

    plain = "sk-mysecretapikey12345"
    cipher = crypto.encrypt_secret(plain)
    assert cipher != plain
    assert crypto.decrypt_secret(cipher) == plain


def test_different_plaintexts_produce_different_ciphertexts(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "test-key-for-unit-tests!!")
    from importlib import reload
    import configs.config as cfg
    reload(cfg)
    import common.crypto as crypto
    reload(crypto)

    c1 = crypto.encrypt_secret("key-a")
    c2 = crypto.encrypt_secret("key-b")
    assert c1 != c2


def test_empty_encryption_key_uses_default_dev_key(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    from importlib import reload
    import configs.config as cfg
    reload(cfg)
    import common.crypto as crypto
    reload(crypto)

    plain = "some-secret"
    assert crypto.decrypt_secret(crypto.encrypt_secret(plain)) == plain


def test_fernet_output_is_str():
    import common.crypto as crypto
    result = crypto.encrypt_secret("hello")
    assert isinstance(result, str)


def test_decrypt_wrong_key_raises(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "key-one!!")
    from importlib import reload
    import configs.config as cfg
    reload(cfg)
    import common.crypto as crypto
    reload(crypto)
    cipher = crypto.encrypt_secret("secret")

    monkeypatch.setenv("ENCRYPTION_KEY", "key-two!!")
    reload(cfg)
    reload(crypto)
    with pytest.raises(Exception):
        crypto.decrypt_secret(cipher)
