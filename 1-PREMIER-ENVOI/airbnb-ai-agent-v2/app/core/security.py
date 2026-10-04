import hashlib
import json
import re
from cryptography.fernet import Fernet

class Vault:
    def __init__(self, key: str):
        self.fernet = Fernet(key.encode()) if key else None

    def encrypt(self, value) -> str:
        if not self.fernet:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY requis pour stocker des données")
        return self.fernet.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def decrypt(self, value: str):
        if not self.fernet:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY requis")
        return json.loads(self.fernet.decrypt(value.encode()))

    def redact(self, text: str, secrets: list[str]) -> str:
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                text = text.replace(secret, "[MASQUÉ]")
        text = re.sub(r"https?://\S+", "[LIEN]", text)
        return re.sub(r"\b\d{4,}\b", "[MASQUÉ]", text)

def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def nested_strings(value):
    """Flatten sensitive values, including lists of access photos, for redaction."""
    if isinstance(value,str):
        return [value]
    if isinstance(value,dict):
        return [item for child in value.values() for item in nested_strings(child)]
    if isinstance(value,(list,tuple)):
        return [item for child in value for item in nested_strings(child)]
    return []
