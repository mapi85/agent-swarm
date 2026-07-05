# Runbook de bascule v1 → v2 (production)

Séquence de mise en production de la v2. **À exécuter par l'administrateur** (sudo + panneau IONOS).
La v1 et son volume ne sont **jamais modifiés** → retour arrière possible à tout moment.

> Contexte serveur : v1 dans `~/agent-swarm` (compose `sudo docker compose`, app sur `127.0.0.1:8000`,
> nginx `agents.mapi85.fr:8080`). v2 dans `~/agent-swarm-v2` (compose projet `agent-swarm-v2`,
> app sur `127.0.0.1:8001`, postgres + volume `agent-swarm-v2_swarm-data`).

---

## ⚠️ Avant tout — 3 points critiques

1. **`SECRET_ENCRYPTION_KEY` (fichier `~/agent-swarm-v2/.env`)** chiffre TOUS les secrets en base
   (clés API des providers, tokens Telegram, mot de passe SMTP). **Sauvegarde-la hors serveur**
   et ne la change JAMAIS après la migration : la perdre = perdre tous les secrets chiffrés.
2. **Ne jamais laisser tourner les agents de v1 ET v2 en même temps** (double exécution, coûts,
   conflits). La séquence ci-dessous arrête la v1 avant de réactiver les agents en v2.
3. **Comptes migrés** : emails placeholder (`<slug>@agents.mapi85.fr`) et mots de passe aléatoires
   (imprimés par `migrate_v1`). À corriger avant que les vrais utilisateurs se connectent
   (Administration → Utilisateurs, ou SQL).

---

## 1. Fenêtre de maintenance + sauvegarde fraîche

```bash
# Sauvegarde de la v1 (base + workdirs) juste avant la bascule
~/backups/backup-swarm-data.sh
# (idéalement : copier ~/backups/data/ hors du serveur)
```

## 2. Geler la v1  — ⚠️ ÉTAPE CLÉ CONTRE LA DÉRIVE

La v1 tourne encore : sa base ET ses fichiers grossissent en continu (constaté :
~4000 fichiers de livrables générés en quelques heures). **Le dump et les fichiers
doivent être pris sur une v1 ARRÊTÉE**, sinon base et fichiers seront incohérents.

```bash
cd ~/agent-swarm
sudo docker compose stop        # arrête l'app v1 : agents stoppés, base + volume figés
```
À partir d'ici, plus rien ne bouge côté v1 : le dump (étape 3) et la migration des
fichiers (étape 4b) portent sur le même instantané cohérent.

## 3. Extraire un dump v1 frais (v1 arrêtée → volume stable)

```bash
mkdir -p ~/mig
docker run --rm -v agent-swarm_swarm-data:/v1:ro -v ~/mig:/out alpine \
  cp /v1/swarm.db /out/v1.db
```
> Le dump utilisé lors des répétitions (4 juillet) est **périmé** : utilise TOUJOURS
> ce dump frais pour la bascule réelle.

## 4. Migrer vers la v2 (base + fichiers)

```bash
cd ~/agent-swarm-v2
git pull                                   # dernier code v2
docker compose -f docker-compose.v2.yml -p agent-swarm-v2 up -d --build db
docker compose -f docker-compose.v2.yml -p agent-swarm-v2 stop app   # scheduler éteint pendant la migration

# 4a. Base : --force VIDE la cible puis réimporte (note le rapport + les mots de passe imprimés !)
docker compose -f docker-compose.v2.yml -p agent-swarm-v2 run --rm \
  -v ~/mig/v1.db:/tmp/v1.db:ro app python -m server.migrate_v1 /tmp/v1.db --force

# 4b. Fichiers : workdirs + ressources (le MÊME volume v1 figé qu'à l'étape 2/3)
docker compose -f docker-compose.v2.yml -p agent-swarm-v2 run --rm \
  -v agent-swarm_swarm-data:/v1:ro app python -m server.migrate_files /v1

# 4c. VÉRIFICATION — PORTE OBLIGATOIRE : ne pas continuer si le verdict n'est pas ✅
docker compose -f docker-compose.v2.yml -p agent-swarm-v2 run --rm \
  -v ~/mig/v1.db:/tmp/v1.db:ro app python -m server.migrate_verify /tmp/v1.db
#   → attendu en fin de sortie : « VERDICT : ✅ RÉCONCILIATION COMPLÈTE »
#   → toute anomalie (⚠) : STOP, diagnostiquer, ne PAS basculer.

# 4d. GELER tous les agents avant de démarrer (sinon ils repartent seuls)
docker compose -f docker-compose.v2.yml -p agent-swarm-v2 exec -T db \
  psql -U swarm -d swarm -c "UPDATE agents SET paused = true;"

# 4e. Démarrer la v2
docker compose -f docker-compose.v2.yml -p agent-swarm-v2 up -d app
sleep 5 && curl -s http://127.0.0.1:8001/healthz     # doit répondre status:ok
```

## 5. Corriger les comptes (emails + mots de passe réels)

Connecte-toi à la v2 comme l'admin migré (identifiants dans le rapport de l'étape 4a), puis
**Administration → Utilisateurs** : corrige les emails, réinitialise les mots de passe, ajuste les
quotas. (Ou en SQL si tu préfères.)

## 6. Bascule nginx — deux options

### Option A (recommandée) — URL inchangée : la v2 prend le port 8080 de la v1
Édite le vhost v1 pour pointer vers l'app v2 :
```bash
sudo sed -i 's#proxy_pass http://127.0.0.1:8000;#proxy_pass http://127.0.0.1:8001;#' \
  /etc/nginx/sites-available/agent-swarm
sudo nginx -t && sudo systemctl reload nginx
```
→ `https://agents.mapi85.fr:8080` sert désormais la v2. (Rien à faire côté IONOS.)

### Option B — nouvelle URL : la v2 reste sur le port 8001
Ouvre le port 8001 dans IONOS + active le vhost `~/agents-v2.conf` (voir plus bas), et communique
la nouvelle URL `https://agents.mapi85.fr:8001` aux utilisateurs.

## 7. Réactiver les agents (quand la v2 est validée)

```bash
# v1 bien arrêtée ? (sinon double exécution)
cd ~/agent-swarm && sudo docker compose ps    # doit être vide/stopped

# Réactiver tous les agents (ou sélectivement via l'interface, agent par agent)
cd ~/agent-swarm-v2
docker compose -f docker-compose.v2.yml -p agent-swarm-v2 exec -T db \
  psql -U swarm -d swarm -c "UPDATE agents SET paused = false;"
```
Le planificateur v2 reprend alors le travail autonome.

---

## Retour arrière (si problème)

La v1 et son volume sont intacts. Pour revenir :
```bash
# 1. Repointer nginx vers la v1 (si Option A appliquée)
sudo sed -i 's#proxy_pass http://127.0.0.1:8001;#proxy_pass http://127.0.0.1:8000;#' \
  /etc/nginx/sites-available/agent-swarm
sudo nginx -t && sudo systemctl reload nginx
# 2. Redémarrer la v1
cd ~/agent-swarm && sudo docker compose up -d
# 3. Geler la v2 pour éviter la double exécution
cd ~/agent-swarm-v2 && docker compose -f docker-compose.v2.yml -p agent-swarm-v2 \
  exec -T db psql -U swarm -d swarm -c "UPDATE agents SET paused = true;"
```

---

## Sauvegarde de la v2 (à mettre en place après la bascule)

La v2 = PostgreSQL + volume + `.env`. Remplacer le cron v1 par :
```bash
# Dump base + workdirs + clé de chiffrement
docker compose -f ~/agent-swarm-v2/docker-compose.v2.yml -p agent-swarm-v2 exec -T db \
  pg_dump -U swarm swarm | gzip > ~/backups/v2/pg-$(date +%F).sql.gz
docker run --rm -v agent-swarm-v2_swarm-data:/d:ro -v ~/backups/v2:/out alpine \
  tar czf /out/data-$(date +%F).tgz -C /d .
cp ~/agent-swarm-v2/.env ~/backups/v2/env.backup      # contient SECRET_ENCRYPTION_KEY
```
Conserver une copie **hors serveur**.

---

## Vhost nginx v2 sur :8001 (Option B) — rappel

Fichier déjà déposé dans `~/agents-v2.conf`. Installation :
```bash
sudo cp ~/agents-v2.conf /etc/nginx/sites-available/agents-v2
sudo ln -sf /etc/nginx/sites-available/agents-v2 /etc/nginx/sites-enabled/agents-v2
sudo nginx -t && sudo systemctl reload nginx
```
+ ouvrir le port TCP 8001 dans le pare-feu cloud IONOS.
