import hashlib
import secrets

def hash_password(password):
    if len(password) < 12:
        raise ValueError("Mot de passe de 12 caractères minimum")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return "scrypt$"+salt+"$"+digest

def verify_password(password, encoded):
    try:
        scheme, salt, digest = encoded.split("$")
        if scheme != "scrypt": return False
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return secrets.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False
