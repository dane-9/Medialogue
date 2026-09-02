"""Password and opaque session-token helpers.

The application deliberately uses server-side sessions rather than JWTs.  A
session cookie contains only a random opaque value; the database stores its
digest and the CSRF digest separately.
"""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.domain import ConfirmationNonce


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(48)


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_confirmation_payload(payload: dict[str, object], secret_key: str) -> str:
    """Create a compact HMAC-signed JSON token for short-lived confirmations."""
    import json

    signed_payload = dict(payload)
    signed_payload["jti"] = secrets.token_urlsafe(16)
    body = json.dumps(signed_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    signature = hmac.new(secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    sig_text = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded}.{sig_text}"


def verify_confirmation_payload(token: str, secret_key: str) -> dict[str, object] | None:
    """Verify an HMAC-signed confirmation token and return its JSON payload."""
    import json

    try:
        encoded, sig_text = token.split(".", 1)
        expected = hmac.new(secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
        expected_text = base64.urlsafe_b64encode(expected).decode("ascii").rstrip("=")
        # Compare the canonical encoded form.  Decoding first would accept
        # alternate final Base64 characters whose unused bits differ, which
        # makes a visibly tampered token verify as the same signature.
        if not hmac.compare_digest(expected_text, sig_text):
            return None
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


async def consume_confirmation_jti(db: AsyncSession, payload: Mapping[str, object]) -> None:
    """Atomically consume a destructive confirmation token ID.

    The unique database constraint is the cross-request/process guard. A
    nested transaction turns a duplicate insert into a controlled application
    error without rolling back the caller's surrounding work.
    """

    try:
        jti = str(payload["jti"])
        purpose = str(payload["purpose"])
        expiry = int(payload["exp"])
        if not jti or len(jti) > 128 or not purpose or len(purpose) > 64:
            raise ValueError
        expires_at = datetime.fromtimestamp(expiry, tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise AppError("INVALID_CONFIRMATION", "Confirmation token is missing a valid ID.", status_code=409) from exc

    try:
        async with db.begin_nested():
            db.add(ConfirmationNonce(jti=jti, purpose=purpose, expires_at=expires_at))
            await db.flush()
    except IntegrityError as exc:
        raise AppError("CONFIRMATION_REPLAY", "This confirmation token has already been used.", status_code=409) from exc
