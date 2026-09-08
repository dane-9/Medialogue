"""Portable, streaming authenticated backup encryption, independent of app secrets."""
from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"MEDIALOGUE-BACKUP-V1\n"
CHUNK = 1024 * 1024


def new_recovery_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def decode_key(value: str) -> bytes:
    try:
        key = base64.b64decode(value.strip(), altchars=b"-_", validate=True)
    except Exception as exc:
        raise ValueError("Enter a valid Medialogue recovery key") from exc
    if len(key) != 32:
        raise ValueError("Recovery keys must contain 32 random bytes")
    return key


def encrypt_backup(source: Path, destination: Path, recovery_key: str) -> None:
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(decode_key(recovery_key)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    created = False
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            created = True
            destination.chmod(0o600)
            writer.write(MAGIC + nonce)
            while block := reader.read(CHUNK):
                writer.write(encryptor.update(block))
            writer.write(encryptor.finalize())
            writer.write(encryptor.tag)
            writer.flush()
            os.fsync(writer.fileno())
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def decrypt_backup(source: Path, destination: Path, recovery_key: str) -> None:
    """Publish plaintext only after GCM authentication succeeds."""
    if destination.exists():
        raise ValueError("Recovery destination already exists")
    temporary = destination.with_name(destination.name + ".decrypting")
    created = False
    try:
        with source.open("rb") as reader:
            if reader.read(len(MAGIC)) != MAGIC:
                raise ValueError("Unsupported encrypted backup format")
            nonce = reader.read(12)
            remaining = source.stat().st_size - len(MAGIC) - 12 - 16
            if remaining < 0:
                raise ValueError("Truncated backup")
            reader.seek(-16, 2)
            tag = reader.read(16)
            reader.seek(len(MAGIC) + 12)
            decryptor = Cipher(algorithms.AES(decode_key(recovery_key)), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(MAGIC)
            with temporary.open("xb") as writer:
                created = True
                temporary.chmod(0o600)
                while remaining:
                    block = reader.read(min(CHUNK, remaining))
                    if not block:
                        raise ValueError("Truncated backup")
                    remaining -= len(block)
                    writer.write(decryptor.update(block))
                writer.write(decryptor.finalize())
            os.replace(temporary, destination)
    finally:
        if created:
            temporary.unlink(missing_ok=True)
