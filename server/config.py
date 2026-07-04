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

    # URL publique de base (pour enregistrer les webhooks Telegram)
    public_base_url: str = ""
    # Rétention des événements de session (jours ; 0 = pas de purge)
    event_retention_days: int = 30

    # --- Modèles par défaut ---
    default_model: str = "claude-opus-4-8"
    default_effort: str = "high"
    max_tokens: int = 16000
    subagent_model: str = "claude-haiku-4-5"
    subagent_max_iterations: int = 8

    # --- Garde-fous d'exécution ---
    shell_timeout_default: int = 300
    shell_timeout_max: int = 1800
    tool_output_limit: int = 50000
    tool_result_max_chars: int = 12000
    context_trim_threshold: int = 150000
    context_keep_last: int = 8
    default_session_token_budget: int = 0
    max_consecutive_tool_errors: int = 6
    max_repeat_tool_calls: int = 4

    # --- Planificateur ---
    scheduler_interval_s: int = 10
    max_concurrent_sessions: int = 4

    # --- Sécurité ---
    email_allowlist: str = ""  # adresses/domaines autorisés, séparés par des virgules ; vide = tout permis
    shell_deny_patterns: str = (
        r"rm\s+-rf\s+/(?:\s|$);;;\bmkfs\b;;;\b:\(\)\s*\{.*\};:;;;\bdd\s+if=.*of=/dev/[sh]d"
    )

    # --- SMTP (envoi de mails par les agents ; les canaux utilisateur arrivent au chantier 5) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    @property
    def email_allowlist_set(self) -> set[str]:
        return {x.strip().lower() for x in self.email_allowlist.split(",") if x.strip()}

    @property
    def shell_deny_list(self) -> list[str]:
        return [p for p in self.shell_deny_patterns.split(";;;") if p.strip()]

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
