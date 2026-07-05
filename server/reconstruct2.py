"""Incrément de reconstruction — volet PREUVE (echo, ledger, websearch) + dossier
overmind en ressources. À lancer APRÈS server.reconstruct, sur l'Essaim propre.

Nécessite le dump v1 (/tmp/v1.db, pour les configs d'agents) et le volume v1
monté en lecture seule (/v1, pour les fichiers). Tout reste GELÉ.

    docker compose ... run --rm -v ~/mig/v1.db:/tmp/v1.db:ro \
        -v agent-swarm_swarm-data:/v1:ro app python -m server.reconstruct2 --apply
"""
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from .config import get_settings

V1DB = "/tmp/v1.db"
V1VOL = Path("/v1")
OWNER = 1  # Par défaut (admin)

# Agents PREUVE à recréer (gelés). keep_deliv = sous-chaînes des livrables à garder.
EVIDENCE = {
    10: {  # echo — figé, aucune surveillance relancée
        "keep_deliv": ["OSINT_FINDINGS", "NETWORK_SURVEILLANCE", "ABUSE_REPORTS"],
        "tasks": [],
        "bilan": "Volet PREUVE (OSINT, sources ouvertes) — FIGÉ, aucune surveillance active "
                 "relancée. Constats pertinents pour le dossier de fraude conservés : rôle de la "
                 "personne visée, cartographie du réseau d'escroquerie et des flux. À exploiter "
                 "uniquement dans un cadre légal (plainte).",
        "mem": [
            ("volet", "PREUVE OSINT en sources ouvertes — figé, aucune collecte active."),
            ("suspect", "Personne visée = suspecte présumée de l'arnaque (identité détaillée dans l'archive brute, non reconduite ici)."),
            ("reseau_scam", "Réseau de domaines 'jinzym' (50+, ~30 actifs) + portails casino ; Cloudflare + Meteverse (Paris) ; registrar récurrent Gname. Domaines crypto d'origine tous morts."),
            ("flux", "Cashout via hot wallets Bybit/Binance ; wallets intermédiaires TRON dormants depuis 2024."),
            ("outils_limite", "Quotas web_search/web_reader épuisés (reset 19/07/2026)."),
        ],
    },
    12: {  # ledger — traçage crypto, documents de gel
        "keep_deliv": ["KYC_FREEZE", "EXECUTIVE_SUMMARY", "ONCHAIN_ANALYSIS", "CRYPTO_ANALYSIS"],
        "tasks": [],
        "bilan": "Volet PREUVE (traçage on-chain, données publiques blockchain). Préjudice "
                 "documenté (~111 652 USDT, 2 victimes) ; chaîne de traçage jusqu'aux exchanges de "
                 "cashout (Bybit, Binance) ; documents de demande de gel KYC prêts pour réquisition. "
                 "Le gel passe par la voie légale (police report + freezing order).",
        "mem": [
            ("volet", "PREUVE — traçage on-chain TRON en données publiques."),
            ("prejudice", "~111 652 USDT documentés (victimes Pierre + Elisabeth Martineau)."),
            ("cashout_exchanges", "Bybit (hot wallet TU4vEruv…) et Binance (hot wallet TDqSquXB…) identifiés comme points de cashout."),
            ("chaine_tracage", "Collecte mutualisée → hub contrôleur (TFRg9MZP…) → cashout ; ~4 couches ; 29 hashes de TX + 19 adresses documentés."),
            ("documents_gel", "LEDGER_KYC_FREEZE_REQUEST_BYBIT_BINANCE.md + LEDGER_EXECUTIVE_SUMMARY.md (prêts pour réquisition judiciaire)."),
            ("procedures_gel", "Bybit/Binance exigent un rapport de police + freezing order (voie légale ; pas d'accès direct)."),
            ("statut_wallets", "Wallets scam vidés/dormants depuis 2024 ; wallets exchanges actifs (soldes clients)."),
        ],
    },
    14: {  # websearch — nettoyé (tâche offensive #53 écartée)
        "keep_deliv": ["osint-email-enumeration", "bitcoin-market-analysis"],
        "tasks": [
            ("Vérifier la procédure de gel Tether (voie légale)",
             "Confirmer la procédure officielle de gel d'USDT par Tether sur réquisition d'une "
             "autorité (canaux légaux uniquement)."),
            ("Évaluer Chainabuse pour signalement de fraude crypto",
             "Évaluer chainabuse.com comme canal de signalement de l'arnaque aux plateformes."),
        ],
        "bilan": "Sous-agent de recherche factuelle (mode repli offline, aucun outil web live). "
                 "Livrables utiles : méthodologie OSINT email + analyse de marché. Nettoyé : la tâche "
                 "de « cadre d'accès distant » (volet offensif) est écartée. Restent des recherches "
                 "légitimes de soutien à la plainte (gel Tether, Chainabuse).",
        "mem": [
            ("mode", "Repli offline : pas d'outil web live ; réponses depuis connaissance interne + calibration de confiance."),
            ("livrables", "osint-email-enumeration-methodology.md + bitcoin-market-analysis (déjà produits)."),
            ("web_quota", "web_search/web_reader épuisés (reset 19/07/2026)."),
            ("perimetre", "Recherche factuelle de soutien : procédures de gel (Tether), canaux de signalement (Chainabuse)."),
        ],
    },
}

# Dossier overmind → ressources (fichiers de preuve clés, pas d'agent).
OVERMIND_GLOBS = [
    ("13_agent-overmind/deliverables/formal", "*.md"),
    ("13_agent-overmind/deliverables/working", "RAPPORT_PHASE6*.md"),
]


def _agent_cfg(v1, aid):
    r = v1.execute("SELECT name,description,mission_prompt,category,model,effort,provider_id "
                   "FROM agents WHERE id=?", (aid,)).fetchone()
    return r


def reconstruct2(apply: bool) -> None:
    s = get_settings()
    engine = create_engine(s.database_url)
    now = datetime.now(timezone.utc)
    v1 = sqlite3.connect(V1DB)
    v1.row_factory = sqlite3.Row

    print(f"=== INCRÉMENT PREUVE — {'APPLIQUÉ' if apply else 'DRY-RUN'} ===")
    print(f"Agents preuve recréés (gelés) : {sorted(EVIDENCE)}")
    print("Dossier overmind → ressources (pas d'agent).")
    if not apply:
        return

    with engine.begin() as db:
        for aid, cur in EVIDENCE.items():
            cfg = _agent_cfg(v1, aid)
            if cfg is None:
                print(f"  ⚠ agent {aid} absent du dump v1 — ignoré")
                continue
            db.execute(text(
                "INSERT INTO agents (id, owner_user_id, name, description, mission_prompt, category, "
                "provider_id, model, effort, max_iterations, session_token_budget, max_parallel_tasks, "
                "paused, created_at) VALUES (:i,:o,:n,:d,:mp,:c,:p,:m,:e,60,0,1,true,:t) "
                "ON CONFLICT (id) DO NOTHING"),
                {"i": aid, "o": OWNER, "n": cfg["name"], "d": cfg["description"] or "",
                 "mp": cfg["mission_prompt"], "c": cfg["category"] or "", "p": cfg["provider_id"],
                 "m": cfg["model"], "e": cfg["effort"] or "high", "t": now})
            for k, v in cur["mem"]:
                db.execute(text("INSERT INTO memories (agent_id,user_id,scope,mkey,mvalue,updated_at) "
                                "VALUES (:a,:u,'agent',:k,:v,:t)"),
                           {"a": aid, "u": OWNER, "k": k, "v": v, "t": now})
            socle = db.execute(text(
                "INSERT INTO tasks (mission_id,agent_id,owner_user_id,title,description,status,created_by,"
                "input_tokens,output_tokens,created_at,completed_at) "
                "VALUES (NULL,:a,:u,'Socle de reprise (preuve)',:de,'done','user',0,0,:t,:t) RETURNING id"),
                {"a": aid, "u": OWNER, "de": "Point de reprise v2 (volet preuve).", "t": now}).scalar()
            db.execute(text(
                "INSERT INTO sessions (task_id,agent_id,number,objective,status,started_at,ended_at,"
                "report,input_tokens,output_tokens) VALUES (:tk,:a,1,'Bilan de reprise','completed',:t,:t,:r,0,0)"),
                {"tk": socle, "a": aid, "r": cur["bilan"], "t": now})
            for title, desc in cur["tasks"]:
                db.execute(text(
                    "INSERT INTO tasks (mission_id,agent_id,owner_user_id,title,description,status,created_by,"
                    "input_tokens,output_tokens,created_at) VALUES (NULL,:a,:u,:ti,:de,'pending','user',0,0,:t)"),
                    {"a": aid, "u": OWNER, "ti": title, "de": desc, "t": now})

        # Dossier overmind → ressources fichiers
        s.resources_dir.mkdir(parents=True, exist_ok=True)
        n_res = 0
        for subdir, pattern in OVERMIND_GLOBS:
            src_dir = V1VOL / "agents" / subdir
            if not src_dir.is_dir():
                continue
            for f in sorted(src_dir.glob(pattern)):
                if not f.is_file():
                    continue
                rid = db.execute(text(
                    "INSERT INTO resources (scope,owner_user_id,task_id,name,kind,description,size,created_by,created_at) "
                    "VALUES ('user',:u,NULL,:n,'file',:d,:sz,'migration:overmind',:t) RETURNING id"),
                    {"u": OWNER, "n": f.name, "d": "Dossier de preuve (overmind) — pour dépôt de plainte",
                     "sz": f.stat().st_size, "t": now}).scalar()
                stored = f"{rid}_{f.name}"
                shutil.copy2(f, s.resources_dir / stored)
                db.execute(text("UPDATE resources SET filename=:fn WHERE id=:i"), {"fn": stored, "i": rid})
                n_res += 1
        print(f"  ressources de preuve créées : {n_res}")

        for tbl in ("agents", "tasks", "sessions", "memories", "resources"):
            db.execute(text(f"SELECT setval(pg_get_serial_sequence('{tbl}','id'), "
                            f"GREATEST((SELECT COALESCE(MAX(id),0) FROM {tbl}),1))"))

    # Fichiers workdir des agents preuve : recopier library + livrables conservés depuis v1
    for aid, cur in EVIDENCE.items():
        cfg = _agent_cfg(v1, aid)
        if cfg is None:
            continue
        src = V1VOL / "agents" / f"{aid}_{cfg['name']}"
        dst = s.agents_dir / str(aid)
        if (src / "library").is_dir():
            shutil.copytree(src / "library", dst / "library", dirs_exist_ok=True)
        deliv_src = src / "deliverables"
        if deliv_src.is_dir():
            for f in deliv_src.rglob("*"):
                if f.is_file() and any(sub in f.name for sub in cur["keep_deliv"]):
                    tgt = dst / "kept_deliverables" / f.name
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, tgt)
        mem_dir = dst / "memory" / "users" / str(OWNER)
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "MEMORY.md").write_text(f"# Reprise v2 (preuve)\n\n{cur['bilan']}\n", encoding="utf-8")

    v1.close()
    print("\n✅ Incrément preuve appliqué. Agents PREUVE gelés ; dossier overmind en ressources.")


if __name__ == "__main__":
    reconstruct2("--apply" in sys.argv)
