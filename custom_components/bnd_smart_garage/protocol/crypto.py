"""Cryptographic primitives the hub requires.

The control API (port 8989) uses AES-128-CBC with MD5-derived key and IV; the
SDK API (port 8991, pairing only here) uses AES-256-CBC with SHA-256-derived
material, RSA-SHA512 signatures and an ECDH key upgrade. All dictated by the
hub firmware - the weak primitives are not a choice.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


def _cbc_encrypt(key: bytes, iv: bytes, plaintext: str) -> str:
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def _cbc_decrypt(key: bytes, iv: bytes, ciphertext_b64: str) -> str:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(base64.b64decode(ciphertext_b64)) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode()


def _md5(value: str) -> bytes:
    return hashlib.md5(value.encode()).digest()  # noqa: S324 - hub protocol


def encrypt_control(secret: str, iv_seed: str, plaintext: str) -> str:
    """Control API: AES-128-CBC, key=MD5(secret), iv=MD5(iv_seed)."""
    return _cbc_encrypt(_md5(secret), _md5(iv_seed), plaintext)


def decrypt_control(secret: str, iv_seed: str, ciphertext_b64: str) -> str:
    """Inverse of encrypt_control."""
    return _cbc_decrypt(_md5(secret), _md5(iv_seed), ciphertext_b64)


def encrypt_sdk(secret: str, iv_seed: str, plaintext: str) -> str:
    """SDK API: AES-256-CBC, key=SHA256(secret), iv=SHA256(iv_seed)[:16]."""
    key = hashlib.sha256(secret.encode()).digest()
    iv = hashlib.sha256(iv_seed.encode()).digest()[:16]
    return _cbc_encrypt(key, iv, plaintext)


def decrypt_sdk_reply(secret: str, ciphertext_b64: str) -> str:
    """Decrypt a hub-originated SDK reply, which never tells us its IV.

    Decrypting with a zero IV garbles only the first 16-byte block (CBC
    chains every later block off the previous *ciphertext*), so the first
    block is dropped and the caller gets the intact remainder - enough to
    regex out the fields pairing needs.
    """
    key = hashlib.sha256(secret.encode()).digest()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).decryptor()
    plaintext = decryptor.update(base64.b64decode(ciphertext_b64)) + decryptor.finalize()
    pad = plaintext[-1] if plaintext else 0
    if 1 <= pad <= 16:
        plaintext = plaintext[:-pad]
    return plaintext[16:].decode("utf-8", errors="replace")


def sign_hmac(key: str, message: str) -> str:
    """base64(HMAC-SHA256(key, message))."""
    return base64.b64encode(
        hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()


def sign_rsa(private_key_der_b64: str, message: str) -> str:
    """base64(RSA-SHA512-PKCS1v15) with a base64 PKCS8 DER private key."""
    private_key = serialization.load_der_private_key(
        base64.b64decode(private_key_der_b64), password=None
    )
    return base64.b64encode(
        private_key.sign(message.encode(), rsa_padding.PKCS1v15(), hashes.SHA512())
    ).decode()
