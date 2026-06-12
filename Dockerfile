# Isole la plateforme (et donc le shell des agents) de la machine hôte.
FROM python:3.12-slim

# Outils de base couramment utilisés par les agents (git, curl, build, sqlite).
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash git curl ca-certificates build-essential sqlite3 procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

# Utilisateur dédié non-root : les agents tournent sous cet utilisateur.
RUN useradd -ms /bin/bash swarm && mkdir -p /app/data && chown -R swarm:swarm /app
USER swarm

EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
