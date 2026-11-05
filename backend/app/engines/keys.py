"""Development key hierarchy standing in for regional KMS/HSM (ZA-EVD-001 §9).

Purpose-separated keys (EVD-REQ-012):
  * master  -> HKDF -> per-tenant KEK  (wraps per-evidence DEKs; AES-256-GCM)
  * master  -> HKDF -> per-tenant Evidence Fingerprint Key (HMAC-SHA-256 only)
  * Ed25519 cell signing key (checkpoints, packages, content releases) — never encrypts.
Production replaces this module with KMS/HSM calls; key material never leaves it.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from functools import lru_cache

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..db import DATA_DIR

KEY_DIR = DATA_DIR / "keys"
KEY_DIR.mkdir(parents=True, exist_ok=True)
FINGERPRINT_KEY_VERSION = "efk-v1"
KEK_VERSION = "kek-v1"


@lru_cache
def _master() -> bytes:
    p = KEY_DIR / "master.key"
    if not p.exists():
        p.write_bytes(os.urandom(32))
    return p.read_bytes()


def _derive(label: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"zoiko-assure-kms-dev", info=label.encode()).derive(_master())


def tenant_kek(tenant_id: str) -> bytes:
    return _derive(f"KEK|{tenant_id}|{KEK_VERSION}")


def tenant_fingerprint_key(tenant_id: str, version: str = FINGERPRINT_KEY_VERSION) -> bytes:
    return _derive(f"EFK|{tenant_id}|{version}")


def hmac_plaintext(tenant_id: str, data: bytes, version: str = FINGERPRINT_KEY_VERSION) -> str:
    return hmac.new(tenant_fingerprint_key(tenant_id, version), data, hashlib.sha256).hexdigest()


def encrypt_payload(tenant_id: str, evidence_id: str, plaintext: bytes) -> tuple[bytes, bytes]:
    """Envelope encryption with a unique per-evidence DEK and bound associated data."""
    dek = AESGCM.generate_key(bit_length=256)
    aad = f"{tenant_id}|{evidence_id}|enc-v1".encode()
    nonce = os.urandom(12)
    ciphertext = nonce + AESGCM(dek).encrypt(nonce, plaintext, aad)
    wnonce = os.urandom(12)
    wrapped = wnonce + AESGCM(tenant_kek(tenant_id)).encrypt(wnonce, dek, f"DEK|{evidence_id}".encode())
    return ciphertext, wrapped


def decrypt_payload(tenant_id: str, evidence_id: str, ciphertext: bytes, wrapped: bytes) -> bytes:
    dek = AESGCM(tenant_kek(tenant_id)).decrypt(wrapped[:12], wrapped[12:], f"DEK|{evidence_id}".encode())
    aad = f"{tenant_id}|{evidence_id}|enc-v1".encode()
    return AESGCM(dek).decrypt(ciphertext[:12], ciphertext[12:], aad)


SIGNING_KEY_FILE = KEY_DIR / "cell-signing-ed25519.pem"


@lru_cache
def signing_key() -> Ed25519PrivateKey:
    p = SIGNING_KEY_FILE
    if not p.exists():
        k = Ed25519PrivateKey.generate()
        p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    return serialization.load_pem_private_key(p.read_bytes(), password=None)


def public_key_pem() -> str:
    return signing_key().public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def key_id_of(pub: Ed25519PublicKey) -> str:
    """The key identifier releases record: algorithm prefix + truncated SHA-256 of the raw public key."""
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "ed25519:" + hashlib.sha256(raw).hexdigest()[:16]


def signer_key_id() -> str:
    return key_id_of(signing_key().public_key())


# ---------------------------------------------------------------- read-only access for verification (A9b)
# Verification must never create, replace or expose a private key: these read an existing key only.
def existing_public_key() -> Ed25519PublicKey | None:
    """The cell signing key's public part if a key already exists; None otherwise (no key is generated)."""
    if not SIGNING_KEY_FILE.exists():
        return None
    return signing_key().public_key()


def public_pem_of(pub: Ed25519PublicKey) -> str:
    return pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def verify_with(pub: Ed25519PublicKey, data: bytes, signature: bytes) -> bool:
    try:
        pub.verify(signature, data)
        return True
    except Exception:  # noqa: BLE001 - any failure is "not verified"; the caller reports INVALID
        return False


def sign(data: bytes) -> str:
    return signing_key().sign(data).hex()


def verify(data: bytes, signature_hex: str, public_pem: str | None = None) -> bool:
    pub: Ed25519PublicKey = (serialization.load_pem_public_key(public_pem.encode()) if public_pem
                             else signing_key().public_key())
    try:
        pub.verify(bytes.fromhex(signature_hex), data)
        return True
    except Exception:
        return False
