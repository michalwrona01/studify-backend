import hashlib
import secrets


PASSWORD_ITERATIONS = 600_000
MAX_PASSWORD_LENGTH = 1024


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError("Hasło musi mieć od 12 do 1024 znaków.")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
        return secrets.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


# Unknown usernames still perform the same password verification work.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
