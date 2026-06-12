# 🐝 Essaim d'agents autonomes

Plateforme de travail d'agents IA autonomes (API Claude), avec :

- **Service back** (FastAPI + SQLite) : registre d'agents, cycle de sessions, planificateur, délégation inter-agents, messagerie, mémoire persistante par agent.
- **Service front** : tableau de bord de supervision en temps réel (agents, sessions, flux d'événements, tâches).

Chaque agent est configurable (nom, mission générique, modèle, effort) et **100 % autonome** : shell complet sur la machine hôte (installer des programmes, créer/démarrer/arrêter des services, se créer ses propres outils), fichiers, recherche/navigation web, envoi d'e-mails, délégation aux autres agents.

## Fonctionnalités

- **Thème clair, responsive** (utilisable sur mobile/tablette) et navigation par onglets : Tableau de bord · Supervision · Missions · Ressources & artefacts · Réglages, plus une **cloche de notifications** dans la barre de menu (badge + panneau déroulant).
- **Tableau de bord** : indicateurs globaux (agents, sessions, tâches, missions, notifications), état de chaque agent en un coup d'œil, et **consommation de tokens par agent, par provider et par mission** (elle est aussi visible au niveau de chaque session et de chaque tâche).
- **Providers LLM multiples** (onglet Réglages) : déclare autant de fournisseurs que nécessaire, de deux types — **Anthropic ou compatible** (Messages API ; URL/clé personnalisées, option « fonctionnalités natives » : thinking adaptatif, effort, compaction, cache) et **OpenAI ou compatible** (Chat Completions). Un provider est **par défaut** ; chaque **agent peut être rattaché à un provider** précis. Les erreurs transitoires (429/5xx/réseau) sont **réessayées automatiquement**.
- **Supervision claire des états** : chaque agent affiche son état synthétique — ▶ en cours · ⏸ en pause · ❓ attend une réponse utilisateur · ⏱ session planifiée · inactif. Les **tuiles rétractables** (agents, missions, tâches, panneaux) gardent la consultation lisible quand les missions et agents se multiplient.
- **Alertes & sollicitations utilisateur** : l'agent peut alerter (`notify_user`) ou **poser une question** (`ask_user`) qui apparaît dans le panneau de notifications. Tu réponds depuis la cloche et la réponse lui est transmise en contexte au début de sa prochaine session (reprise automatique dès la réponse).
- **Ressources** à trois niveaux : **mutualisées** (partagées entre agents), **liées à un agent**, **liées à une tâche**. Fichiers (upload), liens, notes — créées par l'utilisateur (UI) ou par les agents (`save_resource`). Accès agent via `list_resources`/`read_resource`.
- **Explorateur de ressources et d'artefacts** côte à côte : recherche, tri (date/nom/taille), **filtres par agent et par mission**, aperçu (texte/markdown/JSON/image), téléchargement/export. Les artefacts sont les fichiers produits par un agent dans son workdir (`deliverables/`, `library/`, `memory/`).
- **Mémoire structurée persistante** (`memory_set`/`memory_get`/`memory_list`/`memory_delete`), en scope **agent** (général) ou **tâche**. Réinjectée de façon **compacte** à chaque session pour **éviter l'explosion du contexte**, en complément de la compaction côté API et d'un garde-fou client qui élide les anciens résultats d'outils.
- **Missions (agent superviseur)** : l'utilisateur décrit une mission ; un **superviseur** comprend le besoin et propose un **plan** décomposé en tâches (certaines **en parallèle**, d'autres **séquentielles** avec dépendances). Après validation, les tâches sont créées dans un **projet commun** qui préserve leurs liens. Une tâche dépendante n'est lancée qu'une fois ses prérequis terminés, et **reçoit automatiquement le résultat** des tâches dont elle dépend (le superviseur ou l'agent précédent peut aussi lui attacher des ressources via `save_resource` en scope `task`). Les missions terminées sont **archivables mais restent consultables**, et peuvent aussi être **supprimées définitivement** (avec leurs tâches) tant qu'aucune tâche ne s'exécute.
- **Cycle de vie des agents** : un agent peut être **mis en pause** (le planificateur ne lance plus ses sessions, réactivable à tout moment) ou **supprimé définitivement** (historique purgé, tâches de mission ouvertes annulées en cascade ; ses fichiers restent sur disque et ses tâches terminées restent visibles dans l'historique des missions).

## Robustesse & autonomie

- **Échec de dépendance géré** : si une tâche échoue, ses tâches en aval sont **annulées en cascade** (plutôt que bloquées indéfiniment), la mission passe en **« needs_attention »** et une **notification** est levée.
- **Tâches réellement terminées** : à la clôture d'une session, l'agent peut déclarer les tâches **non terminées** (`unfinished_task_ids` de `finish_session`) — elles restent ouvertes au lieu d'être marquées « done » à tort quand une session de continuation est planifiée.
- **Handoff de délégation** : à la fin d'une tâche déléguée, l'agent qui l'a confiée **reçoit le résultat** par message.
- **Budgets de tokens** : plafond par session (cumul in+out) configurable par agent ; au dépassement la session s'arrête proprement et alerte l'utilisateur.
- **Anti-stagnation** : une session qui enchaîne trop d'erreurs d'outil ou répète le même appel à l'identique est **stoppée** et signalée (évite les boucles coûteuses).
- **Sous-agents en contexte** (`spawn_subagent`) : fan-out rapide d'une sous-tâche bornée à un sous-agent **économique** (Haiku) qui renvoie son résultat **inline** — pour paralléliser recherche/traitement sans attendre une session future.
- **Registre de services/ports** (`register_service`/`list_services`/`unregister_service`) : l'hôte étant partagé, les agents déclarent leurs services et vérifient les ports pour **éviter les collisions** ; visible dans le détail de l'agent.
- **Gestion du contexte** : chaque résultat d'outil est borné dès l'insertion + élision anticipée des anciens résultats, en plus de la compaction API.
- **Sécurité** : allowlist d'envoi d'e-mail, denylist de commandes shell destructrices, et consigne anti-injection (le contenu web/fichier est traité comme des données, pas des instructions).
- **Isolation (prod)** : `Dockerfile` + `docker-compose.yml` fournis — la plateforme (et donc le shell des agents) tourne **dans un conteneur**, sous un utilisateur non-root, avec limites mémoire/PID, protégeant la machine hôte.

> Note : le conteneur isole les agents **de l'hôte**, pas les agents **entre eux** (ils partagent le conteneur). D'où le registre de services pour éviter les collisions. Une isolation par agent (un conteneur par session) serait l'étape suivante.

## Le mécanisme de session

Chaque agent travaille par sessions, selon le protocole :

```
qui je suis → contexte initial (mémoire + rapports précédents + tâches + messages)
→ objectif de session → ressources → exécution (itérations outillées)
→ livrables → rapport de mission → préparation de la prochaine session
→ planification à échéance optimale (choisie par l'agent) → ...
```

- L'agent clôt sa session avec l'outil `finish_session` : rapport, livrables, objectif suivant, échéance (`next_run_minutes`).
- Le planificateur relance automatiquement les sessions à échéance, et crée une session immédiate quand un agent libre reçoit une tâche.
- Une session en attente peut être **lancée manuellement** (bouton « ▶ Lancer ») sans attendre son échéance, avec un **commentaire optionnel** injecté au début du contexte de la session (« Note de l'utilisateur ») — utile quand l'agent s'est planifié trop loin ou pour le réorienter.
- Mémoire par agent dans `data/agents/<id>_<nom>/` : `memory/MEMORY.md` (mémoire de long terme tenue par l'agent), `memory/sessions.log` (journal automatique des rapports), `library/` (outils et connaissances que l'agent se constitue), `deliverables/` (livrables).
- Longues sessions : compaction de contexte côté API (beta `compact-2026-01-12`) + cache de prompt → l'agent peut itérer longtemps sans saturer son contexte.

## Démarrage avec Docker (recommandé en prod — isole l'hôte)

```bash
cp .env.example .env   # renseigner ANTHROPIC_API_KEY (+ SMTP_*, EMAIL_ALLOWLIST si besoin)
docker compose up -d --build
```
Tableau de bord : http://127.0.0.1:8000 — les agents exécutent leur shell **dans le conteneur**, pas sur l'hôte.

## Démarrage (dev Windows)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # puis renseigner ANTHROPIC_API_KEY
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Tableau de bord : http://127.0.0.1:8000

Sous Windows, l'outil `shell` des agents exécute du **PowerShell** ; sous Linux, du **bash**. Le prompt système informe l'agent de la plateforme courante.

## Déploiement Ubuntu (production)

```bash
sudo apt update && sudo apt install -y python3-venv
git clone <repo> /opt/agent-swarm && cd /opt/agent-swarm
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # renseigner ANTHROPIC_API_KEY (+ SMTP_* si besoin)
```

Service systemd `/etc/systemd/system/agent-swarm.service` :

```ini
[Unit]
Description=Essaim d'agents autonomes
After=network-online.target

[Service]
WorkingDirectory=/opt/agent-swarm
ExecStart=/opt/agent-swarm/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
# Les agents ont un accès shell complet : exécuter sous un utilisateur dédié.
User=swarm

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agent-swarm
```

> ⚠️ **Sécurité** : les agents disposent d'un accès shell illimité (c'est voulu — autonomie totale). Déployer sur une **VM ou un conteneur dédié**, sous un utilisateur dédié, et ne pas exposer le port 8000 publiquement sans authentification (reverse proxy + auth).

## Utilisation

1. **Créer un agent** : nom, description (visible des autres agents), mission générique (prompt), modèle, effort.
2. **Lui confier une tâche** (`+ Tâche`) → le planificateur déclenche une session immédiate si l'agent est libre. Les agents peuvent aussi se déléguer des tâches entre eux.
3. **Ou planifier une session** (`+ Session`) avec un objectif et une date de démarrage.
4. **Superviser** : flux en direct (réflexion, texte, appels d'outils, résultats), rapports de mission, consommation de tokens, interruption d'une session en cours, pause/reprise d'un agent.

## API (extraits)

| Méthode | Route | Rôle |
|---|---|---|
| `GET/POST` | `/api/agents` | lister / créer des agents |
| `PATCH` | `/api/agents/{id}` | modifier mission, modèle, effort, provider… |
| `POST` | `/api/agents/{id}/pause` · `/resume` | désactiver / réactiver (le planificateur l'ignore en pause) |
| `DELETE` | `/api/agents/{id}` | suppression définitive (historique purgé, tâches ouvertes annulées, workdir conservé) |
| `GET/POST` | `/api/agents/{id}/tasks` | tâches confiées |
| `GET/POST` | `/api/agents/{id}/sessions` | sessions (planification manuelle possible) |
| `POST` | `/api/sessions/{id}/run-now` | lancer immédiatement une session en attente (commentaire optionnel injecté en contexte) |
| `POST` | `/api/sessions/{id}/interrupt` | interrompre une session |
| `GET` | `/api/sessions/{id}/events?after=N` | flux d'événements (polling incrémental) |
| `POST` | `/api/projects` | décrire une mission → le superviseur propose un plan |
| `GET` | `/api/projects?include_archived=` | lister les missions (archivées incluses si demandé) |
| `POST` | `/api/projects/{id}/approve` · `/replan` · `/archive` | valider et lancer · régénérer · archiver |
| `DELETE` | `/api/projects/{id}` | suppression définitive (tâches et ressources liées supprimées ; refusée si une tâche s'exécute) |
| `GET/POST` | `/api/providers` | lister / déclarer des providers LLM |
| `PATCH/DELETE` | `/api/providers/{id}` · `POST …/default` | modifier · supprimer · définir par défaut |
| `GET` | `/api/stats/tokens` | consommation par agent / provider / mission |
| `GET` | `/api/resources?agent_id=&project_id=` | ressources filtrées par agent / mission |
| `GET` | `/api/overview` | indicateurs globaux |

## Architecture

```
backend/
  config.py     # configuration (.env)
  db.py         # SQLite : agents, sessions, tâches, messages, événements, providers, ressources…
  providers.py  # providers LLM typés (anthropic / openai compatibles), provider par agent
  tools.py      # outils des agents (shell, fichiers, délégation, e-mail…) + outils serveur (web)
  planner.py    # agent superviseur des missions (plan en tâches parallèles/séquentielles)
  runtime.py    # boucle agentique d'une session (Claude API, thinking adaptatif, compaction, cache)
  scheduler.py  # lancement des sessions à échéance + sessions immédiates sur tâches
  app.py        # API REST + service du front
frontend/
  index.html    # tableau de bord de supervision
data/           # créé au premier lancement : swarm.db + workdirs des agents
```
