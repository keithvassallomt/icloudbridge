"""Bitwarden/Vaultwarden encryption helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
from dataclasses import dataclass
from enum import IntEnum
from secrets import token_bytes
from typing import Iterable

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


class EncryptionType(IntEnum):
    """Subset of Bitwarden encryption type identifiers we need."""

    AES_CBC_256_B64 = 0
    AES_CBC_256_HMAC_SHA256_B64 = 2


@dataclass
class CipherComponents:
    enc_type: EncryptionType
    iv: bytes | None
    data: bytes
    mac: bytes | None = None

    def encode(self) -> str:
        pieces: list[str] = []
        if self.iv is not None:
            pieces.append(_b64e(self.iv))
        pieces.append(_b64e(self.data))
        if self.mac is not None:
            pieces.append(_b64e(self.mac))
        payload = "|".join(pieces)
        return f"{int(self.enc_type)}.{payload}"

    @staticmethod
    def parse(cipher_string: str) -> "CipherComponents":
        if not cipher_string:
            raise ValueError("Cipher string is empty")
        try:
            header, payload = cipher_string.split(".", 1)
            enc_type = EncryptionType(int(header))
        except ValueError as exc:  # pragma: no cover - guardrail
            raise ValueError("Invalid cipher string header") from exc
        parts = payload.split("|")
        if enc_type == EncryptionType.AES_CBC_256_B64:
            if len(parts) != 2:
                raise ValueError("Invalid AES payload")
            iv = _b64d(parts[0])
            data = _b64d(parts[1])
            return CipherComponents(enc_type, iv=iv, data=data)
        if enc_type == EncryptionType.AES_CBC_256_HMAC_SHA256_B64:
            if len(parts) != 3:
                raise ValueError("Invalid AES-HMAC payload")
            iv = _b64d(parts[0])
            data = _b64d(parts[1])
            mac = _b64d(parts[2])
            return CipherComponents(enc_type, iv=iv, data=data, mac=mac)
        raise ValueError(f"Unsupported encryption type: {enc_type}")


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def _b64d(value: str) -> bytes:
    return base64.b64decode(value.encode("utf-8"))


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    hash_len = hashlib.sha256().digest_size
    if len(prk) < hash_len:
        raise ValueError("PRK is too short for HKDF expand")
    n = math.ceil(length / hash_len)
    okm = b""
    previous = b""
    for counter in range(1, n + 1):
        data = previous + info + bytes([counter])
        previous = hmac.new(prk, data, hashlib.sha256).digest()
        okm += previous
    return okm[:length]


def stretch_key(key: bytes) -> bytes:
    """Derive enc/mac slices from a 32-byte key."""
    enc = _hkdf_expand(key, b"enc", 32)
    mac = _hkdf_expand(key, b"mac", 32)
    return enc + mac


def ensure_stretched(key: bytes) -> bytes:
    if len(key) >= 64:
        return key
    if len(key) != 32:
        raise ValueError("Unexpected key length; expected 32 or 64 bytes")
    return stretch_key(key)


def _aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def decrypt_cipher_string(cipher_string: str, key: bytes) -> bytes:
    components = CipherComponents.parse(cipher_string)
    if components.enc_type == EncryptionType.AES_CBC_256_B64:
        if len(key) != 32:
            raise ValueError("Expected 32-byte key for AES256 decrypt")
        assert components.iv is not None
        return _aes_cbc_decrypt(key, components.iv, components.data)
    if components.enc_type == EncryptionType.AES_CBC_256_HMAC_SHA256_B64:
        stretched = ensure_stretched(key)
        enc_key = stretched[:32]
        mac_key = stretched[32:]
        assert components.mac is not None and components.iv is not None
        mac_check = hmac.new(mac_key, components.iv + components.data, hashlib.sha256).digest()
        if not hmac.compare_digest(mac_check, components.mac):
            raise ValueError("CipherString MAC validation failed")
        return _aes_cbc_decrypt(enc_key, components.iv, components.data)
    raise ValueError("Unsupported encryption type")


def encrypt_string(value: str, key: bytes, *, use_mac: bool = True) -> str:
    if value is None:
        return None  # caller should drop null values
    return encrypt_bytes(value.encode("utf-8"), key, use_mac=use_mac)


def encrypt_bytes(plaintext: bytes, key: bytes, *, use_mac: bool = True) -> str:
    """Encrypt raw bytes (e.g. an item's own key) into a Bitwarden cipher string."""
    iv = token_bytes(16)
    if use_mac:
        stretched = ensure_stretched(key)
        enc_key = stretched[:32]
        mac_key = stretched[32:]
        ciphertext = _aes_cbc_encrypt(enc_key, iv, plaintext)
        mac = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
        components = CipherComponents(
            enc_type=EncryptionType.AES_CBC_256_HMAC_SHA256_B64,
            iv=iv,
            data=ciphertext,
            mac=mac,
        )
        return components.encode()
    if len(key) != 32:
        raise ValueError("Expected 32-byte key for AES256 encrypt")
    ciphertext = _aes_cbc_encrypt(key, iv, plaintext)
    components = CipherComponents(
        enc_type=EncryptionType.AES_CBC_256_B64,
        iv=iv,
        data=ciphertext,
    )
    return components.encode()


def encrypt_optional_list(values: Iterable[str] | None, key: bytes) -> list[dict[str, str]] | None:
    if not values:
        return None
    uris = []
    for value in values:
        if value:
            uris.append({"uri": encrypt_string(value, key), "match": None})
    return uris or None
