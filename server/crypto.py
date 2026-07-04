"""Chiffrement applicatif des secrets stockés en base (Fernet).

Les colonnes suffixées `_enc` (clés API providers, config des canaux, SMTP)
ne contiennent jamais de clair : toujours passer par encrypt_secret/decrypt_secret.
"""
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class EncryptionKeyMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "SECRET_ENCRYPTION_KEY absente : impossible de chiffrer/déchiffrer les secrets. "
            "Générer une clé avec : python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )


def _fernet() -> Fernet:
    key = get_settings().secret_encryption_key
    if not key:
        raise EncryptionKeyMissing()
    return Fernet(key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Secret indéchiffrable : SECRET_ENCRYPTION_KEY a-t-elle changé ?") from exc
