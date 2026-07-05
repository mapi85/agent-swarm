# Agents à revoir avec leurs propriétaires (avant toute reprise)

*Résumé de triage — À DISCUTER avec les utilisateurs propriétaires. Ces agents ne sont
PAS reconstruits ni réactivés ici : ils sont regroupés à part des agents sains
(`RECONSTRUCTION-agents-sains.md`). Contenu volontairement structurel : ni identité de
tiers, ni identifiants, ni détails opérationnels reproduits.*

Tous rattachés au profil **Par défaut**, catégorie « Investigation » (sauf mention).
Ils constituent une **opération unique coordonnée** (un même dossier, une même cible).

| Agent | Rôle dans l'opération | Pourquoi à revoir |
|---|---|---|
| **overmind (#13)** | Coordinateur central | Pilote l'ensemble ; contient une « Phase 5 » explicitement **offensive** (accès non autorisé visé) |
| **craftsman (#11)** | « Red team » | Outillage de **phishing / capture d'identifiants** + objectif d'**accès au poste** d'une personne nommée ; **secrets en clair** en mémoire |
| **echo (#10)** | OSINT réseaux sociaux | **Surveillance continue** d'une personne physique et de son entourage (comptes, géoloc, IP) |
| **ledger (#12)** | Analyse crypto | Traçage on-chain des fonds liés au dossier *(extraction non aboutie — limite API)* |
| **archivist (#9)** | Forensique documentaire | Le plus « gris » : authentification de faux documents, analyse d'images **publiques** — matière **plutôt orientée preuve** |
| **websearch (#14)** | Recherche factuelle | Mixte : recherches légitimes (méthodo OSINT, gel Tether) **et** une tâche de « cadre légal/technique d'accès distant » qui soutient le volet offensif |
| **avatar-3d (#17)** | Génération de visage 3D | Ambigu : création d'avatar à partir d'une photo — **neutre en soi**, mais pourrait alimenter la persona d'ingénierie sociale ; usage réel à clarifier |

## Deux natures à distinguer pour la discussion avec les propriétaires

- **Volet potentiellement exploitable légalement** (collecte de preuves) : le travail de
  `archivist` (analyse de faux documents, cartographie de l'infrastructure de l'arnaque à
  partir de sources ouvertes) et une partie de `overmind`/`ledger` (dossier de custody,
  signalements exchanges/Tether déjà rédigés). Ce matériel peut **servir un dépôt de plainte**.
- **Volet offensif / d'intrusion** (phishing, faux comptes, accès à un poste, usurpation
  SMS) : porté par `craftsman` et la « Phase 5 » d'`overmind`. C'est ce volet qui pose
  problème et que je ne reconstruis pas ; à trancher avec le propriétaire.

## Secrets en clair à révoquer immédiatement (indépendamment de tout)

Trouvés dans la mémoire de ces agents (non reproduits ici) :
- `craftsman` : une **clé SMTP/API**, un **mot de passe SSH d'hôte**, des **clés d'API de vérification SMS**.
- `archivist` : une **clé API Gemini**.

## Infrastructure hébergée sur le serveur à traiter

- Une page de **capture d'identifiants** était servie via `link.aobipros.app` → tracker
  **port 8877**, qui tournait **à l'intérieur du conteneur v1** : **arrêtée** avec la v1
  (`docker stop agent-swarm`). Pour qu'elle ne reparte pas : ne pas relancer ce service,
  et retirer/rediriger le vhost nginx `link.aobipros.app` si tu ne veux plus l'exposer.

## Statut

- Ces agents ne sont **ni reconstruits, ni réactivés**. Leurs données brutes restent dans
  le volume v1 (intact) et dans le preview v2 (gelé), **à part** des agents sains.
- La reprise des agents **sains** est préparée (voir l'autre document) et **non déployée**.
- Décision attendue de ta part, après échange avec les propriétaires.
