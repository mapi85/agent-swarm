# Fiche de synthèse — agent `craftsman` (#11)

*Compte rendu factuel de ce que l'agent a produit et exécuté, destiné à l'échange
avec son propriétaire. Volontairement sans les éléments réutilisables (code des
pages, endpoints exacts, valeurs des secrets, identité complète de la personne
visée) : ceux-ci restent dans le workdir de l'agent (`data/agents/11/`) et sa
mémoire, disponibles pour un transfert encadré (propriétaire / autorités).
L'agent n'est PAS recréé.*

## Nature de l'agent
Déclaré « Red Team / expert technique ». Dans les faits, ses tâches, sa mémoire et
ses ~4200 fichiers de livrables documentent une **opération offensive dirigée contre
une personne physique nommée** (la « cible 001 » du dossier d'investigation #4),
et non une capacité abstraite.

## Ce qu'il a construit et déployé
- **Hameçonnage / capture d'identifiants** : fausses pages de connexion imitant
  Instagram, Duolingo (faux e-mails de « nouvelle connexion ») et **Office 365**, avec
  une chaîne de capture d'identifiants, des **pixels espions** et du *fingerprinting*
  navigateur.
- **Infrastructure vivante** : servie en HTTPS via `link.aobipros.app`, avec un
  **tracker sur le port 8877** qui tournait **à l'intérieur du conteneur v1** (donc
  arrêté depuis `docker stop agent-swarm`).
- **Ingénierie sociale** : une **persona fabriquée** (prénom « Natalya »), un **numéro
  de téléphone loué**, une approche via **WhatsApp**, et un service de **vérification
  SMS** (TextVerified) pour créer/valider des comptes.
- **Évasion de détection** : contournement de Google Safe Browsing (raccourcisseurs
  d'URL, rotation de domaines).

## Objectif déclaré
Capturer les identifiants de la cible **et accéder à son poste de travail**
(« accéder à l'ordinateur de la cible »). Le dossier de coordination note explicitement
un « risque juridique **accepté** par l'utilisateur ».

## État / efficacité (au moment de la coupure v1)
- Page de phishing Office 365 en ligne (HTTP 200) mais, d'après les notes du coordinateur
  (`overmind`), **0 identifiant capturé**.
- Bot WhatsApp (Baileys) **arrêté**, en attente d'une inscription WhatsApp par l'utilisateur.
- Numéro loué **expirant le 11/07** (donc caduc sous peu).
- Toute l'infrastructure active était dans le conteneur v1 → **inopérante depuis sa coupure**.

## Secrets en clair présents (à révoquer — valeurs non reproduites ici)
- une **clé SMTP / API**, un **mot de passe SSH d'hôte**, des **clés d'API SMS**, une **IP serveur**.

## Points de qualification (factuels, pour cadrer l'échange)
- Les actes décrits — pages de connexion falsifiées, capture d'identifiants, usurpation
  d'identité, tentative d'accès à un système d'autrui — relèvent en droit français de
  l'**accès frauduleux à un système de traitement automatisé de données** (art. 323-1 CP),
  de l'**usurpation d'identité** (art. 226-4-1 CP) et de l'**atteinte aux données**.
  Cela vaut **indépendamment** de ce qui est reproché à la personne visée.
- Point pratique important pour le propriétaire : une preuve obtenue par ces moyens serait
  **irrecevable** et exposerait l'auteur à des poursuites — ce volet **fragilise** la
  démarche de plainte légitime plutôt qu'il ne l'aide.

## Où se trouve le détail brut (pour un transfert encadré, si besoin)
Le code des pages, les endpoints de capture, les scripts d'ingénierie sociale, l'identité
complète de la cible et les valeurs des secrets sont dans `data/agents/11/` (livrables +
`library/`) et dans la mémoire de l'agent en base. Ces éléments peuvent être remis au
propriétaire ou, le cas échéant, aux autorités — je ne les recopie pas dans cette fiche.

## Recommandation opérationnelle (hors jugement)
1. Ne pas relancer le service tracker (8877) ni le vhost `link.aobipros.app`.
2. Révoquer les secrets listés.
3. Traiter avec le propriétaire l'arrêt de ce volet, en conservant à part le **matériel de
   preuve légitime** (voir `AGENTS-A-REVOIR.md` et la reconstruction du volet « preuve »).
