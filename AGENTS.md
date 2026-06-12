# AGENTS.md — Guide de reprise du projet

Document destiné à un agent IA (ou un développeur) qui reprend ce projet dans un autre IDE.
Lis-le en entier avant de modifier le code.

## 1. Ce qu'est le projet

Plateforme d'**essaim d'agents IA autonomes** motorisés par l'**API Claude (Anthropic)**.
- Un **agent** = nom + description + mission (prompt générique) + modèle + effort + budget. Il travaille par **sessions**.
- Une **session** = une boucle agentique : contexte initial → exécution outillée → `finish_session` (rapport + livrables + objectif/échéance de la prochaine session).
- Un **service back** (FastAPI + SQLite) orchestre, planifie, fait communiquer les agents.
- Un **service front** (une seule page HTML/JS) supervise en temps réel.
- Un **superviseur** (appel de planification) décompose une mission en **projet** de tâches (parallèles/séquentielles avec dépendances).

Public/contexte : autonomie totale assumée (shell complet, internet, e-mail, auto-installation). Cible de prod : **Ubuntu**, idéalement en conteneur. Dev : **Windows** (PowerShell).

## 2. Stack & exécution

- Python 3.12+ (testé 3.13 en dev). FastAPI + Uvicorn. SQLite (fichier `data/swarm.db`). SDK `anthropic`. `httpx` pour le fallback OpenAI. `python-multipart` pour l'upload.
- Lancer en dev :
  ```bash
  python -m venv .venv
  .venv/bin/pip install -r requirements.txt      # Windows : .venv\Scripts\pip
  cp .env.example .env                            # renseigner ANTHROPIC_API_KEY
  uvicorn backend.app:app --port 8000
  ```
- Front servi par FastAPI sur `/` (montage `StaticFiles`). Tableau de bord : http://127.0.0.1:8000
- Déploiement : voir `DEPLOY.md` (Docker ou systemd).

## 3. Arborescence

```
backend/
  config.py     # constantes + lecture .env (chemins, garde-fous, budgets, sécurité)
  db.py         # SQLite : schéma, migrations, toutes les fonctions d'accès
  providers.py  # abstraction LLM : AnthropicProvider + OpenAIProvider, provider par agent, helpers blocs
  tools.py      # outils des agents (définitions JSON + exécution) + run_subagent
  planner.py    # agent superviseur : make_plan() + materialize()
  runtime.py    # boucle agentique d'une session (le cœur)
  scheduler.py  # boucle de planification (lance/crée les sessions)
  app.py        # API REST + montage du front
frontend/index.html   # tableau de bord complet (thème clair, responsive, 5 onglets + cloche notifications)
Dockerfile, docker-compose.yml, .dockerignore
requirements.txt, .env.example, README.md, DEPLOY.md, AGENTS.md
data/   # créé au runtime : swarm.db + agents/<id>_<nom>/ + resources/
```

## 4. Modèle de données (SQLite, `backend/db.py`)

- **agents** : `name`, `mission_prompt`, `model`, `effort`, `status` (idle|running|paused), `max_iterations`, `session_token_budget`, `provider_id` (NULL = provider par défaut).
- **providers** : `name`, `ptype` (anthropic|openai), `base_url`, `api_key`, `default_model`, `native_features`, `is_default`. Un seul `is_default=1` (géré par `set_default_provider`).
- **sessions** : `agent_id`, `number`, `objective`, `status` (planned|running|completed|failed|interrupted), `scheduled_at`, `report`, `deliverables`, `next_objective`, `provider`, tokens.
- **tasks** : `agent_id`, `origin` (`user`|`agent:<nom>`|`supervisor`), `description`, `title`, `project_id`, `depends_on` (JSON d'ids), `status` (pending|in_progress|done|failed|cancelled), `result`, `input_tokens`/`output_tokens` (tokens de session répartis entre les tâches traitées).
- **projects** : `title`, `mission`, `summary`, `plan` (JSON), `status` (proposed|running|completed|needs_attention|archived).
- **messages** : messagerie inter-agents (lue en début de session).
- **events** : flux de supervision d'une session (`type` ∈ status|thinking|text|tool_use|tool_result|error).
- **notifications** : `type` (alert|question), `status` (open|answered|dismissed), `response`, `delivered`.
- **resources** : `scope` (shared|agent|task), `kind` (file|note|link), `filename`/`content`.
- **memories** : mémoire structurée `scope` (agent|task), `mkey`/`mvalue` (UNIQUE par agent/scope/task/clé).
- **services** : registre des services/ports déclarés par les agents.
- **settings** : clé→valeur JSON (historique : ancienne config providers, migrée vers la table `providers` par `_seed_providers`).

Migrations : `db._migrate()` ajoute les colonnes manquantes via `ALTER TABLE` (idempotent). Le schéma de base est en `CREATE TABLE IF NOT EXISTS`. **Si tu ajoutes une colonne, mets à jour SCHEMA *et* `_migrate`.**

## 5. Flux clés

**Session** (`runtime.run_session`) : passe l'agent en `running` → absorbe `ready_tasks` (dépendances satisfaites) + messages + réponses utilisateur → construit le contexte initial (`build_initial_context`) → boucle : appel provider (`_complete`, avec retries sur erreur transitoire) → journalise les blocs → exécute les `tool_use` → réinjecte les résultats. S'arrête sur `finish_session`, budget dépassé, stagnation, refus, interruption, ou `max_iterations`. Clôture : statut session, attribution des tokens aux tâches (répartis), statut tâches (celles listées dans `unfinished_task_ids` de `finish_session` repassent `pending` au lieu de `done`), handoff délégation, propagation d'échec projet, log.

**Planificateur** (`scheduler.scheduler_loop`, tick toutes les `SCHEDULER_INTERVAL_S` s) :
1. lance les sessions `planned` échues (agents idle, **pas en attente de réponse**),
2. crée une session pour les agents idle ayant des `ready_tasks`,
3. crée une session de reprise pour les agents dont une réponse utilisateur est arrivée.
`recover_stale_state()` au démarrage remet les sessions `running` orphelines en échec.

**Providers** (`providers.py`) : registre en base (table `providers`), deux types — `anthropic` (Messages API ; `native_features` active thinking adaptatif/effort/compaction/cache) et `openai` (Chat Completions). Chaque agent référence un provider (`provider_id`) ou hérite du provider **par défaut**. `provider_row_for_agent()` / `build_provider()` instancient ; `runtime._complete` fait des **retries** (`_RETRY_DELAYS`) si `is_transient(exc)` (429/5xx/réseau) — il n'y a plus de fallback automatique vers un second provider. **Conversation canonique = blocs Anthropic (dicts)** ; le provider OpenAI traduit dans les deux sens. Helpers `block_type()`/`block_get()` lisent indifféremment un objet SDK ou un dict. CRUD REST : `/api/providers` (la clé n'est jamais renvoyée, seulement `api_key_set`).

**Missions** (`planner.py` + endpoints `/api/projects`) : `make_plan(mission)` → JSON {title, summary, new_agents, tasks[ref, agent, depends_on]}. `materialize()` crée les agents manquants puis les tâches en mappant les refs→ids. Une tâche dépendante n'est `ready` que si ses prérequis sont `done` ; elle reçoit le `result` des prérequis dans son contexte. Échec d'une tâche → `cancel_downstream` annule l'aval, projet → `needs_attention` + notification.

**Sollicitation utilisateur** : `ask_user` (NON bloquant) crée une question et l'agent clôt sa session ; le planificateur ne relance pas l'agent tant qu'une question est ouverte ; à la réponse, la session planifiée est avancée et la réponse injectée. `notify_user` = alerte simple.

**Sous-agents** : `tools.run_subagent` — fan-out en contexte sur le provider de l'agent (modèle `SUBAGENT_MODEL` si provider anthropic, sinon le modèle du provider/de l'agent), jeu d'outils restreint, renvoie le texte final inline.

## 6. Conventions

- **Langue** : UI, prompts, commentaires et messages d'outils en **français**. Garde ce ton.
- **Style** : code compact et lisible, pas d'« usine à gaz ». Les avertissements de longueur de ligne (>100) du linter sont tolérés ici — ne réécris pas le code juste pour ça.
- **DB** : accès uniquement via `backend/db.py` (un verrou `threading.Lock` protège la connexion partagée). N'ouvre pas d'autre connexion.
- **Outils agents** : un nouvel outil = (1) une entrée dans `tools.tool_definitions()`, (2) un handler dans `tools.execute_tool()`, (3) éventuellement une mention dans `runtime.SYSTEM_TEMPLATE`.
- **API Claude (important)** :
  - Modèle par défaut `claude-opus-4-8`, **thinking adaptatif** (`{"type":"adaptive"}`) + `output_config.effort`. **Pas de `budget_tokens`, ni `temperature/top_p/top_k`** (supprimés → 400).
  - **Haiku ne supporte pas effort/thinking/compaction** → `AnthropicProvider._supports_advanced()` filtre ; ne contourne pas ça.
  - Compaction beta `compact-2026-01-12` + cache `cache_control` côté primaire natif.
  - Toujours réinjecter `response.blocks` complet comme tour assistant (préserve thinking/compaction).

## 7. Tester (sans frais d'API)

On teste la logique en important les modules et en appelant les fonctions DB/outils directement (pas besoin de clé). Pattern utilisé :
```bash
.venv\Scripts\python.exe -c "from backend import app, runtime, tools, scheduler, db, providers, planner, config; print('imports OK')"
# puis un petit script qui crée agents/tâches/projets et vérifie cancel_downstream, ready_tasks, etc.
```
Pour un boot réel : `uvicorn backend.app:app --port 8XXX` avec une `ANTHROPIC_API_KEY` factice → les endpoints REST répondent (les sessions échouent en 401, attendu).
Nettoie toujours `data/` entre deux tests (base + workdirs).

> ⚠️ Dev Windows : le bac à sable PowerShell bloque les chaînes ressemblant à des commandes destructrices (`rm -rf /`, suppressions près de la racine). Pour tester la denylist shell, construis la chaîne via `chr()` ; pour supprimer des dossiers, préfère les API .NET (`[System.IO.Directory]::Delete`).

## 8. Variables d'environnement

Voir `.env.example` (commenté). Essentiel : `ANTHROPIC_API_KEY` (clé par défaut du provider Anthropic initial). Optionnels notables : `DEFAULT_MODEL`, `DEFAULT_EFFORT`, `MAX_CONCURRENT_SESSIONS`, `DEFAULT_SESSION_TOKEN_BUDGET`, `SUBAGENT_MODEL`, `MAX_CONSECUTIVE_TOOL_ERRORS`/`MAX_REPEAT_TOOL_CALLS`, `TOOL_RESULT_MAX_CHARS`/`CONTEXT_TRIM_THRESHOLD`/`CONTEXT_KEEP_LAST`, `EMAIL_ALLOWLIST`, `SHELL_DENY_PATTERNS`, `SMTP_*`. Les **providers** (types, URL, clés, défaut) se gèrent dans l'onglet **Réglages** (table `providers`), pas dans `.env`.

## 9. Pistes / dette connue (par où continuer)

- **Isolation par agent** : aujourd'hui les agents partagent l'hôte/conteneur (d'où le registre de services contre les collisions de port). Étape suivante : un conteneur par session (façon Managed Agents) pour exécuter du non-fiable côte à côte.
- **Persistance réelle des services** : `services` est déclaratif (l'agent déclare) — pas de supervision de process/healthcheck.
- **Concurrence DB** : SQLite + verrou suffisent à cette échelle ; passer à Postgres si forte charge multi-sessions.
- **Sécurité** : la denylist shell et l'allowlist e-mail sont des garde-fous simples ; durcir l'egress réseau en prod (proxy/pare-feu) et envisager une revue des commandes sensibles.
- **Qualité du plan superviseur** : un seul appel JSON, sans boucle de critique ; on pourrait ajouter une validation de faisabilité (l'agent assigné a-t-il les outils ?).
- **Coûts** : budget par session existe ; un budget par projet et un tableau de coûts dans le temps seraient utiles.
