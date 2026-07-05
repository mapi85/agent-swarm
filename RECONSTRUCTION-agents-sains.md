# Dossier de reprise — agents SAINS (préparé, NON déployé)

*Reconstruction curée des agents légitimes, prête à appliquer sur ton feu vert.
Rien n'est écrit en base : c'est un plan de reprise à valider. Les agents « à revoir »
sont traités à part (voir `AGENTS-A-REVOIR.md`).*

Périmètre : profil **Par défaut** (trading) + profil **Démo** (veille). Agents systèmes
et croisements de profil : voir la note en fin de document.

---

## agent-generator (#3) — usine de configurations
**Conserver** : oui (réutilisable). **Tâches actives** : aucune (toutes faites) → agent gardé sans tâche.

**Artefacts** : garder `library/` (20 outils : backtesting, `safety/`, `order_queue`, `cache_manager`, `async_http_client`, `task_completion_discipline.md`). Écarter les 293 livrables (configs d'agents déjà créés, en base — outputs consommés).

**Mémoire consolidée (95 → ~9)** :
- rôle : génère configs (system prompt + outils function-calling) d'agents autonomes
- conventions : config JSON + prompt .md, `response_format` JSON forcé, optimisation tokens (~84 %)
- roster créé (versions finales) : orchestrator 1.0 · buyer/seller/swapper 1.0 · executor 2.0 (OrderQueue SIGUSR1, 1.9 ms) · overmind 2.0 (port 8096) · websearch 2.0 (repli offline) · avatar-3d 1.0 · resc 1.1
- outils dans library/ : safety, order_queue, backtesting
- discipline : `task_completion_discipline.md` (règle unfinished_task_ids)

**Bilan unique** : agent-usine de l'essaim ; a produit les configs des agents spécialisés ; génération terminée, en veille, disponible pour concevoir/mettre à jour des agents.

---

## agent-orchestrator (#4) — supervision trading
**Conserver** : oui. **Tâches** : 0 active (piloté par cadence de session, pas par file). #105/#106 = rapports obsolètes sur position clôturée → écartés. Rien à ressusciter.

**Artefacts** : garder `library/order_validator.py` + `library/macd_corrected.py`. Aucun livrable disque.

**Mémoire consolidée (~26 → 11)** :
- role : supervision, validation ordres (BUY/SELL/SWAP), gestion risque, arbitrage (Seller > Buyer > Swapper)
- system_mode : PAPER, capital initial 10 000 USDT
- capital_courant : equity ~$9 497 (-5,03 %), 0/5 position, 100 % CASH (mode PASSIVE_SURVEILLANCE)
- realized_ledger : net -$359,01 (BNB +9,33 / BTC1 -289,71 / XRP -94,86 / BTC2 +16,23)
- macd_bug_fix (CRITIQUE) : bug S88 EMA mal alignées → MACD faussé ; fix aligner EMA depuis la fin, calcul inline Python depuis S108
- reentry_criteria : 3 conditions cumulées (MACD hist repasse positif + volume > 1,0x + reconquête $63 000)
- watchlist : BTC/XRP/ETH/SOL ; supports $62K/$61,5K/$60K
- data_source : `trading_state.json` (⚠ actuellement sous le dossier de l'agent #3, voir note)

**Bilan unique** : système 100 % cash (equity ~$9 497), sans position depuis la clôture défensive de BTC2 (+$16,23). MACD 4h en croisement baissier, volume très bas. En PASSIVE_SURVEILLANCE, 0/3 critères de réentrée. Prochaines étapes : surveiller re-croisement MACD positif + retour du volume + reconquête $63 000, sinon rester cash.

---

## agent-buyer (#5) — achat trading
**Conserver** : oui. **Tâches** : 2 done, rien à ressusciter (aucune erreur technique).

**Artefacts** : garder `library/composite_scorer.py` + `library/market_data.py`. Écarter livrables (ordre consommé, rapports redondants).

**Mémoire consolidée (~30 → 8)** :
- config : PAPER, watchlist BTC/ETH/SOL/BNB/XRP, cycle 4h, seuil composite 70/100, levier max 3x, max 5 positions
- etat : 100 % cash, aucune position
- regime : MACD 4h bearish confirmé, Fear&Greed 23 (extreme fear), composite global ~32
- prerequis_reentree : 0/3 (MACD hist positif montant, volume > 1x, reclaim $63K)
- historique : 4 trades fermés, net ≈ -$358,6
- leçon : ordres S#15 (Task #46) jamais exécutés par executor → toujours confirmer l'exécution
- outils : composite_scorer + market_data (scoring + données live)

**Bilan unique** : acheteur en paper trading, 100 % cash, marché en fear extrême, aucun candidat > seuil 70. Ré-entrée seulement si les 3 prérequis réunis. Point d'attention systémique : des ordres passés n'ont jamais été exécutés côté executor.

---

## agent-seller (#6) — vente trading
**Conserver** : oui. **Tâches** : 2 done, rien à ressusciter.

**Artefacts** : `library/` et livrables vides sur disque — tout est en mémoire.

**Mémoire consolidée (5 + MEMORY.md → 8)** :
- active_position : POS_063394AB2672 BTCUSDT LONG 2x, entry $60 350, TP1 $63K / TP2 $64K, SL $59 300, uPnL +$9,93 → HOLD
- closed_positions : 4 trades (BNB +9,33 / BTC -144,63 puis -144,27 / XRP -94,80)
- metrics : win rate 25 %, PnL réalisé -$374,37, non réalisé +$9,93
- strategy : no-panic/maximize-PnL ; TP→100 % / SL→100 % ; funding > 0,5 % marge → fermeture
- reversal_criteria (LONG, 4 requises) : RSI > 70 + divergence MACD baissière + prix < MA50 + volume en hausse
- sell_order_format : {type:SELL_ORDER, position_id, symbol, close_percentage, reason}
- infrastructure : queue `orders/pending_orders.json` + `trading_state.json` (⚠ sous dossier agent #3)

**Bilan unique** : vendeur en paper trading, infrastructure opérationnelle. Une position BTC LONG ouverte et profitable (+$9,93), en HOLD (inversion 1/4, MACD en recovery vers TP1). Historique : 4 clôturées, win rate 25 %, réalisé -$374,37. Prêt pour reprise du monitoring événementiel.

> ⚠ Incohérence à trancher : une clé mémoire disait « 100 % cash », contredite par la position active → deux positions différentes (une clôturée, une ouverte). À vérifier contre `trading_state.json`.

---

## agent-executor (#8) — exécution trading
**Conserver** : oui. **Tâches** : RESSUSCITER #46 (3 ordres avortés sur *stagnation* = échec technique) → pending ; écarter les done (#16/#17/#24/#25/#27/#80).

**Artefacts** : garder `library/execute_order.py` + `library/execute_buy_s72.py`. Écarter la confirmation d'une position clôturée.

**Mémoire consolidée (8)** :
- config : PAPER, Bitunix (ccxt), capital $10 000, glm-5.2
- statut : 100 % CASH / STANDBY, toutes positions fermées
- capital : dispo $9 496,86, drawdown -5,03 %
- trading_state : `trading_state.json` (⚠ sous dossier agent #3)
- dernière_position : BTC2 clôturée +$16,23 (TP1 + close défensif)
- réentrée : 0/3, attente signal orchestrateur
- règles_exécution : DRY_RUN/PAPER/LIVE ; précision USD/EUR 2 déc., size 8 déc.

**Bilan unique** : exécutant PAPER, 100 % cash en veille. A exécuté plusieurs cycles (BNB/XRP/BTC) et des clôtures défensives ; dernier trade BTC2 soldé +$16,23. Une tâche technique (#46, 3 ordres avortés sur stagnation) à relancer, mais seulement si l'orchestrateur réémet des ordres frais.

> ⚠ Mémoire structurée « IN POSITION » périmée (≈S90) vs MEMORY.md « 100 % cash » (S97) : j'ai retenu MEMORY.md.

---

## agent-bi-specialist (#15) — veille marché [profil Démo]
**Conserver** : oui. **Tâches** : RESSUSCITER/garder #64 (Synthèse hebdo, jamais faite) → pending ; écarter #63 (done).

**Artefacts** : garder `library/` (5 fichiers : `market-erp-hosting-france.md`, `rss-monitor.py` v2, `boamp-monitor.py` v2, `generate-dashboard.py`, `run-veille.sh`) + livrables `bulletin-veille-2026-07-02.md` + `deep-dive-concurrents-emergents-2026-07.md`. Écarter les dumps bruts JSON et le dashboard HTML (régénérable).

**Mémoire consolidée (9 → 8)** :
- concurrents_cloud_top5 : AWS, Azure, GCP, OVHcloud, Scaleway
- concurrents_erp_top5 : SAP, Cegid, Sage, Microsoft Dynamics, Oracle
- concurrents_emergents : Bleu/Delos Cloud (Thales+MS), ChapsVision (souveraine, remplace Palantir DGSI), Alibaba Cloud FR (Paris)
- signaux_2026 : souveraineté, NIS2, DGSI→ChapsVision, DMA, post-quantique FR
- veille_boamp_ref : 995 marchés (Infogérance 384, Logiciel 202, Refonte SI 171…)
- sources : Silicon.fr, LeMondeInformatique, JDN, ZDNet, OVHcloud, Scaleway, SAP News, BOAMP
- outils_status : rss-monitor v2 (7/9 sources), boamp-monitor v2, dashboard v1, run-veille.sh v2
- prochaine_veille : scraper Cegid, automatiser dashboard, matching BOAMP↔critères, bulletin n°4

**Bilan unique** : agent de veille marché ERP & hébergement France (3 sessions). Base de connaissances + chaîne de surveillance Python (RSS 7/9, BOAMP enrichi, dashboard). 489 signaux collectés, deep-dive de 3 concurrents souverains, bulletin n°3. Reste à faire : scraper Cegid, automatiser le dashboard, rapport de synthèse hebdo (#64).

---

## Notes transverses (à traiter à la reconstruction)

1. **Fichier d'état de trading partagé** : `trading_state.json` et `orders/pending_orders.json` sont rangés **sous le workdir de l'agent #3** (`data/agents/3/…`) mais utilisés par orchestrator/buyer/seller/executor. À la reprise v2 : soit les déplacer dans un espace commun (ressource partagée / tâche-socle liée), soit préserver ce chemin et relier les agents de trading via des **tâches liées** (porosité) pour qu'ils y accèdent proprement.
2. **Déclencheurs** : les agents de trading fonctionnent par **cadence de session** (cron 4h) plus que par file de tâches. En v2, prévoir la planification adéquate — mais **rien n'est lancé** tant que tu ne le décides pas (agents gelés, relance manuelle).
3. **Profil Démo** : `bi-specialist` seul agent ; tâche #64 en attente.
4. **Croisement Démo↔echo** : le profil Démo utilise 1 fois `agent-echo` (agent à revoir) — dépendance à trancher avec les propriétaires, pas reconstruite ici.

*Statut : PRÉPARÉ, NON DÉPLOYÉ. Aucune écriture en base, aucun agent réactivé.*
