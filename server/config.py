"""Configuration du backend v2 (variables d'environnement / .env)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # postgresql+psycopg://user:mdp@hote:port/base — le driver psycopg sert
    # à la fois l'app (async) et Alembic (sync)
    database_url: str = "postgresql+psycopg://swarm:swarm@127.0.0.1:5433/swarm"

    # Clé Fernet (base64, 32 octets) pour chiffrer les secrets stockés en base
    # (clés API providers, tokens Telegram, mot de passe SMTP).
    # Génération : python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    secret_encryption_key: str = ""

    log_level: str = "INFO"
    data_dir: Path = Path("data")

    @property
    def agents_dir(self) -> Path:
        return self.data_dir / "agents"

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def resources_dir(self) -> Path:
        return self.data_dir / "resources"


@lru_cache
def get_settings() -> Settings:
    return Settings()
