"""Create local settings once without printing any secret."""
from pathlib import Path
import secrets
from cryptography.fernet import Fernet

if __name__ == "__main__":
    target = Path("private/local.env")
    target.parent.mkdir(exist_ok=True)
    target.parent.chmod(0o700)
    if target.exists():
        raise SystemExit("private/local.env existe déjà; conservé intact.")
    template = Path(".env.example").read_text()
    template = template.replace("ADMIN_SECRET=\n", "ADMIN_SECRET="+secrets.token_urlsafe(36)+"\n")
    template = template.replace("TOKEN_ENCRYPTION_KEY=\n", "TOKEN_ENCRYPTION_KEY="+Fernet.generate_key().decode()+"\n")
    target.write_text(template)
    target.chmod(0o600)
    print("private/local.env créé avec TEST_MODE=true. Mot de passe admin et clé de chiffrement disponibles dans ce fichier local.")
