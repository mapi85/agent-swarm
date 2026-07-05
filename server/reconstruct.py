"""Reconstruction curée du nouvel Essaim (agents SAINS + volet PREUVE).

Applique sur la base v2 (migration complète) la curation validée : ne garde que
les agents retenus, remplace leur mémoire par une version consolidée, leur écrit
un bilan de session unique, garde leurs tâches vivantes en attente, et élague les
artefacts. Tout reste GELÉ (agents en pause, aucune session lancée).

PRÉALABLE OBLIGATOIRE : un archive-dump de la base v2 doit avoir été pris avant
(les agents « à revoir » n'existent plus après cette opération ; leur brut vit
dans le volume v1 gelé + l'archive-dump).

    python -m server.reconstruct            # dry-run : affiche le plan
    python -m server.reconstruct --apply    # applique
"""
import shutil
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from .config import get_settings

# Profils conservés (les vides Test #2 / PRIVE #4 sont supprimés)
KEPT_USERS = {1: "admin", 3: "user"}  # 1 = Par défaut, 3 = Démo

# owner : propriétaire ; mem : mémoire consolidée [(clé, valeur)] ; bilan : texte ;
# tasks : tâches vivantes à ouvrir [(titre, description)] ; keep_deliv : livrables à
# garder (sous-chaînes de noms) ; sinon legacy_deliverables/ est vidé.
CURATION = {
    3: {"owner": 1, "keep_deliv": [], "tasks": [], "bilan":
        "Agent-usine de l'essaim. A produit les configurations des agents spécialisés "
        "(trading, investigation, veille, créatif). Génération terminée ; en veille, "
        "disponible pour concevoir ou mettre à jour des configurations d'agents. Boîte à "
        "outils réutilisable dans library/.",
        "mem": [
            ("role", "Génère les configurations (system prompt + outils function-calling) d'agents autonomes."),
            ("conventions", "config JSON + prompt .md ; response_format JSON forcé ; optimisation tokens (~84%)."),
            ("roster_versions", "orchestrator 1.0 · buyer/seller/swapper 1.0 · executor 2.0 (OrderQueue SIGUSR1) · overmind 2.0 · websearch 2.0 (repli offline) · avatar-3d 1.0."),
            ("outils_library", "safety/, order_queue, backtesting, cache_manager, async_http_client."),
            ("discipline", "task_completion_discipline.md (règle unfinished_task_ids)."),
        ]},
    4: {"owner": 1, "keep_deliv": [], "tasks": [], "bilan":
        "Orchestrateur trading : système 100% cash (equity ~$9 497, -5,03%), sans position "
        "depuis la clôture défensive de BTC2 (+$16,23). MACD 4h en croisement baissier, volume "
        "très bas. En PASSIVE_SURVEILLANCE, 0/3 critères de réentrée. Prochaines étapes : "
        "surveiller re-croisement MACD positif + retour du volume + reconquête $63 000.",
        "mem": [
            ("role", "Supervision + validation des ordres (BUY/SELL/SWAP), gestion du risque, arbitrage (Seller>Buyer>Swapper)."),
            ("system_mode", "PAPER, capital initial 10 000 USDT."),
            ("capital", "equity ~$9 497 (-5,03%), 0/5 position, 100% CASH, mode PASSIVE_SURVEILLANCE."),
            ("realized_ledger", "net -$359,01 (BNB +9,33 / BTC1 -289,71 / XRP -94,86 / BTC2 +16,23)."),
            ("macd_bug_fix", "CRITIQUE : bug S88 EMA mal alignées ; fix = aligner EMA depuis la fin, calcul inline Python (S108+)."),
            ("reentry_criteria", "3 conditions cumulées : MACD hist repasse positif + volume >1,0x + reconquête $63 000."),
            ("watchlist", "BTC/XRP/ETH/SOL ; supports $62K/$61,5K/$60K."),
            ("data_source", "trading_state.json (fichier partagé de trading)."),
        ]},
    5: {"owner": 1, "keep_deliv": [], "tasks": [], "bilan":
        "Acheteur en paper trading, 100% cash, marché en fear extrême, aucun candidat au-dessus "
        "du seuil 70. Ré-entrée seulement si les 3 prérequis réunis. Point d'attention : des "
        "ordres passés n'ont jamais été exécutés côté executor — toujours confirmer l'exécution.",
        "mem": [
            ("config", "PAPER, watchlist BTC/ETH/SOL/BNB/XRP, cycle 4h, seuil composite 70/100, levier max 3x, max 5 positions."),
            ("etat", "100% cash, aucune position."),
            ("regime", "MACD 4h bearish confirmé, Fear&Greed 23, composite global ~32."),
            ("prerequis_reentree", "0/3 : MACD hist positif montant + volume >1x + reclaim $63K."),
            ("historique", "4 trades fermés, net ≈ -$358,6."),
            ("leçon", "ordres S#15 (ex-#46) jamais exécutés par executor → toujours confirmer l'exécution."),
            ("outils", "library/composite_scorer.py + market_data.py (scoring + données live)."),
        ]},
    6: {"owner": 1, "keep_deliv": [], "tasks": [], "bilan":
        "Vendeur en paper trading, infrastructure opérationnelle. Une position BTC LONG ouverte "
        "et profitable (+$9,93), en HOLD (inversion 1/4, MACD en recovery vers TP1 $63K). "
        "Historique : 4 clôturées, win rate 25%, réalisé -$374,37. Prêt pour reprise du "
        "monitoring événementiel. À vérifier contre trading_state.json avant reprise.",
        "mem": [
            ("active_position", "POS_063394AB2672 BTCUSDT LONG 2x, entry $60 350, TP1 $63K / TP2 $64K, SL $59 300, uPnL +$9,93 → HOLD."),
            ("closed_positions", "4 trades (BNB +9,33 / BTC -144,63 puis -144,27 / XRP -94,80)."),
            ("metrics", "win rate 25%, PnL réalisé -$374,37, non réalisé +$9,93."),
            ("strategy", "no-panic/maximize-PnL ; TP→100% / SL→100% ; funding >0,5% marge → fermeture."),
            ("reversal_criteria", "LONG (4 requises) : RSI>70 + divergence MACD baissière + prix<MA50 + volume en hausse."),
            ("infrastructure", "queue orders/pending_orders.json + trading_state.json (partagés)."),
        ]},
    8: {"owner": 1, "keep_deliv": [],
        "tasks": [("Réexécuter les ordres d'achat en attente (ex-#46)",
                   "Reprise de l'ancienne tâche #46 (3 ordres avortés sur stagnation). À ne "
                   "traiter que si l'orchestrateur réémet des ordres FRAIS — le contexte marché "
                   "de S15 est périmé. Confirmer l'exécution effective après passage.")],
        "bilan":
        "Exécutant PAPER (Bitunix, $10 000), 100% cash en veille. A exécuté plusieurs cycles "
        "(BNB/XRP/BTC) et des clôtures défensives ; dernier trade BTC2 soldé +$16,23. Une tâche "
        "technique (ex-#46) à relancer seulement sur ordres frais.",
        "mem": [
            ("config", "PAPER, Bitunix (ccxt), capital $10 000, glm-5.2."),
            ("statut", "100% CASH / STANDBY, toutes positions fermées."),
            ("capital", "dispo $9 496,86, drawdown -5,03%."),
            ("trading_state", "trading_state.json (partagé)."),
            ("derniere_position", "BTC2 clôturée +$16,23 (TP1 + close défensif)."),
            ("regles_execution", "DRY_RUN/PAPER/LIVE ; précision USD/EUR 2 déc., size 8 déc."),
        ]},
    15: {"owner": 3, "keep_deliv": ["bulletin-veille-2026-07-02", "deep-dive-concurrents-emergents"],
         "tasks": [("Synthèse & rapport hebdomadaire",
                    "Ex-tâche #64, jamais faite : consolider les signaux de veille et le suivi "
                    "concurrentiel en un rapport hebdomadaire (marché/concurrents/opportunités).")],
         "bilan":
         "Agent de veille marché ERP & hébergement France (3 sessions). Base de connaissances + "
         "chaîne de surveillance Python (RSS 7/9 sources, BOAMP enrichi, dashboard). ~489 signaux "
         "collectés, deep-dive de 3 concurrents souverains, bulletin n°3. Reste à faire : scraper "
         "Cegid, automatiser le dashboard, rapport de synthèse hebdo.",
         "mem": [
             ("concurrents_cloud", "AWS, Azure, GCP, OVHcloud, Scaleway."),
             ("concurrents_erp", "SAP, Cegid, Sage, Microsoft Dynamics, Oracle."),
             ("concurrents_emergents", "Bleu/Delos Cloud (Thales+MS), ChapsVision (souveraine), Alibaba Cloud FR (Paris)."),
             ("signaux_2026", "souveraineté, NIS2, DGSI→ChapsVision, DMA, post-quantique FR."),
             ("veille_boamp_ref", "995 marchés (Infogérance 384, Logiciel 202, Refonte SI 171…)."),
             ("sources", "Silicon.fr, LeMondeInformatique, JDN, ZDNet, OVHcloud, Scaleway, SAP News, BOAMP."),
             ("outils_status", "rss-monitor v2 (7/9), boamp-monitor v2, dashboard v1, run-veille.sh v2."),
             ("prochaine_veille", "scraper Cegid, automatiser dashboard, matching BOAMP↔critères, bulletin n°4."),
         ]},
    # --- volet PREUVE ---
    9: {"owner": 1,
        "keep_deliv": ["FAUX_PASSEPORT_ANALYSE", "IG_REELS_SCREENSHOTS_ANALYSIS", "SESSION19_ANALYSIS"],
        "tasks": [],  # dossier de preuve gelé : pas de relance d'investigation active ici
        "bilan":
        "Volet PREUVE (forensique documentaire, sources ouvertes). Dossier consolidé : "
        "authentification de faux documents (auto-falsification établie), analyse d'images "
        "publiques, cartographie de l'infrastructure de l'arnaque (domaines crypto désormais "
        "morts ; réseau casino en contraction). Matériel exploitable pour un dépôt de plainte. "
        "Aucune investigation active relancée : à cadrer avec le propriétaire.",
        "mem": [
            ("volet", "PREUVE — forensique documentaire et analyse OSINT en sources ouvertes uniquement."),
            ("faux_documents", "Faux passeports 'K. Smith' : falsification confirmée (anomalies MRZ/ICAO) ; photos = la personne visée elle-même → auto-falsification (pas usurpation d'un tiers)."),
            ("infra_arnaque", "Domaines crypto d'origine (bmexcoins + ~8) tous morts (NXDOMAIN) ; registrar récurrent Gname ; réseau casino jinzym en contraction."),
            ("facade_employeur", "'Imperia Exclusive' = façade fabriquée (site ~83% images AI, backend vide, aucun registre) — score authenticité 42/100."),
            ("outil", "library/analyze_ig_photos.py."),
            ("note_secret", "⚠ une clé API Gemini était en clair dans l'ancienne mémoire — à révoquer, non reconduite ici."),
        ]},
}


def reconstruct(apply: bool) -> None:
    s = get_settings()
    engine = create_engine(s.database_url)
    now = datetime.now(timezone.utc)

    print("=== RECONSTRUCTION (agents sains + volet preuve) ===")
    print(f"Profils conservés : {KEPT_USERS}")
    print(f"Agents recréés    : {sorted(CURATION)}")
    print(f"Mode              : {'APPLIQUÉ' if apply else 'DRY-RUN (rien écrit)'}\n")

    with engine.begin() as db:
        kept_ids = list(CURATION)
        all_agent_dirs = {int(p.name) for p in s.agents_dir.iterdir()
                          if p.is_dir() and p.name.isdigit()} if s.agents_dir.is_dir() else set()
        drop_dirs = sorted(all_agent_dirs - set(kept_ids))
        print(f"Workdirs à retirer du volume v2 (brut conservé dans v1) : {drop_dirs}")

        if not apply:
            print("\n(dry-run) — relance avec --apply pour exécuter.")
            return

        # Purge du domaine (on conserve providers, channels, app_settings)
        db.execute(text("TRUNCATE tasks, task_links, sessions, events, memories, resources, "
                        "notifications, messages, services, missions, token_usage RESTART IDENTITY CASCADE"))
        db.execute(text("DELETE FROM agents WHERE id <> ALL(:k)"), {"k": kept_ids})
        db.execute(text("DELETE FROM users WHERE id <> ALL(:k)"), {"k": list(KEPT_USERS)})
        for uid, role in KEPT_USERS.items():
            db.execute(text("UPDATE users SET role=:r, status='active' WHERE id=:i"), {"r": role, "i": uid})

        for aid, cur in CURATION.items():
            owner = cur["owner"]
            db.execute(text("UPDATE agents SET paused=true WHERE id=:i"), {"i": aid})
            # mémoire consolidée
            for k, v in cur["mem"]:
                db.execute(text("INSERT INTO memories (agent_id, user_id, scope, mkey, mvalue, updated_at) "
                                "VALUES (:a,:u,'agent',:k,:v,:t)"),
                           {"a": aid, "u": owner, "k": k, "v": v, "t": now})
            # tâche-socle + bilan de session unique
            socle = db.execute(text(
                "INSERT INTO tasks (mission_id, agent_id, owner_user_id, title, description, status, "
                "created_by, created_at, completed_at) VALUES (NULL,:a,:u,:ti,:de,'done','user',:t,:t) RETURNING id"),
                {"a": aid, "u": owner, "ti": "Socle de reprise (bilan + mémoire consolidée)",
                 "de": "Point de reprise v2 : mémoire consolidée et bilan unique.", "t": now}).scalar()
            db.execute(text(
                "INSERT INTO sessions (task_id, agent_id, number, objective, status, started_at, ended_at, "
                "report, input_tokens, output_tokens) VALUES (:tk,:a,1,:o,'completed',:t,:t,:r,0,0)"),
                {"tk": socle, "a": aid, "o": "Bilan de reprise", "r": cur["bilan"], "t": now})
            # tâches vivantes (en attente, non lancées)
            for title, desc in cur["tasks"]:
                db.execute(text(
                    "INSERT INTO tasks (mission_id, agent_id, owner_user_id, title, description, status, "
                    "created_by, created_at) VALUES (NULL,:a,:u,:ti,:de,'pending','user',:t)"),
                    {"a": aid, "u": owner, "ti": title, "de": desc, "t": now})

        # Réalignement des séquences
        for tbl in ("tasks", "sessions", "memories", "users", "agents"):
            db.execute(text(f"SELECT setval(pg_get_serial_sequence('{tbl}','id'), "
                            f"GREATEST((SELECT COALESCE(MAX(id),0) FROM {tbl}),1))"))

    # --- disque : élagage des workdirs conservés + suppression des autres ---
    for aid, cur in CURATION.items():
        base = s.agents_dir / str(aid)
        # MEMORY.md propre
        owner = cur["owner"]
        mem_dir = base / "memory" / "users" / str(owner)
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "MEMORY.md").write_text(f"# Reprise v2\n\n{cur['bilan']}\n", encoding="utf-8")
        for stale in ("sessions.log",):
            for f in (base / "memory").rglob(stale):
                f.unlink(missing_ok=True)
        # legacy_deliverables : ne garder que les fichiers listés
        deliv = base / "legacy_deliverables"
        if deliv.is_dir():
            if not cur["keep_deliv"]:
                shutil.rmtree(deliv, ignore_errors=True)
            else:
                for f in deliv.rglob("*"):
                    if f.is_file() and not any(sub in f.name for sub in cur["keep_deliv"]):
                        f.unlink(missing_ok=True)

    for aid in drop_dirs:
        shutil.rmtree(s.agents_dir / str(aid), ignore_errors=True)

    print("\n✅ Reconstruction appliquée. Agents GELÉS (en pause), tâches en attente non lancées.")


if __name__ == "__main__":
    reconstruct("--apply" in sys.argv)
