"""
ChainPay Security Module — Fixed
==================================
FIX LOG:
  - VoucherSystem: sign_transaction uses private key (HMAC); redeem_voucher
    was passing PUBLIC key to verify_transaction_signature.
    HMAC is symmetric — must verify with the SAME key used to sign.
    Fixed: store signing key reference in voucher and verify with private key,
    OR switch to shared-secret model where sender's private key is the HMAC secret
    and the recipient verifies using the sender's public key derived value.
    PRACTICAL FIX: Voucher now stores sender_id; verifier must know the signing key.
    For hackathon demo: verify_voucher accepts private_key directly.

  - SessionManager: no changes needed (JWT HS256 symmetric is correct).
  - AESCipher: no changes needed (GCM authenticated encryption correct).
  - generate_keypair: no changes needed.
"""

import hashlib
import hmac
import secrets
import base64
import json
import time
import os
from typing import Optional, Tuple, Dict
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import jwt


AES_KEY_SIZE       = 32
GCM_NONCE_SIZE     = 12
PBKDF2_ITERATIONS  = 100_000
JWT_ALGORITHM      = "HS256"
JWT_EXPIRY_SECONDS = 3600
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS     = 300


# ─── Key Derivation ───────────────────────────────────────────────────────────

def derive_key_from_password(password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256, 100k iterations. ~100ms computation time."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=salt,
        iterations=PBKDF2_ITERATIONS
    )
    return kdf.derive(password.encode())


def hash_password(password: str) -> str:
    """Returns 'salt_hex:key_hex' for storage. Random 32-byte salt per hash."""
    salt = secrets.token_bytes(32)
    key  = derive_key_from_password(password, salt)
    return f"{salt.hex()}:{key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time comparison. Resistant to timing attacks."""
    try:
        salt_hex, key_hex = stored_hash.split(":")
        salt     = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        derived  = derive_key_from_password(password, salt)
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


# ─── AES-256-GCM ─────────────────────────────────────────────────────────────

class AESCipher:
    """
    AES-256-GCM authenticated encryption.
    Each encryption uses a fresh 96-bit random nonce.
    GCM tag provides integrity — tampering raises an exception on decrypt.
    """

    def __init__(self, key: Optional[bytes] = None):
        if key is None:
            key = secrets.token_bytes(AES_KEY_SIZE)
        if len(key) != AES_KEY_SIZE:
            raise ValueError(f"Key must be {AES_KEY_SIZE} bytes")
        self._key    = key
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        nonce      = secrets.token_bytes(GCM_NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ciphertext).decode()

    def decrypt(self, token: str) -> str:
        combined   = base64.b64decode(token.encode())
        nonce      = combined[:GCM_NONCE_SIZE]
        ciphertext = combined[GCM_NONCE_SIZE:]
        return self._aesgcm.decrypt(nonce, ciphertext, None).decode()

    @property
    def key_b64(self) -> str:
        return base64.b64encode(self._key).decode()

    @classmethod
    def from_key_b64(cls, key_b64: str) -> "AESCipher":
        return cls(key=base64.b64decode(key_b64.encode()))


# ─── HMAC Authentication ──────────────────────────────────────────────────────

def generate_hmac(data: str, secret: str) -> str:
    """HMAC-SHA3-256 message authentication code."""
    return hmac.new(
        secret.encode(),
        data.encode(),
        hashlib.sha3_256
    ).hexdigest()


def verify_hmac(data: str, signature: str, secret: str) -> bool:
    """Constant-time HMAC verification."""
    expected = generate_hmac(data, secret)
    return hmac.compare_digest(expected, signature)


# ─── Transaction Signing ──────────────────────────────────────────────────────

def sign_transaction(payload: str, private_key: str) -> str:
    """HMAC-SHA3-256 transaction signature using private key."""
    return generate_hmac(payload, private_key)


def verify_transaction_signature(payload: str, signature: str, key: str) -> bool:
    """
    Verify a transaction signature.
    NOTE: HMAC is symmetric — must use the SAME key that was used to sign.
    For user transactions: pass private_key (only sender knows it).
    """
    return verify_hmac(payload, signature, key)


def generate_keypair() -> Tuple[str, str]:
    """
    Generate a simulated keypair (private_key_hex, public_key_hex).
    In production: use ECC (secp256k1 or Ed25519) via cryptography library.
    For demo: public_key is SHA3-256(private_key) — deterministically derived.
    """
    private_key = secrets.token_hex(32)
    public_key  = hashlib.sha3_256(private_key.encode()).hexdigest()
    return private_key, public_key


# ─── JWT Session Manager ──────────────────────────────────────────────────────

class SessionManager:
    """
    Stateless JWT session management with revocation list.
    Tokens: HS256, 1 hour expiry.
    In-memory revocation (lost on restart — acceptable for demo).
    """

    def __init__(self, secret_key: Optional[str] = None):
        self._secret         = secret_key or secrets.token_hex(32)
        self._revoked: set   = set()
        self._rate_limits:   Dict[str, list] = {}
        self._failed_attempts: Dict[str, Tuple[int, float]] = {}

    def create_token(self, user_id: str, phone: str, role: str = "user") -> str:
        payload = {
            "sub":   user_id,
            "phone": phone,
            "role":  role,
            "iat":   time.time(),
            "exp":   time.time() + JWT_EXPIRY_SECONDS,
            "jti":   secrets.token_hex(16),
        }
        return jwt.encode(payload, self._secret, algorithm=JWT_ALGORITHM)

    def verify_token(self, token: str) -> Optional[dict]:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[JWT_ALGORITHM])
            if payload.get("jti") in self._revoked:
                return None
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def revoke_token(self, token: str):
        try:
            payload = jwt.decode(token, self._secret, algorithms=[JWT_ALGORITHM],
                                 options={"verify_exp": False})
            self._revoked.add(payload.get("jti"))
        except Exception:
            pass

    def check_rate_limit(self, user_id: str, window: int = 60,
                          max_requests: int = 30) -> bool:
        """Sliding window rate limiter. Returns True if request is allowed."""
        now     = time.time()
        history = self._rate_limits.get(user_id, [])
        history = [t for t in history if now - t < window]
        if len(history) >= max_requests:
            return False
        history.append(now)
        self._rate_limits[user_id] = history
        return True

    def record_failed_attempt(self, user_id: str) -> bool:
        """Returns True if account should be locked out."""
        count, first_time = self._failed_attempts.get(user_id, (0, time.time()))
        if time.time() - first_time > LOCKOUT_SECONDS:
            count      = 0
            first_time = time.time()
        count += 1
        self._failed_attempts[user_id] = (count, first_time)
        return count >= MAX_FAILED_ATTEMPTS

    def is_locked_out(self, user_id: str) -> bool:
        count, first_time = self._failed_attempts.get(user_id, (0, 0))
        if time.time() - first_time > LOCKOUT_SECONDS:
            return False
        return count >= MAX_FAILED_ATTEMPTS

    def clear_failed_attempts(self, user_id: str):
        self._failed_attempts.pop(user_id, None)


# ─── Voucher System (Offline Payments) ────────────────────────────────────────

class VoucherSystem:
    """
    Pre-signed offline payment vouchers.

    FIX: Previously signed with private_key but verified with public_key.
    HMAC is symmetric — both operations must use the same key.

    CORRECTED DESIGN:
      - Voucher is signed with sender's private_key (HMAC secret).
      - Redemption verifies with sender's private_key.
      - In a real system: sender hands voucher + key-commitment to recipient
        via NFC/BLE; recipient submits both to server for online redemption.
      - For demo: redeem_voucher accepts the private_key directly.

    Anti-double-spend: serial number tracked in _redeemed set (persistent DB in production).
    Expiry: 24 hours.
    """

    EXPIRY_SECONDS = 86400
    _redeemed: set = set()

    @staticmethod
    def create_voucher(sender_id: str, amount: float, currency: str,
                       private_key: str) -> dict:
        """Create a signed offline payment voucher."""
        serial  = secrets.token_hex(16)
        voucher = {
            "serial":    serial,
            "sender_id": sender_id,
            "amount":    amount,
            "currency":  currency,
            "created_at":  time.time(),
            "expires_at":  time.time() + VoucherSystem.EXPIRY_SECONDS,
        }
        payload             = json.dumps(voucher, sort_keys=True)
        voucher["signature"] = generate_hmac(payload, private_key)
        return voucher

    @classmethod
    def redeem_voucher(cls, voucher: dict,
                       private_key: str) -> Tuple[bool, str]:
        """
        Validate and redeem a voucher.
        FIX: accepts private_key (same key used to sign) not public_key.

        Anti-double-spend: serial checked against _redeemed set.
        """
        import copy
        voucher = copy.deepcopy(voucher)  # Don't mutate caller's dict
        serial  = voucher.get("serial")

        if serial in cls._redeemed:
            return False, "Voucher already redeemed (double-spend prevented)"

        if time.time() > voucher.get("expires_at", 0):
            return False, "Voucher expired"

        # Extract and verify signature
        signature = voucher.pop("signature", "")
        payload   = json.dumps(voucher, sort_keys=True)

        if not verify_hmac(payload, signature, private_key):
            return False, "Invalid voucher signature"

        cls._redeemed.add(serial)
        return True, "Voucher redeemed successfully"


# ─── Singleton ────────────────────────────────────────────────────────────────

_session_manager: Optional[SessionManager] = None

def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager