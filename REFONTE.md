# Refonte agent-swarm — Spécification cible

*Rédigée le 2026-07-04, sur la base de l'audit (`AUDIT.md`) et des évolutions demandées. Statut : proposition à valider.*

## Décisions actées

| Sujet | Décision |
|---|---|
| Base de données | **PostgreSQL** (ajouté au docker-compose), migration one-shot depuis `swarm.db` |
| Front | **Vue 3 + Vite** (réécriture complète, `AUDIT.md §5` = checklist de non-régression) |
| Parallélisme agent | **1 session = 1 tâche** ; un agent mène N tâches de front (plafond configurable par agent, défaut 1) |
| Comptes | **Auto-inscription + validation admin** (compte `pending` jusqu'à approbation) |
| Mémoire des agents système | **Scission stricte par utilisateur** |
| Quotas de tokens par utilisateur | **Oui, dès la v1** |
| Porosité des liens de tâches | **Héritage transitif de toute la chaîne** (accès à la demande, cf. §4) |
| Recherche globale | **Dès la v1** (remplace l'Explorateur) |
| Périmètre | **Une seule bascule** (backend + front + modèle tâche ensemble) |

---

## 1. Utilisateurs & authentification (évolution n°1)

### Modèle
- Table `users` : id, email UNIQUE, password_hash (**argon2id**), display_name, role (`admin` | `user`), status (`pending` | `active` | `disabled`), created_at.
- Inscription : formulaire public (email + mdp + nom) → compte `pending` → notification à l'admin → activation dans l'UI admin. L'admin peut aussi créer/désactiver des comptes directement.
- Connexion : email + mot de passe → token signé (JWT court + refresh, ou token opaque en base). Le changement de mot de passe invalide les tokens de l'utilisateur **seulement** (contrairement au token global actuel).
- Réinitialisation de mot de passe par email (le SMTP existe déjà).
- **Plus de `ADMIN_PASSWORD` partagé** : le premier compte créé (ou un compte seedé à la migration) est admin.

### Cloisonnement — côté serveur, pas côté requête
C'est le changement de fond par rapport à l'actuel (où `profile_id` n'est qu'un filtre d'affichage) :
- Chaque ressource possédée porte `owner_user_id` : agents, missions, tâches, notifications, canaux, ressources.
- **Toute** route vérifie l'appartenance (dépendance FastAPI `current_user` + contrôle d'ownership), lecture **et** écriture. Un `user` ne voit et ne touche que ses objets + les objets système en lecture. L'`admin` voit tout.
- Les canaux de notification (email, Telegram) deviennent **par utilisateur** ; les questions/alertes d'une tâche sont routées vers le propriétaire de la tâche. L'email du compte est le canal par défaut.

## 2. Providers mutualisés (évolution n°2)

- Les providers restent **globaux** : CRUD, ordre de fallback, limites de fenêtres glissantes → **admin uniquement**.
- Les utilisateurs les **consomment** : au moment de créer/éditer un agent, ils choisissent parmi les providers existants et leurs modèles (lecture seule ; les clés ne sont jamais exposées).
- Le superviseur de missions (agent système) utilise le provider par défaut, y compris pour paramétrer les agents qu'il crée.
- **Attribution de la consommation** : chaque session enregistre `provider_id` (plus le nom) **et** `user_id` → le dashboard admin ajoute « tokens par utilisateur » ; chaque utilisateur voit sa propre consommation.
- **Quotas de tokens par utilisateur (décidé, v1)** : plafonds par fenêtres glissantes (X tokens / Y heures + Z tokens / W jours, mêmes fenêtres que les limites de provider), définis par l'admin par utilisateur, 0 = illimité. Contrôle au lancement de session et en cours de session ; au dépassement, la session s'arrête proprement, la tâche repasse en attente et l'utilisateur est notifié. Jauges visibles : par l'utilisateur (sa conso/son quota) et par l'admin (tous).

## 3. Agents système (évolution n°3)

- `agents.owner_user_id = NULL` ⇒ agent système : **visible et utilisable par tous**, paramétrable (prompt, modèle, provider, plafonds) **par l'admin uniquement**, dans un onglet dédié de l'administration.
- Le **superviseur de missions** devient lui-même un agent système explicite (aujourd'hui c'est du code caché dans `planner.py`) : son prompt et son modèle deviennent paramétrables par l'admin.
- Isolation entre utilisateurs : garantie par le modèle centré tâche (§4) — les productions d'un agent système vivent dans les tâches, chacune appartenant à un utilisateur.
- **Mémoire scindée par utilisateur (décidé)** : pour un agent système, toute la mémoire de niveau agent est cloisonnée par utilisateur — `memories.user_id` en base, et sur disque `data/agents/<id>/memory/users/<user_id>/` (MEMORY.md + sessions.log par utilisateur). Une session ne charge que la mémoire de l'utilisateur propriétaire de la tâche. Pour un agent dédié (owner non NULL), `user_id` = son propriétaire, comportement inchangé. Seule la `library/` (outillage technique que l'agent se constitue) reste commune, avec consigne système de n'y stocker aucune donnée utilisateur — elle est consultable par l'admin.

## 4. Recentrage sur la tâche (évolution n°4) — le cœur de la refonte

### Principe
- **Mission** = le besoin décrit par l'utilisateur ; le superviseur en dérive la typologie des agents et le plan.
- **Tâche** = l'unité de travail confiée à un agent, et **l'unité de supervision** : description, statut, ressources, artefacts, sessions, consommation — tout est rattaché à la tâche, sans mélange.
- **Session** = une exécution bornée au service d'**une seule tâche** (fini les sessions qui absorbent toutes les tâches prêtes de l'agent).
- **Agent** = la configuration (prompt, modèle, provider, plafonds) + ses connaissances durables (mémoire, bibliothèque). La supervision d'agent ne montre plus le travail, elle montre le paramétrage et la charge (tâches en cours).

### Modèle de données
- `tasks` : id, title, description, status, agent_id, mission_id (nullable), **owner_user_id**, created_by (`user` | `agent:<id>` | `supervisor` | `self`), tokens, dates.
- `sessions` : id, **task_id NOT NULL**, agent_id, number (séquence **par tâche**), objective, status, report, tokens, provider_id, dates. Une tâche peut enchaîner plusieurs sessions (continuation planifiée par l'agent via `finish_session`).
- **`task_links`** (task_id, linked_task_id, kind `depends_on` | `follow_up`) : remplace le champ JSON `depends_on` **et** porte la nouvelle « porosité ». **Héritage transitif (décidé)** : une tâche a accès en lecture aux ressources et artefacts de **toute sa chaîne d'ascendance** (fermeture transitive des liens, cycles interdits à la création). Pour ne pas saturer le contexte, l'accès est **à la demande** : le contexte initial de session n'injecte que la liste des tâches ancêtres (id, titre, statut, agent) ; l'agent explore avec `list_task_files(task_id)` / `read_task_file(task_id, path)` / `list_resources(task_id=...)`, autorisés sur tout ancêtre.
- **Workdir par tâche** : `data/tasks/<task_id>/` (artefacts + livrables de la tâche). L'agent conserve `data/agents/<agent_id>/` pour `memory/` et `library/` uniquement (lecture/écriture pour lui, ses outils fichiers sont **confinés** à : workdir de sa tâche courante + sa library + lecture des tâches liées).
- Ressources : scope `shared` (admin), `user` (nouveauté : propres à un utilisateur, visibles de toutes ses tâches), `task`. Le scope `agent` disparaît au profit de `user` et `task` (les ressources « d'agent » actuelles migrent vers le propriétaire de l'agent).

### Travail autonome = tâche aussi
Aujourd'hui un agent s'auto-planifie des sessions hors de toute tâche (`next_objective`) — ce serait un angle mort de supervision dans le nouveau modèle. Proposition : **tout passe par une tâche**. Quand un agent veut poursuivre un travail de fond, il crée une tâche `created_by=self` (éventuellement liée à la précédente). La supervision devient exhaustive : rien ne s'exécute sans tâche visible.

### Ordonnanceur
- File par agent ; un agent exécute jusqu'à `max_parallel_tasks` sessions simultanées (défaut 1, réglable par agent — utile pour les agents système sollicités par plusieurs utilisateurs).
- Plafond global `MAX_CONCURRENT_SESSIONS` conservé.
- Une question `ask_user` bloque **sa tâche**, plus tout l'agent : les autres tâches de l'agent continuent.

### Supervision (UI)
- **Vue Agents** : liste (miens + système), paramétrage, état de charge (n tâches en cours / en file), mémoire & bibliothèque, pause/reprise.
- **Vue Tâches** : la vraie vue de suivi — liste/filtres (statut, agent, mission), détail par tâche : description, statut, **ressources et artefacts de la tâche**, tâches liées (amont/aval, navigables), sessions avec flux d'événements en direct, rapport, tokens, actions (relancer, interrompre, répondre à une question).
- **Vue Missions** : inchangée dans l'esprit (plan, vagues, progression), mais chaque tâche pointe vers la vue Tâches.
- **Administration** (admin) : utilisateurs (validation des inscriptions, quotas), providers, agents système, canaux globaux, tokens par utilisateur.

## 5. Suppression de l'Explorateur (évolution n°5)

- L'onglet Explorateur disparaît : les artefacts se consultent **dans la tâche** (et la bibliothèque dans l'agent), les ressources dans la tâche / le profil utilisateur.
- On conserve le composant d'**aperçu universel** (image / Markdown source-rendu / texte / téléchargement), réutilisé partout.
- **Recherche globale (décidé, v1)** : champ de recherche dans le header (admin : tout ; utilisateur : son périmètre) couvrant tâches (titre + description, full-text PostgreSQL), missions, ressources (nom + description) et noms de fichiers d'artefacts ; résultats groupés par type, clic → la tâche/l'agent concerné avec l'aperçu universel.

## 6. Améliorations transverses proposées (point 6)

**Sécurité (corrige les faiblesses de l'audit)**
1. Mots de passe hashés argon2id ; clés API des providers et tokens Telegram **chiffrés en base** (clé de chiffrement dans `.env`).
2. Webhook Telegram vérifié par `secret_token` ; rate-limit sur login/inscription.
3. Fin du token en query string (les images/téléchargements passent par des URL signées de courte durée ou cookie).
4. Outils fichiers des agents confinés (cf. §4) ; denylist shell conservée mais assumée comme défense faible : l'isolation Docker reste la vraie barrière.

**Temps réel & performance**
5. **SSE** (Server-Sent Events) pour le flux des sessions et les compteurs — remplace le polling 3 s full re-render. FastAPI le gère nativement ; fallback polling conservé.
6. Rétention des `events` : purge/compression des événements des sessions terminées au-delà de N jours (la table est la principale source de croissance de la base).

**Robustesse & exploitation**
7. Transactions réelles (SQLAlchemy + Alembic pour les migrations de schéma — fini le `_migrate` artisanal).
8. Réparation automatique des tâches orphelines au démarrage (aujourd'hui : retry manuel).
9. Plafond d'auto-replanification (un agent ne peut pas s'auto-créer des tâches indéfiniment sans validation — garde-fou anti-boucle de coût, seuil réglable).
10. Healthcheck compose + endpoint `/healthz` ; logs structurés.
11. Sauvegarde : le cron (déjà écrit) évolue vers `pg_dump` + tar des workdirs, avec copie hors serveur.

## 7. Architecture technique cible

```
backend/            FastAPI (API v2, OpenAPI), SQLAlchemy + Alembic, PostgreSQL
  auth/             users, tokens, inscriptions, rôles
  domain/           agents, tasks, sessions, missions, providers, resources, notifications
  runtime/          boucle agentique (1 session = 1 tâche), outils confinés, sous-agents
  scheduler/        files par agent, parallélisme plafonné, reprises
  realtime/         SSE (flux sessions, compteurs)
frontend/           Vue 3 + Vite (+ TypeScript), composants par vue,
                    client API généré depuis OpenAPI, store léger (Pinia)
docker-compose      services : app + postgres (+ volumes swarm-data, pgdata)
```

## 8. Plan de migration (ids stables)

Ordre d'exécution — la prod continue de tourner jusqu'à la bascule :

1. **Sauvegarde fraîche** (script en place) + copie hors serveur.
2. **Script de migration** SQLite → PostgreSQL (Python, idempotent, testé sur une copie du dump de prod) :
   - ids conservés à l'identique partout (contrainte : noms de dossiers workdir et fichiers ressources encodent les ids) ;
   - `profiles` → `users` : chaque profil devient un compte (email et mot de passe initial à fournir dans un petit fichier de mapping ; le profil « Par défaut » → compte admin) ;
   - `agents.profile_id` → `owner_user_id` (NULL reste système) ;
   - références par **nom** → par **id** (`sessions.provider` → `provider_id`, `tasks.origin` → `created_by` normalisé, `messages.from_agent` → id) ;
   - `tasks.depends_on` (JSON) → lignes `task_links(kind=depends_on)` ;
   - sessions historiques multi-tâches : rattachées à leur tâche quand `tasks.session_id` le permet, sinon marquées `legacy` (consultables, non rattachées) ;
   - ressources `scope=agent` → `scope=user` (propriétaire de l'agent) ;
   - nettoyage des orphelins (pas de FK aujourd'hui) avec rapport de ce qui est purgé ;
   - secrets (clés providers, tokens Telegram, SMTP) → chiffrés à l'import.
3. **Workdirs** : `data/agents/<id>_<nom>/` → `data/agents/<id>/` (mapping dans le script) ; les artefacts existants restent au niveau agent, exposés dans l'UI comme « artefacts hérités » de l'agent (impossible de les ré-attribuer par tâche a posteriori) ; `deliverables/` des agents rattachés à une tâche `legacy` par agent si on veut les voir côté tâches.
4. **Bascule** : déploiement de la nouvelle stack en parallèle sur le VPS (autre port), migration des données, tests, puis bascule nginx. Retour arrière = repointer nginx (l'ancienne stack et ses données ne sont pas touchées).

## 9. Schéma PostgreSQL cible

Conventions : PK `id BIGINT GENERATED ALWAYS AS IDENTITY` (les ids migrés depuis SQLite sont insérés tels quels puis la séquence est réalignée), horodatages `TIMESTAMPTZ`, JSON en `JSONB`, **FK réelles avec `ON DELETE` explicites**, tout champ secret chiffré applicativement (préfixe `_enc`).

```
users               id, email CITEXT UNIQUE, password_hash, display_name,
                    role ('admin'|'user'), status ('pending'|'active'|'disabled'),
                    quota_short_tokens, quota_short_hours, quota_long_tokens, quota_long_days,
                    created_at
user_tokens         id, user_id FK→users CASCADE, token_hash UNIQUE, expires_at, created_at
                    (tokens opaques révocables ; le reset de mdp purge les tokens de l'utilisateur)
password_resets     id, user_id FK, token_hash, expires_at, used_at

providers           id, name UNIQUE, ptype ('anthropic'|'openai'), base_url,
                    api_key_enc, default_model, models JSONB, native_features BOOL,
                    is_default BOOL, priority, limit_short_tokens, limit_short_hours,
                    limit_long_tokens, limit_long_days, created_at

agents              id, owner_user_id FK→users NULL (NULL = agent système),
                    name, description, mission_prompt, category,
                    provider_id FK→providers NULL (NULL = défaut), model, effort,
                    max_iterations, session_token_budget, max_parallel_tasks DEFAULT 1,
                    paused BOOL, created_at
                    UNIQUE (COALESCE(owner_user_id,0), name)   -- unicité du nom par périmètre
                    -- plus de colonne status : l'état (idle/running/n tâches) se dérive des sessions

missions            id, owner_user_id FK→users, title, mission, summary, plan JSONB,
                    status ('proposed'|'running'|'completed'|'needs_attention'|'archived'),
                    input_tokens, output_tokens, created_at, updated_at

tasks               id, mission_id FK→missions NULL, agent_id FK→agents,
                    owner_user_id FK→users, title, description, result,
                    status ('pending'|'ready'|'in_progress'|'waiting_user'|'done'|'failed'|'cancelled'),
                    created_by ('user'|'supervisor'|'self'|'agent') + created_by_agent_id FK NULL,
                    input_tokens, output_tokens, created_at, completed_at
task_links          task_id FK→tasks CASCADE, linked_task_id FK→tasks,
                    kind ('depends_on'|'follow_up'), PK (task_id, linked_task_id, kind)
                    -- garde anti-cycle à l'insertion (requête récursive)

sessions            id, task_id FK→tasks NOT NULL, agent_id FK→agents,
                    number (séquence par tâche), objective,
                    status ('planned'|'running'|'completed'|'failed'|'interrupted'),
                    scheduled_at, started_at, ended_at, report, deliverables JSONB,
                    error, provider_id FK→providers NULL, user_note,
                    input_tokens, output_tokens
events              id BIGSERIAL, session_id FK→sessions CASCADE, ts,
                    type ('status'|'thinking'|'text'|'tool_use'|'tool_result'|'error'|'usage'),
                    content TEXT — index (session_id, id) ; purge > N jours après clôture

token_usage         id, ts, user_id FK, provider_id FK, agent_id FK, task_id FK, session_id FK,
                    input_tokens, output_tokens
                    -- une ligne par appel LLM : sert les jauges provider ET les quotas
                    -- utilisateur (fenêtres glissantes) sans agréger events ; index (user_id, ts),
                    -- (provider_id, ts)

messages            id, from_agent_id FK→agents NULL (NULL = système), to_agent_id FK→agents,
                    task_id FK NULL (contexte du handoff), content, read BOOL, created_at
notifications       id, user_id FK→users (destinataire), agent_id FK, task_id FK NULL,
                    session_id FK NULL, type ('alert'|'question'),
                    status ('open'|'answered'|'dismissed'), content, response,
                    external_ids JSONB, channel_dispatched BOOL, created_at, answered_at
notification_channels id, owner_user_id FK→users, name, type ('email'|'telegram'),
                    config_enc, use_for_alerts BOOL, use_for_questions BOOL,
                    enabled BOOL, created_at
                    -- remplace agent_channels : le routage est par utilisateur, plus par agent

resources           id, scope ('shared'|'user'|'task'), owner_user_id FK NULL,
                    task_id FK NULL, name, kind ('file'|'note'|'link'), filename,
                    content, description, size, created_by, created_at
memories            id, agent_id FK→agents, user_id FK→users NULL, scope ('agent'|'task'),
                    task_id FK NULL, mkey, mvalue, updated_at,
                    UNIQUE (agent_id, COALESCE(user_id,0), scope, COALESCE(task_id,0), mkey)
services            id, agent_id FK→agents, name, port, command, status, notes,
                    created_at, updated_at
settings            key PK, value JSONB (smtp_config chiffré, paramètres globaux)
```

Arborescence disque cible :

```
data/
  agents/<agent_id>/
    memory/users/<user_id>/MEMORY.md + sessions.log   # scission stricte
    library/                                          # commune, admin-visible
  tasks/<task_id>/                                    # workdir de la tâche (artefacts, livrables)
  resources/<resource_id>_<nom>
```

## 10. Découpage en chantiers (une seule bascule)

Le développement se fait sur une branche/arborescence v2 ; la prod actuelle n'est pas touchée avant la bascule. Ordre à dépendances minimales :

| # | Chantier | Contenu | Dépend de |
|---|---|---|---|
| 1 | **Socle backend** | Projet FastAPI v2, SQLAlchemy + Alembic, Postgres dans compose, chiffrement des secrets, `/healthz`, logs structurés | — |
| 2 | **Auth & comptes** | users/rôles, inscription + validation, tokens révocables, reset mdp, middleware d'ownership | 1 |
| 3 | **Domaine cœur** | agents (dédiés/système), providers (admin), missions/superviseur, tasks + task_links (anti-cycle, fermeture transitive), quotas & token_usage | 2 |
| 4 | **Runtime & scheduler** | boucle 1 session = 1 tâche, files par agent + `max_parallel_tasks`, outils confinés (workdir tâche + library + ancêtres en lecture), mémoire scindée, tâches `self`, blocage `ask_user` par tâche | 3 |
| 5 | **Notifications & temps réel** | canaux par utilisateur (email/Telegram sécurisé), routage vers le propriétaire, SSE (flux sessions + compteurs), rétention events | 3 |
| 6 | **Front Vue 3** | shell + auth, vues Agents / Tâches / Missions / Dashboard / Admin, aperçu universel, recherche globale, jauges quotas — checklist `AUDIT.md §5` adaptée au modèle tâche | 2 (puis 3-5 au fil de l'eau) |
| 7 | **Script de migration** | SQLite→Postgres ids stables (§8), renommage workdirs, scission mémoire, mapping profils→comptes, rapport d'orphelins ; **testé sur le dump de prod** | 3 |
| 8 | **Bascule** | déploiement parallèle sur le VPS (autre port), migration réelle, recette avec la checklist, bascule nginx, `pg_dump` dans le cron de sauvegarde, retour arrière documenté | tout |

Jalons de validation intermédiaires proposés : fin du chantier 4 (démo : deux utilisateurs, un agent système, tâches liées avec porosité) et fin du chantier 7 (migration à blanc du dump de prod, zéro perte).
