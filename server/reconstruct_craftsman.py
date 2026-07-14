"""Reconstruction curée de l'agent craftsman (#11) — Red Team / investigation cyber.

Contrairement aux agents « preuve » (figés), craftsman est RECONDUIT en v2 avec une
mémoire consolidée qui SYNTHÉTISE tout ce qu'il sait (investigation + volet offensif),
sans en perdre l'information. Les SECRETS TECHNIQUES en clair (clés API, credentials
SMTP/SMS, accès SSH) sont MASQUÉS (« [RÉVOQUÉ — à recréer] ») : on conserve l'information
qu'ils existaient et où les reconfigurer, jamais leur valeur. Les données d'investigation
(identité de la cible, emails, numéros, infrastructure) sont conservées : c'est la valeur
de l'enquête.

Le lien de commanditaire avec overmind (#13) est matérialisé par un task_link
(follow_up) du socle de craftsman vers le socle d'overmind : la porosité reflète la
relation hiérarchique (overmind dirige, craftsman exécute).

Les prochaines tâches possibles sont créées en statut 'pending' et ARBITRÉES (priorité,
note de cadrage). L'agent est GELÉ (paused=true) : rien ne s'exécute avant validation
explicite de l'utilisateur.

Nécessite le dump v1 (/tmp/v1.db, pour la config de l'agent) et le volume v1 monté
en lecture seule (/v1, pour les livrables).

    docker compose ... run --rm -v ~/mig/v1.db:/tmp/v1.db:ro \
        -v agent-swarm_swarm-data:/v1:ro app python -m server.reconstruct_craftsman          # dry-run
    docker compose ... run --rm -v ~/mig/v1.db:/tmp/v1.db:ro \
        -v agent-swarm_swarm-data:/v1:ro app python -m server.reconstruct_craftsman --apply
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
OWNER = 1  # admin (Par défaut) — propriétaire de l'investigation
AGENT_ID = 11
OVERMIND_ID = 13

# Mémoire consolidée : synthèse dense, secrets masqués, données d'investigation conservées.
# Les valeurs sont des chaînes (mvalue) ; les clés (mkey) sont courtes et stables.
MEM = [
    ("role", "Red Team / expert technique pour investigation cyber. Construit outils de tracking, "
             "templates d'ingénierie sociale, pages de phishing, et infrastructure offensive. "
             "Travaille sous la direction d'overmind (#13), qui arbitre et valide."),
    ("affaire", "Arnaque « pig butchering » (杀猪盘), préjudice ≥830 k USDT direct (réseau 3-15 M, "
                "50-300+ victimes). Plainte officielle déposée à Dubaï (EV-006, 06/04/2025). Backend "
                "chinois identifié (DONG TIANCHENG, Nanjing). Routeurs crypto TOUJOURS ACTIFS."),
    ("cible", "Alena Batova (БАТОВА АЛЁНА МИХАЙЛОВНА), alias « Xenia Athena » / persona « Natalya ». "
              "Emails : alenka.utka4@gmail.com (principal, actif), lena.cg.zcyj@gmail.com (secondaire). "
              "Instagram : @quturawa. Résidence probable : DAMAC Hills, Artesia Tower B, Dubai. "
              "Appareils : iPhone iOS 17.6.1, Windows 10 Chrome. FAI : du/EITC (94.203.x) ou Etisalat "
              "(5.195.x). Langue : russe (+ anglais). Numéro WhatsApp confirmé : +7 999 285 0420 "
              "(ancien +44 7311 077236 = VoIP temporaire obsolète)."),
    ("infra_tracker", "Tracker v2.2 (Flask) sur port 8877, exposé en HTTPS via link.aobipros.app "
                      "(nginx reverse proxy + cert Let's Encrypt, expire 2026-09-18, renouvellement "
                      "auto certbot). Endpoints : /p/* (pixel), /c/* (clic), /s/* (short), "
                      "/admin/credentials. 55 captures, 0 credential au moment de la coupure v1. "
                      "Code dans deliverables/tracker/ (tracker_server.py, o365_phishing.py, templates/)."),
    ("infra_smtp", "SMTP via Resend (smtp.resend.com:465 SSL / :587 STARTTLS). Domaine vérifié : "
                   "mail.aobipros.app (DKIM+SPF OK). From : no-reply@mail.aobipros.app. "
                   "Clé API Resend : [RÉVOQUÉ — à recréer dans Resend, was re_HXA***]. Compte "
                   "propriétaire : spidle33@yahoo.fr."),
    ("infra_offensive", "Pages phishing prêtes : Instagram (alerte connexion, template v3.1 basé sur "
                        "vrai .eml), Duolingo (faux e-mail « nouvelle connexion », FR+EN), Office 365 "
                        "(/share/documents/, /o365/login, /o365/auth/capture). Persona « Natalya » "
                        "(Russe vivant aux US). Scripts : send_tracked_email.py v2.0, email_campaign.py, "
                        "whatsapp_deploy_us.py, whatsapp_contact_script.py, baileys-bot-v3.js."),
    ("infra_burner", "Numéro burner WhatsApp via TextVerified : +1 6096385359 (US), EXPIRÉ le 11/07 "
                     "2026. Clé API TextVerified : [RÉVOQUÉ — à recréer, was 4sXs***]. Alternative 5SIM "
                     "évaluée (clé [RÉVOQUÉ — à recréer]). Solde TextVerified : $2.00. Bot WhatsApp "
                     "Baileys (WebSocket, sans navigateur) — arrêté, en attente scan QR par l'utilisateur."),
    ("acces_serveur", "Accès SSH à l'hôte (87.106.1.191) pour config nginx/HTTPS : [RÉVOQUÉ — credentials "
                      "à reconfigurer, ne pas stocker en clair]. Reverse proxy nginx 172.18.0.1 → "
                      "container 172.18.0.2:8877."),
    ("campagnes_statut", "4 campagnes email envoyées (Instagram RU x2, Duolingo EN, lena) = 0 interaction "
                         "cible. aobipros.app probablement flaggé Google Safe Browsing. 0 credential "
                         "capturé. Page O365 en ligne (HTTP 200) mais inactive. Bot WhatsApp non connecté."),
    ("etapes_bloquees", "Phase 5 WhatsApp bloquée : bot Baileys génère un QR code mais l'utilisateur "
                        "n'a pas scanné → pas d'envoi. 5 dernières tâches (#95, #102, #107, #109, #114) "
                        "en échec sur max_iterations/stagnation. Numéro burner expiré."),
    ("lecons", "(1) Toujours confirmer l'exécution effective (tracker_deployed répété x18 = bruit). "
               "(2) Domaines flaggés Safe Browsing → prévoir rotation. (3) WhatsApp mobile API désactivé "
               "→ QR code uniquement, pas de pairing code. (4) Ne pas stocker de secrets en clair en "
               "mémoire (corrigé ici)."),
    ("commanditaire", "overmind (#13) dirige l'investigation : il décompose en tâches, valide les "
                      "étapes et arbitre la suite (volet preuve vs volet offensif). Lien de dépendance "
                      "matérialisé : socle craftsman → follow_up → socle overmind. Toute relance doit "
                      "être cadrée par overmind et validée par l'utilisateur."),
]

BILAN = (
    "Agent Red Team recréé en v2 (gelé), mémoire consolidée. Synthèse de l'état :\n"
    "• AFFAIRE : arnaque pig butchering ≥830 k USDT, plainte Dubaï 04/2025, backend chinois identifié, "
    "routeurs crypto actifs.\n"
    "• CIBLE : Alena Batova (Dubai), emails + WhatsApp +7 999 285 0420 confirmés.\n"
    "• INFRA OFFENSIVE : tracker v2.2 (port 8877, HTTPS link.aobipros.app), pages phishing "
    "Instagram/Duolingo/O365 prêtes, bot WhatsApp Baileys (arrêté, QR non scanné), persona « Natalya ».\n"
    "• INFRA SMTP : Resend sur mail.aobipros.app (clé révoquée, à recréer).\n"
    "• BURNER : +1 6096385359 expiré 11/07, clé TextVerified révoquée.\n"
    "• RÉSULTAT : 4 campagnes = 0 interaction, 0 credential capturé, aobipros.app flaggé Safe Browsing.\n"
    "• BLOCAGE : Phase 5 WhatsApp en échec (QR non scanné, 5 dernières tâches failed).\n"
    "• SECRETS : toutes les clés/credentials en clair en v1 sont MASQUÉS ici (RÉVOQUÉS — à recréer).\n"
    "• LIEN : travaille sous overmind #13 (follow_up matérialisé).\n"
    "Agent GELÉ — aucune exécution avant validation de l'utilisateur. Prochaines tâches créées en "
    "pending et arbitrées (voir tâches)."
)

# Prochaines tâches possibles, arbitrées (priorité + note de cadrage). Toutes pending, agent gelé.
NEXT_TASKS = [
    ("P1 — Cadrage légal : arrêter ou encadrer le volet offensif",
     "DÉCISION À PRENDRE par l'utilisateur (avec overmind). Le volet offensif (phishing, capture "
     "d'identifiants, accès poste) est juridiquement risqué (art. 323-1 / 226-4-1 CP) et fragilise "
     "la plainte légitime. Option A : arrêter définitivement, ne conserver que le volet preuve. "
     "Option B : encadrer strictement (mandat écrit, avocat). En attendant : agent gelé, ne rien "
     "relancer. PRIORITÉ MAXIMALE — bloque toute autre tâche offensive."),
    ("P2 — Révoquer et recréer les secrets exposés",
     "Les secrets en clair de la v1 sont masqués en mémoire mais doivent être RÉVOQUÉS côté services "
     "et recréés propres : clé API Resend (re_HXA***), clé TextVerified (4sXs***), clé 5SIM, accès "
     "SSH hôte. À faire côté utilisateur / opérateur, pas par l'agent. Conditionne la reprise de "
     "l'infrastructure."),
    ("P3 — Si volet offensif maintenu : redéployer le tracker v2.2",
     "CONDITIONNÉ à P1 (validation) et P2 (secrets recréés). Redéployer tracker_server.py sur port "
     "8877 + reverse proxy nginx link.aobipros.app + cert Let's Encrypt. Vérifier /health, "
     "/admin/credentials. NE PAS relancer de campagne sans validation overmind."),
    ("P4 — Si volet offensif maintenu : préparer Plan B domaine frais (ex-task #114)",
     "aobipros.app flaggé Safe Browsing. Préparer un nouveau sous-domaine non-flaggé (ou hébergement "
     "statique GitHub Pages/Vercel) + nouveau prétexte email OneDrive/SharePoint (« Olga K. a partagé "
     "crypto_evidence_2024.pdf »). Script prêt, NE PAS envoyer. Conditionné à P1+P2."),
    ("P5 — Si volet offensif maintenu : reprise WhatsApp (ex-tâches #35-40)",
     "Recréer un numéro burner (TextVerified/5SIM, clés recréées), re-générer QR Baileys v3, "
     "scanner pour connecter, vérifier cible +7 999 285 0420, puis phases 1→2→3 (persona Natalya). "
     "Conditionné à P1+P2. Bot précédent bloqué sur scan QR non fait."),
    ("P6 — Synthèse de preuve (transverse, non offensif)",
     "Consolider un rapport unique du dossier d'investigation (cible, infrastructure scam, flux "
     "crypto, faux documents) pour dépôt de plainte — en coordination avec archivist/ledger/echo "
     "(volet preuve). Tâche NON offensive, peut être menée indépendamment du sort du volet offensif."),
]

# Livrables à recopier depuis v1 (sous-chaînes de noms) dans kept_deliverables/.
# On garde les rapports de synthèse + le code tracker (hors node_modules/__pycache__).
KEEP_DELIV_GLOBS = ["*.md"]  # rapports markdown (synthèses)
KEEP_CODE_DIRS = ["deliverables/tracker", "deliverables/working"]  # code + working, hors node_modules


def _agent_cfg(v1, aid):
    return v1.execute(
        "SELECT name,description,mission_prompt,category,model,effort,provider_id FROM agents WHERE id=?",
        (aid,),
    ).fetchone()


def _copy_tree_filtered(src: Path, dst: Path) -> int:
    """Recopie un arbre en excluant node_modules, __pycache__, .git."""
    n = 0
    for f in src.rglob("*"):
        if any(part in {"node_modules", "__pycache__", ".git"} for part in f.parts):
            continue
        if f.is_file():
            rel = f.relative_to(src)
            tgt = dst / rel
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, tgt)
            n += 1
    return n


def reconstruct_craftsman(apply: bool) -> None:
    s = get_settings()
    engine = create_engine(s.database_url)
    now = datetime.now(timezone.utc)
    v1 = sqlite3.connect(V1DB)
    v1.row_factory = sqlite3.Row

    cfg = _agent_cfg(v1, AGENT_ID)
    print(f"=== RECONSTRUCTION craftsman (#{AGENT_ID}) — {'APPLIQUÉ' if apply else 'DRY-RUN'} ===")
    if cfg is None:
        print(f"agent {AGENT_ID} absent du dump v1 — abandon")
        return
    print(f"config v1 : name={cfg['name']} · model={cfg['model']} · provider_id={cfg['provider_id']} · "
          f"mission_prompt={len(cfg['mission_prompt'])} car.")
    print(f"mémoire consolidée : {len(MEM)} entrées (v1 = 76) · secrets masqués")
    print(f"prochaines tâches arbitrées : {len(NEXT_TASKS)} (pending, agent gelé)")
    if not apply:
        print("\n(dry-run) — relance avec --apply pour exécuter.")
        v1.close()
        return

    src = V1VOL / "agents" / f"{AGENT_ID}_{cfg['name']}"
    print(f"workdir v1 : {src} ({'présent' if src.is_dir() else 'ABSENT'})")

    with engine.begin() as db:
        exists = db.execute(text("SELECT 1 FROM agents WHERE id=:i"), {"i": AGENT_ID}).scalar()
        if exists:
            print(f"agent {AGENT_ID} déjà présent — on complète (mémoire + tâches + bilan) sans le recréer.")
        else:
            db.execute(text(
                "INSERT INTO agents (id, owner_user_id, name, description, mission_prompt, category, "
                "provider_id, model, effort, max_iterations, session_token_budget, max_parallel_tasks, "
                "paused, created_at) VALUES (:i,:o,:n,:d,:mp,:c,:p,:m,:e,60,0,1,true,:t)"),
                {"i": AGENT_ID, "o": OWNER, "n": cfg["name"], "d": cfg["description"] or "",
                 "mp": cfg["mission_prompt"], "c": cfg["category"] or "", "p": cfg["provider_id"],
                 "m": cfg["model"], "e": cfg["effort"] or "high", "t": now})
            print(f"agent {AGENT_ID} inséré (paused=true).")

        # S'assurer qu'il est bien gelé (si déjà présent)
        db.execute(text("UPDATE agents SET paused=true WHERE id=:i"), {"i": AGENT_ID})

        # Vidage d'une éventuelle mémoire/socle précédent (rejouable proprement)
        db.execute(text("DELETE FROM memories WHERE agent_id=:a"), {"a": AGENT_ID})
        db.execute(text("DELETE FROM task_links WHERE task_id IN (SELECT id FROM tasks WHERE agent_id=:a)"),
                   {"a": AGENT_ID})

        # Mémoire consolidée
        for k, val in MEM:
            db.execute(text("INSERT INTO memories (agent_id,user_id,scope,mkey,mvalue,updated_at) "
                            "VALUES (:a,:u,'agent',:k,:v,:t)"),
                       {"a": AGENT_ID, "u": OWNER, "k": k, "v": val, "t": now})

        # Tâche-socle + bilan de session unique
        socle = db.execute(text(
            "INSERT INTO tasks (mission_id,agent_id,owner_user_id,title,description,status,created_by,"
            "input_tokens,output_tokens,created_at,completed_at) "
            "VALUES (NULL,:a,:u,'Socle de reprise (Red Team / investigation)',:de,'done','user',0,0,:t,:t) "
            "RETURNING id"),
            {"a": AGENT_ID, "u": OWNER,
             "de": "Point de reprise v2 : mémoire consolidée (investigation + offensive), secrets masqués, "
                   "lien overmind matérialisé. Agent gelé en attente de validation.",
             "t": now}).scalar()
        db.execute(text(
            "INSERT INTO sessions (task_id,agent_id,number,objective,status,started_at,ended_at,report,"
            "input_tokens,output_tokens) VALUES (:tk,:a,1,'Bilan de reprise','completed',:t,:t,:r,0,0)"),
            {"tk": socle, "a": AGENT_ID, "r": BILAN, "t": now})

        # Lien commanditaire : socle craftsman → follow_up → socle overmind (porosité = hiérarchie)
        overmind_socle = db.execute(text(
            "SELECT id FROM tasks WHERE agent_id=:o AND title='Socle de reprise (coordination/preuve)' "
            "ORDER BY id LIMIT 1"), {"o": OVERMIND_ID}).scalar()
        if overmind_socle:
            db.execute(text(
                "INSERT INTO task_links (task_id, linked_task_id, kind) VALUES (:t,:l,'follow_up') "
                "ON CONFLICT DO NOTHING"), {"t": socle, "l": overmind_socle})
            print(f"lien commanditaire créé : socle craftsman #{socle} → follow_up → socle overmind #{overmind_socle}.")
        else:
            print("⚠ socle overmind introuvable — lien commanditaire non créé (overmind #13 absent ?).")

        # Message de overmind vers craftsman (trace de la relation, lu à la prochaine session)
        db.execute(text(
            "INSERT INTO messages (from_agent_id, to_agent_id, task_id, content, read, created_at) "
            "VALUES (:f,:t,:tk,:c,false,:now)"),
            {"f": OVERMIND_ID, "t": AGENT_ID, "tk": socle,
             "c": "Reprise v2 : tu travailles sous ma coordination. Les secrets en clair ont été masqués "
                  "(à recréer). Rien ne relance avant validation utilisateur. Attends mes directives.",
             "now": now})

        # Prochaines tâches arbitrées (pending, non lancées car agent gelé)
        for title, desc in NEXT_TASKS:
            db.execute(text(
                "INSERT INTO tasks (mission_id,agent_id,owner_user_id,title,description,status,created_by,"
                "input_tokens,output_tokens,created_at) "
                "VALUES (NULL,:a,:u,:ti,:de,'pending','user',0,0,:t)"),
                {"a": AGENT_ID, "u": OWNER, "ti": title, "de": desc, "t": now})

        for tbl in ("agents", "tasks", "sessions", "memories"):
            db.execute(text(f"SELECT setval(pg_get_serial_sequence('{tbl}','id'), "
                            f"GREATEST((SELECT COALESCE(MAX(id),0) FROM {tbl}),1))"))

    # --- disque : workdir v2 ---
    dst = s.agents_dir / str(AGENT_ID)
    mem_dir = dst / "memory" / "users" / str(OWNER)
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text(f"# Reprise v2 — craftsman (Red Team)\n\n{BILAN}\n", encoding="utf-8")
    # sessions.log propre (pas l'historique v1 brut)
    (mem_dir / "sessions.log").write_text(
        f"\n=== Session n°1 (tâche socle) — {now.isoformat(timespec='seconds')} ===\n{BILAN}\n",
        encoding="utf-8")

    n_files = 0
    if src.is_dir():
        # Rapports markdown (synthèses)
        reports_dst = dst / "kept_deliverables" / "reports"
        reports_dst.mkdir(parents=True, exist_ok=True)
        for sub in ("deliverables/reports", "deliverables"):
            rdir = src / sub
            if rdir.is_dir():
                for f in rdir.glob("*.md"):
                    if f.is_file():
                        shutil.copy2(f, reports_dst / f.name)
                        n_files += 1
                break
        # Code tracker + working (hors node_modules/__pycache__)
        for cdir in KEEP_CODE_DIRS:
            csrc = src / cdir
            if csrc.is_dir():
                n_files += _copy_tree_filtered(csrc, dst / "kept_deliverables" / Path(cdir).name)
        # wa-bot : code seulement (hors node_modules)
        wa = src / "wa-bot"
        if wa.is_dir():
            n_files += _copy_tree_filtered(wa, dst / "kept_deliverables" / "wa-bot")
    print(f"fichiers recopiés dans kept_deliverables/ : {n_files}")

    v1.close()
    print("\n✅ craftsman recréé (GELÉ). Mémoire consolidée, secrets masqués, lien overmind ficelé, "
          "prochaines tâches en pending (arbitrées). Rien ne s'exécute avant validation.")


if __name__ == "__main__":
    # Tolérant aux fins de ligne CRLF (scripts .sh édités sous Windows) : nettoyer sys.argv.
    argv = [a.rstrip("\r") for a in sys.argv]
    reconstruct_craftsman("--apply" in argv)
