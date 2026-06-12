"""Configuration centrale de la plateforme d'essaim d'agents."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Stockage
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
AGENTS_DIR = DATA_DIR / "agents"
RESOURCES_DIR = DATA_DIR / "resources"
DB_PATH = DATA_DIR / "swarm.db"

# Modèle par défaut
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "claude-opus-4-8")
DEFAULT_EFFORT = os.getenv("DEFAULT_EFFORT", "high")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))

# Sous-agents en contexte (fan-out rapide, modèle économique)
SUBAGENT_MODEL = os.getenv("SUBAGENT_MODEL", "claude-haiku-4-5")
SUBAGENT_MAX_ITERATIONS = int(os.getenv("SUBAGENT_MAX_ITERATIONS", "8"))

# Garde-fous d'exécution
DEFAULT_MAX_ITERATIONS = int(os.getenv("DEFAULT_MAX_ITERATIONS", "60"))
SHELL_TIMEOUT_DEFAULT = int(os.getenv("SHELL_TIMEOUT_DEFAULT", "300"))
SHELL_TIMEOUT_MAX = int(os.getenv("SHELL_TIMEOUT_MAX", "1800"))
TOOL_OUTPUT_LIMIT = int(os.getenv("TOOL_OUTPUT_LIMIT", "50000"))

# Garde-fous contexte : cap par résultat d'outil + seuil d'élision des anciens résultats
TOOL_RESULT_MAX_CHARS = int(os.getenv("TOOL_RESULT_MAX_CHARS", "12000"))
CONTEXT_TRIM_THRESHOLD = int(os.getenv("CONTEXT_TRIM_THRESHOLD", "150000"))
CONTEXT_KEEP_LAST = int(os.getenv("CONTEXT_KEEP_LAST", "8"))

# Budget de tokens par session (cumul in+out ; 0 = illimité), surchargé par agent
DEFAULT_SESSION_TOKEN_BUDGET = int(os.getenv("DEFAULT_SESSION_TOKEN_BUDGET", "0"))

# Détection de stagnation
MAX_CONSECUTIVE_TOOL_ERRORS = int(os.getenv("MAX_CONSECUTIVE_TOOL_ERRORS", "6"))
MAX_REPEAT_TOOL_CALLS = int(os.getenv("MAX_REPEAT_TOOL_CALLS", "4"))

# Sécurité
# Allowlist e-mail : domaines et/ou adresses autorisés (séparés par des virgules ; vide = tout permis)
EMAIL_ALLOWLIST = [x.strip().lower() for x in os.getenv("EMAIL_ALLOWLIST", "").split(",") if x.strip()]
# Motifs shell interdits (regex, séparés par des « ;;; »)
_DEFAULT_DENY = r"rm\s+-rf\s+/(?:\s|$);;;\bmkfs\b;;;\b:\(\)\s*\{.*\};:;;;\bdd\s+if=.*of=/dev/[sh]d"
SHELL_DENY_PATTERNS = [p for p in os.getenv("SHELL_DENY_PATTERNS", _DEFAULT_DENY).split(";;;") if p.strip()]

# Planificateur
SCHEDULER_INTERVAL_S = int(os.getenv("SCHEDULER_INTERVAL_S", "10"))
MAX_CONCURRENT_SESSIONS = int(os.getenv("MAX_CONCURRENT_SESSIONS", "4"))

# SMTP (envoi de mails par les agents)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

IS_WINDOWS = sys.platform == "win32"

DATA_DIR.mkdir(parents=True, exist_ok=True)
AGENTS_DIR.mkdir(parents=True, exist_ok=True)
RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
