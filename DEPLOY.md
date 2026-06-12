# Déploiement sur Ubuntu

Plateforme d'essaim d'agents autonomes. Deux options : **Docker** (recommandé — isole le shell des agents de la machine hôte) ou **systemd** (natif).

> ⚠️ Les agents disposent d'un **accès shell complet** + Internet + e-mail (c'est voulu : autonomie totale). Déploie sur une **VM ou un hôte dédié**, et n'expose pas le port 8000 publiquement sans authentification (reverse proxy + auth).

---

## Prérequis communs

- Une clé API Anthropic (`ANTHROPIC_API_KEY`).
- Ubuntu 22.04+ avec accès `sudo`.

> Archive disponible en deux formats, contenu identique (à la racine, sans `.venv` ni `data`) :
> - ZIP : `unzip agent-swarm.zip` (installer `unzip` au besoin : `sudo apt install -y unzip`)
> - TAR.GZ : `tar xzf agent-swarm.tar.gz` (natif, aucun paquet à installer)
>
> L'environnement virtuel n'est **pas** dans l'archive : l'option Docker n'en a pas besoin ; l'option systemd le crée (étape 3).

---

## Option A — Docker (recommandée)

### 1. Installer Docker

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

### 2. Déposer et décompresser le projet

```bash
mkdir -p ~/agent-swarm && cd ~/agent-swarm
# Copie agent-swarm.zip ici (scp / sftp), puis :
unzip agent-swarm.zip
```

### 3. Configurer

```bash
cp .env.example .env
nano .env          # renseigner ANTHROPIC_API_KEY (+ SMTP_*, EMAIL_ALLOWLIST si besoin)
```

### 4. Construire et démarrer

```bash
sudo docker compose up -d --build
```

- Tableau de bord : `http://127.0.0.1:8000` (sur le serveur).
- Les agents exécutent leur shell **dans le conteneur**, pas sur l'hôte.
- Données persistées dans le volume Docker `swarm-data` (base + workdirs des agents).

### 5. Exploitation

```bash
sudo docker compose logs -f          # suivre les logs
sudo docker compose restart          # redémarrer
sudo docker compose down             # arrêter
sudo docker compose up -d --build    # mettre à jour après modification du code
```

---

## Option B — systemd (sans Docker)

### 1. Installer Python et dépendances système

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git
```

### 2. Déposer le projet

```bash
sudo mkdir -p /opt/agent-swarm && sudo chown $USER /opt/agent-swarm
cd /opt/agent-swarm
# Copie agent-swarm.zip ici, puis :
unzip agent-swarm.zip
```

### 3. Environnement virtuel + dépendances

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 4. Configurer

```bash
cp .env.example .env
nano .env          # ANTHROPIC_API_KEY, etc.
```

### 5. Créer un utilisateur dédié (les agents tourneront sous lui)

```bash
sudo useradd -r -s /usr/sbin/nologin swarm 2>/dev/null || true
sudo chown -R swarm:swarm /opt/agent-swarm
```

### 6. Service systemd

```bash
sudo tee /etc/systemd/system/agent-swarm.service >/dev/null <<'UNIT'
[Unit]
Description=Essaim d'agents autonomes
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/agent-swarm
EnvironmentFile=/opt/agent-swarm/.env
ExecStart=/opt/agent-swarm/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
User=swarm
Group=swarm

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now agent-swarm
```

### 7. Exploitation

```bash
sudo systemctl status agent-swarm
sudo journalctl -u agent-swarm -f      # logs en direct
sudo systemctl restart agent-swarm
```

---

## Accès distant sécurisé (optionnel mais recommandé)

Ne publie pas le port 8000 tel quel. Place un reverse proxy avec authentification.

### Exemple Nginx + Basic Auth

```bash
sudo apt install -y nginx apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd admin     # définir le mot de passe

sudo tee /etc/nginx/sites-available/agent-swarm >/dev/null <<'CONF'
server {
    listen 80;
    server_name _;
    location / {
        auth_basic "Essaim d'agents";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_http_version 1.1;
    }
}
CONF

sudo ln -sf /etc/nginx/sites-available/agent-swarm /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

> Ajoute HTTPS (Let's Encrypt / `certbot`) si l'accès se fait depuis Internet.
> En Docker, le port est déjà limité à `127.0.0.1:8000` dans `docker-compose.yml` ; le proxy y accède en local.

---

## Premiers pas après déploiement

1. Ouvre le **Tableau de bord** (indicateurs, états des agents, consommation de tokens).
2. Onglet **Réglages** : un provider « Anthropic » est créé par défaut (clé = `ANTHROPIC_API_KEY`) ; ajoute d'autres providers (Anthropic-compatible ou OpenAI-compatible) si besoin et choisis le provider par défaut.
3. Onglet **Supervision** : crée un agent (mission, provider, modèle, effort, budget de tokens).
4. Confie-lui une **tâche**, ou décris une **mission** dans l'onglet Missions pour laisser le superviseur planifier.
5. Surveille le **flux**, la **cloche de notifications** 🔔 (questions/alertes) et les **Ressources & artefacts**.

> **Mise à jour d'une installation existante** : remplace les fichiers du projet (l'archive ne contient ni `data/` ni `.env`) puis reconstruis/redémarre. Les migrations de base sont automatiques au démarrage — y compris la migration des anciens réglages primaire/fallback vers la table des providers.

---

## Sauvegarde / restauration

- **Docker** : les données sont dans le volume `swarm-data`.
  ```bash
  # sauvegarde
  sudo docker run --rm -v swarm-data:/data -v "$PWD":/backup alpine \
    tar czf /backup/swarm-backup.tgz -C /data .
  # restauration (conteneur arrêté)
  sudo docker run --rm -v swarm-data:/data -v "$PWD":/backup alpine \
    sh -c "cd /data && tar xzf /backup/swarm-backup.tgz"
  ```
- **systemd** : tout est dans `/opt/agent-swarm/data/` (sauvegarde ce dossier).

---

## Dépannage

| Symptôme | Piste |
|---|---|
| 401 / `invalid x-api-key` dans le flux | clé absente/erronée : `.env` (`ANTHROPIC_API_KEY`) ou la clé du provider dans Réglages (puis redémarrer) |
| Aucune session ne démarre | un agent en pause, ou en attente d'une réponse (cloche de notifications) |
| Mission figée | une tâche a échoué → mission `needs_attention`, voir Notifications |
| Le port 8000 est pris | changer le mapping dans `docker-compose.yml` ou l'option `--port` |
| Un agent installe des paquets (Docker) | normal, c'est isolé dans le conteneur |
