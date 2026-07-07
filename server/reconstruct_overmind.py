"""Recréation de l'agent overmind (#13) — orchestrateur, recentré sur le volet
LÉGITIME (coordination + dossier de preuve). Le volet offensif « Phase 5 »
(phishing, faux comptes, accès poste) est volontairement retiré de sa mémoire
et de son bilan. Agent gelé.

Nécessite le dump v1 (/tmp/v1.db) pour la configuration de l'agent.

    docker compose ... run --rm -v ~/mig/v1.db:/tmp/v1.db:ro app python -m server.reconstruct_overmind --apply
"""
import sqlite3
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from .config import get_settings

V1DB = "/tmp/v1.db"
OWNER = 1

MEM = [
    ("volet", "COORDINATION + PREUVE — orchestration de l'investigation de fraude ; dossier destiné à un dépôt de plainte."),
    ("affaire", "Arnaque crypto pig butchering (~100 k€+), 4 victimes (Martineau x2, El Manchi, +1) ; plainte déposée à Dubaï le 06/04/2025."),
    ("suspect", "Personne présumée de l'arnaque (identité détaillée dans le dossier de preuve — ressources)."),
    ("dossier_preuve", "Chaîne de custody V38 (144 pièces EV-001→144), fiches d'identité, chronologie — assemblés et figés."),
    ("crypto", "Réseau TRON tracé (voir ledger) ; cashout Bybit/Binance ; préjudice ~111 652 USDT documenté."),
    ("infra_scam", "Réseau de domaines jinzym + domaines crypto d'origine morts (voir archivist/echo)."),
    ("faux_documents", "Auto-falsification de passeports établie (voir archivist)."),
    ("signalements", "Bybit / Binance / Tether PRÊTS et figés + synthèse avocats/exchanges — pour dépôt par voie légale."),
    ("agents_coordonnes", "archivist (forensique), ledger (crypto), echo (OSINT figé), websearch (recherche) — tous sur le volet preuve."),
    ("cadre", "Suite à mener STRICTEMENT par voie légale (plainte, réquisitions Tether/exchanges). Volet offensif retiré."),
]

BILAN = (
    "Coordinateur de l'investigation de fraude (pig butchering, ~100 k€+, 4 victimes, plainte "
    "déposée à Dubaï 04/2025). Dossier de preuve complet assemblé : chaîne de custody (144 pièces), "
    "identité, réseau crypto, infrastructure et faux documents établis ; signalements exchanges + "
    "Tether prêts à déposer. La suite se mène strictement dans un cadre légal (plainte, réquisitions). "
    "Le volet offensif a été retiré de sa mémoire. Agent gelé."
)


def run(apply: bool) -> None:
    s = get_settings()
    engine = create_engine(s.database_url)
    v1 = sqlite3.connect(V1DB)
    v1.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    cfg = v1.execute("SELECT name,description,mission_prompt,category,model,effort,provider_id "
                     "FROM agents WHERE id=13").fetchone()
    print(f"=== RECRÉATION overmind (#13) — {'APPLIQUÉ' if apply else 'DRY-RUN'} ===")
    if cfg is None:
        print("agent 13 absent du dump v1 — abandon"); return
    print(f"mission_prompt: {len(cfg['mission_prompt'])} car. · mémoire consolidée: {len(MEM)} entrées (volet légitime)")
    if not apply:
        return

    with engine.begin() as db:
        if db.execute(text("SELECT 1 FROM agents WHERE id=13")).scalar():
            print("agent 13 déjà présent — abandon (rien écrit)"); return
        db.execute(text(
            "INSERT INTO agents (id,owner_user_id,name,description,mission_prompt,category,provider_id,"
            "model,effort,max_iterations,session_token_budget,max_parallel_tasks,paused,created_at) "
            "VALUES (13,:o,:n,:d,:mp,:c,:p,:m,:e,60,0,1,true,:t)"),
            {"o": OWNER, "n": cfg["name"], "d": cfg["description"] or "", "mp": cfg["mission_prompt"],
             "c": cfg["category"] or "", "p": cfg["provider_id"], "m": cfg["model"],
             "e": cfg["effort"] or "high", "t": now})
        for k, v in MEM:
            db.execute(text("INSERT INTO memories (agent_id,user_id,scope,mkey,mvalue,updated_at) "
                            "VALUES (13,:u,'agent',:k,:v,:t)"), {"u": OWNER, "k": k, "v": v, "t": now})
        socle = db.execute(text(
            "INSERT INTO tasks (mission_id,agent_id,owner_user_id,title,description,status,created_by,"
            "input_tokens,output_tokens,created_at,completed_at) "
            "VALUES (NULL,13,:u,'Socle de reprise (coordination/preuve)',:de,'done','user',0,0,:t,:t) RETURNING id"),
            {"u": OWNER, "de": "Point de reprise v2 (volet coordination/preuve).", "t": now}).scalar()
        db.execute(text(
            "INSERT INTO sessions (task_id,agent_id,number,objective,status,started_at,ended_at,report,"
            "input_tokens,output_tokens) VALUES (:tk,13,1,'Bilan de reprise','completed',:t,:t,:r,0,0)"),
            {"tk": socle, "r": BILAN, "t": now})
        db.execute(text("SELECT setval(pg_get_serial_sequence('agents','id'), "
                        "GREATEST((SELECT COALESCE(MAX(id),0) FROM agents),1))"))

    mem_dir = s.agents_dir / "13" / "memory" / "users" / str(OWNER)
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text(f"# Reprise v2 (coordination/preuve)\n\n{BILAN}\n", encoding="utf-8")
    v1.close()
    print("✅ overmind recréé (gelé), mémoire recentrée sur le volet légitime.")


if __name__ == "__main__":
    run("--apply" in sys.argv)
