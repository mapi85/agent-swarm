# Audit complet — agent-swarm (préparation refonte)

*Audit réalisé le 2026-07-04 sur le commit `e279150` (main). Objectif : disposer de tous les éléments avant refonte majeure, avec migration correcte de l'existant.*

---

## 1. État du serveur de production (87.106.1.191)

- **Machine** : VPS Ubuntu 26.04 LTS, 4 vCPU, 3.8 Go RAM, disque 116 Go (7 % utilisé), uptime 23 jours.
- **Déploiement** : Docker (option A de DEPLOY.md), dépôt cloné dans `~/agent-swarm` (utilisateur `spidle33`).
  - Conteneur `agent-swarm` (image `agent-swarm-swarm`, Python 3.12.13), up 41 h, port `127.0.0.1:8000`.
  - **Code déployé = `e279150`**, identique au HEAD local. Arbre git propre.
- **Reverse proxy** : nginx actif ; sites : `agent-swarm`, `default`, `link.aobipros.app` (⚠ un **autre site** cohabite sur ce serveur). Ports ouverts : 22, 80, 443, 8080, 9090 (+ 8000 local).
- **Données de production** : volume Docker `agent-swarm_swarm-data` = **177 Mo** (swarm.db + workdirs agents + ressources).
- **Couche writable du conteneur : 1,47 Go** — les paquets/outils installés par les agents *dans* le conteneur (hors `/app/data`) sont **éphémères** : perdus à chaque rebuild. Déjà le cas aujourd'hui, à garder en tête pour la refonte.
- **⚠ SAUVEGARDES : AUCUNE sauvegarde des données.** `~/backups/agent-swarm-20260620-095851` (268 Ko) ne contient que du **code**. Pas de crontab. Le volume de 177 Mo (toute la production) n'est sauvegardé nulle part. **À corriger avant toute opération de refonte/migration.**
- Divers : `~/agent-swarm.zip` (10 juin, 60 Ko, vestige pré-git).

*Non inspecté (bloqué par les contrôles de permission, volontairement non contourné) : contenu de `.env` serveur, config nginx détaillée, intérieur du conteneur (docker exec), contenu de la base (l'API exige un token d'auth).*

---

## 2. Cartographie applicative

Monorepo : backend Python **FastAPI + SQLite** (~4 200 lignes), frontend **SPA monolithique vanilla JS** (`frontend/index.html`, ~2 500 lignes), servie statiquement par le backend. Un seul process uvicorn fait tout : API REST, front, scheduler, exécution des sessions d'agents.

| Module | Rôle |
|---|---|
| `config.py` | Constantes + `.env` ; crée `data/` à l'import |
| `db.py` | Schéma SQLite + migrations auto + ~90 fonctions d'accès ; connexion globale unique + `threading.Lock` |
| `providers.py` | Abstraction LLM : Anthropic (Messages API, thinking/effort/compaction/cache) et OpenAI-compatible (Chat Completions httpx) ; listing dynamique des modèles |
| `tools.py` | ~25 outils agents (shell, fichiers, mémoire, ressources, services, délégation, messagerie, ask/notify_user, email, finish_session, sous-agents) + outils serveur web_search/web_fetch |
| `runtime.py` | Boucle agentique de session ; registre global `RUNNING` ; retries + bascule multi-providers ; trim de contexte ; anti-stagnation |
| `planner.py` | Superviseur de missions : 1 appel LLM → plan JSON → matérialisation en tâches avec dépendances |
| `scheduler.py` | Boucle asyncio (tick 10 s) : sessions échues, tâches prêtes, reprises post-réponse, dispatch notifications |
| `notify.py` | Canaux externes : email SMTP + Telegram (sendMessage/webhook) |
| `app.py` | ~55 endpoints REST + middleware d'auth + montage du front |

**Flux clés** : cycle de session (planned → running → finish_session → session suivante auto-planifiée) ; missions (plan proposé → approve → tâches avec vagues de dépendances → cascade d'annulation sur échec → retry) ; fallback providers ordonné par `priority` avec reprise planifiée sur 429 ; notifications → email/Telegram, réponse par UI ou reply Telegram.

---

## 3. Modèle de données (critique pour la migration)

Base `data/swarm.db`. Dates en TEXT ISO-8601 UTC. **Clés étrangères jamais activées** (`REFERENCES` décoratifs) → orphelins probables dans les données de prod.

### 16 tables

1. **profiles** : id, name UNIQUE, created_at
2. **agents** : id, name UNIQUE, description, mission_prompt, model, effort, status (idle|running|paused), category, max_iterations, session_token_budget, profile_id (NULL = agent système), created_at, +provider_id (NULL = provider défaut)
3. **sessions** : id, agent_id, number (séquence/agent), objective, status (planned|running|completed|failed|interrupted), scheduled_at, started_at, ended_at, report, deliverables (JSON), next_objective, error, **provider (nom, pas id)**, user_note, input_tokens, output_tokens
4. **projects** : id, title, mission, summary, plan (JSON), status (proposed|running|completed|needs_attention|archived), created_at, updated_at, +profile_id
5. **tasks** : id, agent_id, origin ('user'|'agent:<nom>'|'supervisor'), description, title, project_id, depends_on (JSON d'ids), status (pending|in_progress|done|failed|cancelled), result, session_id, created_at, completed_at, +input/output_tokens
6. **messages** : id, **from_agent (nom)**, to_agent_id, content, read, created_at
7. **events** : id, session_id, agent_id, ts, type (status|thinking|text|tool_use|tool_result|error|usage), content — volumétrie principale, non bornée, pas de rétention
8. **settings** : key PK, value (JSON) — `smtp_config` ; vestiges possibles `primary_provider`/`fallback_provider`
9. **notifications** : id, agent_id, session_id, type (alert|question), content, status (open|answered|dismissed), response, delivered, created_at, answered_at, +external_ids (JSON Telegram), +channel_dispatched
10. **resources** : id, scope (shared|agent|task), agent_id, task_id, name, kind (file|note|link), **filename (`<id>_<nom>` relatif à data/resources)**, content, description, size, created_by, created_at
11. **memories** : id, agent_id, scope (agent|task), task_id, mkey, mvalue, updated_at, UNIQUE(agent_id,scope,task_id,mkey)
12. **providers** : id, name UNIQUE, ptype (anthropic|openai), base_url, **api_key (EN CLAIR)**, default_model, models (JSON), native_features, is_default, limit_short_tokens/hours, limit_long_tokens/days, priority, created_at
13. **services** : id, agent_id, name, port, command, status, notes, created_at, updated_at
14. **notification_channels** : id, name, type (email|telegram), **config (JSON EN CLAIR : bot_token…)**, enabled, created_at
15. **agent_channels** : (agent_id, channel_id) PK, use_notifs, use_questions

Migrations auto idempotentes au démarrage (`db._migrate` + `_seed_providers`), incluant la création du profil « Par défaut » et le seed du provider Anthropic depuis `ANTHROPIC_API_KEY`.

### Données hors base (à migrer ensemble)
- `data/agents/<id>_<nom>/` : workdir par agent — `memory/MEMORY.md`, `memory/sessions.log`, `library/`, `deliverables/`. **Le nom du dossier encode l'id ET le nom.**
- `data/resources/<rid>_<nom>` : fichiers uploadés — le préfixe encode l'id de la ressource.
- `.env` : secrets (ANTHROPIC_API_KEY, SMTP_*, ADMIN_PASSWORD).

### Pièges de migration identifiés
- **Ne jamais renuméroter les ids** : ils sont encodés dans les noms de dossiers workdir, les noms de fichiers ressources, `tasks.depends_on` (JSON), `notifications.external_ids`, `memories.task_id`, `tasks.session_id`.
- **Références par NOM et non par id** : `sessions.provider`, `tasks.origin` (`agent:<nom>`), `messages.from_agent`. Renommer un provider casse stats/quotas ; renommer un agent casse le handoff de délégation.
- Champs JSON stockés en TEXT : `plan`, `depends_on`, `deliverables`, `models`, `config`, `external_ids`, `settings.value`.
- Unicité `agents.name` **globale** (pas par profil) ; profil « Par défaut » auto-créé par migration.
- Pas de FK → prévoir un nettoyage des orphelins avant migration.
- Secrets en clair en DB (providers.api_key, channels.config, smtp_config.password) : à traiter comme sensibles dans tout dump/export.

---

## 4. Surface API (~55 endpoints)

**Auth** : middleware sur `/api/*` ; si `ADMIN_PASSWORD` vide → accès libre. Token maison HMAC-SHA256 (secret dérivé du mot de passe, payload {pid, exp}, TTL 30 j), via `Authorization: Bearer` ou `?token=`. Exemptés : login, verify, GET /api/profiles, **tout `/api/webhooks/*`** (public, non vérifié).

Familles : auth/profils · overview/timeline/stats · agents (CRUD, pause/resume, tasks, sessions, services, artifacts, memories, channels) · sessions (détail, events polling incrémental, run-now, retry, interrupt) · projects/missions (plan LLM, approve/replan/retry/archive/delete) · notifications (answer/dismiss) · resources (upload multipart 100 Mo, link, content, delete) · providers (CRUD, default, order/move, fetch-models) · settings SMTP · channels (+test) · webhook Telegram. Liste détaillée : voir README + `backend/app.py`.

---

## 5. Frontend — référentiel fonctionnel (checklist de non-régression)

SPA un seul fichier : ~410 l. CSS, ~350 l. HTML (5 vues + 9 modales `<dialog>`), ~1720 l. JS vanilla. Rendu par template strings + `innerHTML`, échappement manuel `esc()`, moteur Markdown maison, **polling 3 s full re-render** (timeline ~15 s ; seul le flux d'événements est incrémental via `?after=`). État : ~20 globales + localStorage (token, toggles) + sessionStorage (profil actif par onglet).

**Fonctionnalités à préserver (~60 comportements)** — inventaire détaillé par vue :

- **Login/profils** : sélecteur profil (« Tous » / existants / création inline), chip profil header, changement de profil avec re-login, cloisonnement `profile_id` propagé sur overview/agents/timeline/stats/notifications/missions ; agents sans profil = système, visibles partout ; 401 → réouverture login.
- **Cloche** : badge notifications ouvertes, filtres statut, questions ❓ / alertes 🔔, réponse inline (Ctrl+Entrée), Ignorer, Markdown rendu, anti-écrasement pendant la saisie.
- **Tableau de bord** : timeline −12h/+6h par thème (dépliable par agent, blocs colorés par statut, ligne « now », tooltips) ; 7 KPI (dont cliquables missions/notifs) ; tuile « Questions en attente » ; agents groupés par état (▶❓⏱⚠⏸💤) avec clic → supervision ; synthèse tokens (4 KPI) ; histogramme jour/heure avec filtre Tout/7j/24h ; 4 tableaux tokens (agent/thème/provider/mission).
- **Supervision** : recherche, « actifs seulement » (persisté), regroupement par thème repliable (état mémorisé) ; détail agent 3 onglets (Description / Sessions & tâches / Artefacts) ; question en attente épinglée ; actions +Tâche, +Session, Modifier, Pause/Reprendre, Supprimer (confirm détaillé) ; rapport dernière session en Markdown ; ressources de l'agent + upload multi-fichiers ; canaux par agent (Alertes/Questions, Questions = Telegram uniquement) ; sessions avec masquage des passées, actions par statut (Stop / ▶ Lancer avec note / ✕ Annuler / ↺ Relancer) ; tâches en `<details>` avec résultat Markdown ; services ; flux d'événements typés colorés avec autoscroll intelligent ; explorateur d'artefacts (fil d'Ariane, recherche, tri).
- **Modale agent** : nom (verrouillé en édition), thème avec datalist, provider, modèle avec fetch live depuis l'API du provider + saisie libre, effort, itérations max, budget tokens, profil.
- **Missions** : description → plan proposé (nouveaux agents + vagues par dépendances), Valider/Régénérer/Supprimer ; suivi avec icônes de statut par tâche, barre de progression, 🔄 Relancer (reprise après échec), Archiver, Supprimer (cascade).
- **Explorateur** : artefacts par agent (navigation dossiers) ; ressources avec filtres scope/agent/mission, upload multiple, liens, notes ; aperçu universel (image, Markdown source/rendu, texte) + téléchargement.
- **Réglages** : SMTP (+test) ; canaux email/Telegram (+test, activation pour agents existants) ; providers ordonnés = **chaîne de secours** (drag & drop + ▲▼), jauges de consommation courte/longue fenêtre (seuils 70/90 %), modale complète (type, base URL, clé, modèles + fetch API, natives, limites, défaut).
- **Transverses** : responsive 3 paliers (mobile OK, modales bottom-sheet), formats fr-FR, préservation des `<details>` ouverts et de la saisie pendant le polling.

---

## 6. Dette technique & risques

### Backend
- **SQLite connexion globale + lock, appels synchrones dans l'event loop** ; séquences multi-requêtes non transactionnelles (`delete_agent`, `materialize`…). Incompatible multi-workers/multi-instances (état global `runtime.RUNNING`, `db._conn`).
- `asyncio.create_task` non référencées (risque GC) ; double écriture du statut agent (tick + run_session) ; course possible sur `MAX_CONCURRENT_SESSIONS`.
- SQL construit par f-strings depuis kwargs (sûr en interne, fragile en refonte). Pas d'ORM, tout en dicts.
- Double logique SMTP divergente (`tools.py` vs `notify.py`).
- Format de conversation hybride (blocs SDK Anthropic vs dicts OpenAI) ; bascule de provider en cours de session = conversion avec perte du thinking.
- **Cloisonnement profils cosmétique** : simple filtre de requête, aucune vérification à l'écriture — tout token accède à tout.

### Sécurité
- **Webhook Telegram public sans aucune vérification** (pas de secret_token) : n'importe qui peut répondre aux questions des agents.
- Secrets en clair en DB ; token dérivé du mot de passe (pas de rotation individuelle) ; pas de rate-limit sur login ; token en query string (fuite logs).
- Shell agents : denylist regex triviale contournable ; `read_file`/`write_file` acceptent des chemins absolus **hors workdir** ; injection de prompt via web_fetch = exécution shell arbitraire (seule défense : consigne système). Mitigé par l'isolation Docker.
- Front : `esc()` n'échappe pas l'apostrophe (XSS potentiel dans les attributs générés, contenu venant des LLM) ; moteur Markdown maison non sanitizé.

### Front
- ~70 handlers inline dans des template strings (classe de bugs récurrente, cf. commit « fix SyntaxError onclick ») ; full re-render 3 s avec rustines anti-perte de saisie ; duplication (2 explorateurs d'artefacts, 2 calculs de vagues, 4 rendus de notification) ; zéro test, zéro build, gestion d'erreur par `alert()` ; a11y quasi absente ; i18n en dur.

### Robustesse / exploitation
- `events.content` non borné, pas de rétention/VACUUM → croissance de la DB.
- Auto-planification sans plafond (boucle de coût possible).
- Tâches `in_progress` orphelines après crash réparées seulement par retry manuel.
- **Aucune sauvegarde des données de prod** ; pas de healthcheck ni d'observabilité dans compose ; egress réseau du conteneur non restreint (connu/assumé).

---

## 7. Dépendances & intégrations

- Python 3.12+ : `anthropic>=0.92.0` (beta compaction `compact-2026-01-12`, thinking adaptatif, effort), `fastapi`, `uvicorn[standard]`, `python-dotenv`, `python-multipart`, `httpx`. Stdlib : sqlite3, smtplib, hmac.
- Intégrations : API Anthropic (Messages + `/v1/models` + outils serveur `web_search_20260209`/`web_fetch_20260209` — versions datées, à surveiller), endpoints OpenAI-compatibles, SMTP STARTTLS, Telegram Bot API.
- Front : zéro dépendance externe (pas de CDN).

---

## 8. Recommandations avant refonte (ordre suggéré)

1. **Mettre en place une sauvegarde automatique du volume `swarm-data`** (cron + tar, cf. DEPLOY.md §sauvegarde) — préalable non négociable à toute migration.
2. Écrire le **script de migration à ids stables** : DB + mapping workdirs/fichiers ressources + normalisation des références par nom → par id.
3. Extraire/chiffrer les secrets stockés en DB.
4. Sécuriser le webhook Telegram (secret_token) et confiner `read_file`/`write_file` au workdir.
5. Décider du socle : SQLite WAL + accès transactionnel vs Postgres ; supprimer l'état global si multi-process visé.
6. Refonte front : modules + build, rendu déclaratif, couche API unifiée (fin du token en query), SSE/WebSocket à la place du polling 3 s, sanitizer Markdown éprouvé — en utilisant le §5 comme checklist de non-régression.
