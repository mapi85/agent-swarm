"""Modèles SQLAlchemy — schéma cible de la refonte (REFONTE.md §9).

Conventions :
- PK BIGINT IDENTITY BY DEFAULT (permet d'insérer les ids migrés depuis SQLite) ;
- horodatages TIMESTAMPTZ, created_at par défaut côté serveur ;
- JSON en JSONB sous PostgreSQL (variant JSON générique pour les tests) ;
- statuts/énumérations en chaînes courtes, contrôlés par l'applicatif ;
- colonnes suffixées _enc : chiffrées via server.crypto, jamais de clair.
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonB = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=False), primary_key=True)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --------------------------------------------------------------------------
# Comptes & authentification
# --------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("uq_users_email_lower", text("lower(email)"), unique=True),)

    id: Mapped[int] = pk()
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")  # admin | user
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending | active | disabled
    # Quotas de tokens (fenêtres glissantes, 0 = illimité)
    quota_short_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    quota_short_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_long_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    quota_long_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = created_at()


class UserToken(Base):
    """Tokens d'API opaques, révocables individuellement (stockés hashés)."""
    __tablename__ = "user_tokens"

    id: Mapped[int] = pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[int] = pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


# --------------------------------------------------------------------------
# Providers LLM (mutualisés, gérés par l'admin)
# --------------------------------------------------------------------------

class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    ptype: Mapped[str] = mapped_column(String(16), nullable=False, default="anthropic")  # anthropic | openai
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    default_model: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    models: Mapped[list] = mapped_column(JsonB, nullable=False, default=list)
    native_features: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 1 = essayé en premier
    limit_short_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    limit_short_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limit_long_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    limit_long_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = created_at()


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------

class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        # Unicité du nom par périmètre : parmi les agents système (owner NULL)
        # et parmi les agents de chaque utilisateur.
        Index("uq_agents_scope_name", text("coalesce(owner_user_id, 0)"), "name", unique=True),
    )

    id: Mapped[int] = pk()
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)  # NULL = agent système
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mission_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id"))  # NULL = provider par défaut
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    effort: Mapped[str] = mapped_column(String(16), nullable=False, default="high")
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    session_token_budget: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    max_parallel_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Cadence de veille : si > 0, le planificateur garantit une session au moins
    # toutes les N minutes quand l'agent est inactif (agents événementiels/récurrents
    # qui, sinon, deviennent dormants une fois leurs tâches terminées). 0 = désactivé.
    heartbeat_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = created_at()
    # L'état d'exécution (inactif / n tâches en cours) se dérive des sessions.


# --------------------------------------------------------------------------
# Missions, tâches, sessions
# --------------------------------------------------------------------------

class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[int] = pk()
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    mission: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    plan: Mapped[dict | None] = mapped_column(JsonB)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="proposed")
    # proposed | running | completed | needs_attention | archived
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = pk()
    mission_id: Mapped[int | None] = mapped_column(ForeignKey("missions.id"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    # pending | ready | in_progress | waiting_user | done | failed | cancelled
    created_by: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    # user | supervisor | self | agent
    created_by_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Nb de sessions consécutives closes sans progrès (ni next_objective, ni done,
    # ni ask_user). Au-delà du seuil → 'stalled' (bac À traiter). Reset sur vrai progrès.
    consecutive_stalls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    created_at: Mapped[datetime] = created_at()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskLink(Base):
    """Liens entre tâches : dépendances de mission et « porosité » (follow_up).

    L'héritage est transitif : une tâche lit les ressources/artefacts de toute
    sa chaîne d'ascendance. Les cycles sont refusés à l'insertion.
    """
    __tablename__ = "task_links"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    linked_task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)  # depends_on | follow_up


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = pk()
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # séquence par tâche
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="planned", index=True)
    # planned | running | completed | failed | interrupted
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report: Mapped[str | None] = mapped_column(Text)
    deliverables: Mapped[list | None] = mapped_column(JsonB)
    error: Mapped[str | None] = mapped_column(Text)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    user_note: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class Event(Base):
    """Flux d'événements d'une session. Purgé au-delà de la rétention configurée."""
    __tablename__ = "events"
    __table_args__ = (Index("idx_events_session", "session_id", "id"),)

    id: Mapped[int] = pk()
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = created_at()
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    # status | thinking | text | tool_use | tool_result | error | usage
    content: Mapped[str] = mapped_column(Text, nullable=False)


class TokenUsage(Base):
    """Une ligne par appel LLM : sert les jauges providers, les quotas
    utilisateur (fenêtres glissantes) et les statistiques, sans agréger events."""
    __tablename__ = "token_usage"
    __table_args__ = (
        Index("idx_usage_user_ts", "user_id", "ts"),
        Index("idx_usage_provider_ts", "provider_id", "ts"),
    )

    id: Mapped[int] = pk()
    ts: Mapped[datetime] = created_at()
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # part des input_tokens relue depuis le cache de préfixe (facturée à prix réduit)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


# --------------------------------------------------------------------------
# Messagerie, notifications, canaux
# --------------------------------------------------------------------------

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = pk()
    from_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))  # NULL = système
    to_agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))  # contexte du handoff
    content: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = created_at()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("idx_notif_user_status", "user_id", "status", "id"),)

    id: Mapped[int] = pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)  # destinataire
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"))
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # alert | question
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open | answered | dismissed
    content: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str | None] = mapped_column(Text)
    external_ids: Mapped[dict | None] = mapped_column(JsonB)  # ex. message_id Telegram par canal
    channel_dispatched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = created_at()
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationChannel(Base):
    """Canaux par utilisateur (le routage n'est plus par agent)."""
    __tablename__ = "notification_channels"

    id: Mapped[int] = pk()
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # email | telegram
    config_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    use_for_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    use_for_questions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at()


# --------------------------------------------------------------------------
# Ressources, mémoire, services, réglages
# --------------------------------------------------------------------------

class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (Index("idx_res_scope", "scope", "owner_user_id", "task_id"),)

    id: Mapped[int] = pk()
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # shared | user | task
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # file | note | link
    filename: Mapped[str | None] = mapped_column(String(400))  # relatif à data/resources/
    content: Mapped[str | None] = mapped_column(Text)  # texte de la note ou URL du lien
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="user")
    created_at: Mapped[datetime] = created_at()


class Memory(Base):
    """Mémoire structurée. Pour les agents système, user_id cloisonne
    strictement la mémoire par utilisateur (REFONTE.md §3)."""
    __tablename__ = "memories"
    __table_args__ = (
        Index(
            "uq_memories_scope_key",
            "agent_id",
            text("coalesce(user_id, 0)"),
            "scope",
            text("coalesce(task_id, 0)"),
            "mkey",
            unique=True,
        ),
    )

    id: Mapped[int] = pk()
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # agent | task
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    mkey: Mapped[str] = mapped_column(String(200), nullable=False)
    mvalue: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Service(Base):
    """Registre des services/ports déclarés par les agents (hôte partagé)."""
    __tablename__ = "services"

    id: Mapped[int] = pk()
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    port: Mapped[int | None] = mapped_column(Integer)
    command: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running | stopped
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AppSetting(Base):
    """Réglages globaux (ex. smtp_config — valeurs sensibles chiffrées)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JsonB, nullable=False)
