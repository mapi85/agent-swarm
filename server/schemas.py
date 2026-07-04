"""Schémas Pydantic de l'API v2 (auth & comptes — les schémas métier
arriveront avec les chantiers suivants)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class ChangeEmailIn(BaseModel):
    current_password: str
    new_email: EmailStr


class SetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    role: str
    status: str
    quota_short_tokens: int
    quota_short_hours: int
    quota_long_tokens: int
    quota_long_days: int
    created_at: datetime


class TokenOut(BaseModel):
    token: str
    user: UserOut


class UserCreateIn(BaseModel):
    """Création directe par l'admin (compte actif immédiatement)."""
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="user", pattern="^(admin|user)$")


class UserPatchIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    quota_short_tokens: int | None = Field(default=None, ge=0)
    quota_short_hours: int | None = Field(default=None, ge=0)
    quota_long_tokens: int | None = Field(default=None, ge=0)
    quota_long_days: int | None = Field(default=None, ge=0)


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ptype: str
    base_url: str
    default_model: str
    models: list[str]
    native_features: bool
    is_default: bool
    priority: int
    limit_short_tokens: int
    limit_short_hours: int
    limit_long_tokens: int
    limit_long_days: int
    created_at: datetime
    # enrichis par le router (jamais depuis l'ORM directement)
    api_key_set: bool = False
    usage_short: int = 0
    usage_long: int = 0
    agent_count: int = 0


class ProviderCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ptype: str = Field(default="anthropic", pattern="^(anthropic|openai)$")
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=2000)
    default_model: str = Field(default="", max_length=120)
    models: list[str] = Field(default_factory=list)
    native_features: bool = True
    is_default: bool = False
    limit_short_tokens: int = Field(default=0, ge=0)
    limit_short_hours: int = Field(default=0, ge=0)
    limit_long_tokens: int = Field(default=0, ge=0)
    limit_long_days: int = Field(default=0, ge=0)


class ProviderPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = None  # None = inchangée, "" = effacée
    default_model: str | None = None
    models: list[str] | None = None
    native_features: bool | None = None
    limit_short_tokens: int | None = Field(default=None, ge=0)
    limit_short_hours: int | None = Field(default=None, ge=0)
    limit_long_tokens: int | None = Field(default=None, ge=0)
    limit_long_days: int | None = Field(default=None, ge=0)


class FetchModelsIn(BaseModel):
    """Interrogation de l'API d'un provider (existant via provider_id,
    ou en cours de saisie via les champs libres)."""
    provider_id: int | None = None
    ptype: str = Field(default="anthropic", pattern="^(anthropic|openai)$")
    base_url: str = ""
    api_key: str = ""


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------

class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int | None
    name: str
    description: str
    mission_prompt: str
    category: str
    provider_id: int | None
    model: str
    effort: str
    max_iterations: int
    session_token_budget: int
    max_parallel_tasks: int
    paused: bool
    created_at: datetime
    open_tasks: int = 0
    running_tasks: int = 0


class AgentCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    mission_prompt: str = Field(min_length=1)
    category: str = Field(default="", max_length=120)
    provider_id: int | None = None  # None = provider par défaut
    model: str = Field(default="", max_length=120)  # "" = default_model du provider
    effort: str = Field(default="high", pattern="^(low|medium|high|max)$")
    max_iterations: int = Field(default=60, ge=5, le=500)
    session_token_budget: int = Field(default=0, ge=0)
    max_parallel_tasks: int = Field(default=1, ge=1, le=10)
    system: bool = False  # agent système (admin uniquement)


class AgentPatchIn(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    mission_prompt: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, max_length=120)
    provider_id: int | None = None  # null explicite = repasser au provider par défaut
    model: str | None = Field(default=None, min_length=1, max_length=120)
    effort: str | None = Field(default=None, pattern="^(low|medium|high|max)$")
    max_iterations: int | None = Field(default=None, ge=5, le=500)
    session_token_budget: int | None = Field(default=None, ge=0)
    max_parallel_tasks: int | None = Field(default=None, ge=1, le=10)


# --------------------------------------------------------------------------
# Tâches & liens
# --------------------------------------------------------------------------

class TaskLinkIn(BaseModel):
    task_id: int  # tâche antérieure (antécédent)
    kind: str = Field(default="follow_up", pattern="^(depends_on|follow_up)$")


class TaskCreateIn(BaseModel):
    agent_id: int
    title: str = Field(default="", max_length=300)
    description: str = Field(min_length=1)
    links: list[TaskLinkIn] = Field(default_factory=list)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mission_id: int | None
    agent_id: int
    owner_user_id: int
    title: str
    description: str
    result: str | None
    status: str
    created_by: str
    created_by_agent_id: int | None
    input_tokens: int
    output_tokens: int
    created_at: datetime
    completed_at: datetime | None


class TaskLinkOut(BaseModel):
    task_id: int
    title: str
    status: str
    agent_id: int
    kind: str


class TaskDetailOut(TaskOut):
    antecedents: list[TaskLinkOut] = Field(default_factory=list)  # liens sortants (amont direct)
    dependents: list[TaskLinkOut] = Field(default_factory=list)  # liens entrants (aval direct)


class UsageOut(BaseModel):
    short_used: int
    short_limit: int
    short_hours: int
    long_used: int
    long_limit: int
    long_days: int


# --------------------------------------------------------------------------
# Missions & sessions
# --------------------------------------------------------------------------

class MissionCreateIn(BaseModel):
    mission: str = Field(min_length=1)


class MissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int
    title: str
    mission: str
    summary: str
    plan: dict | None
    status: str
    input_tokens: int
    output_tokens: int
    created_at: datetime
    updated_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    agent_id: int
    number: int
    objective: str
    status: str
    scheduled_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    report: str | None
    deliverables: list | None
    error: str | None
    provider_id: int | None
    user_note: str | None
    input_tokens: int
    output_tokens: int


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    ts: datetime
    type: str
    content: str


class RunNowIn(BaseModel):
    user_note: str | None = None
