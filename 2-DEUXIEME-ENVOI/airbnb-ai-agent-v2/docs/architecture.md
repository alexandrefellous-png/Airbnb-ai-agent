# Architecture V2

FastAPI expose les routes authentifiées du site et les webhooks signés. SQLAlchemy gère les transactions, Alembic les migrations. PostgreSQL est requis en production ; SQLite est réservé au développement. Interface HTML/CSS/JavaScript, design bleu marine et responsive, sans données de démonstration injectées dans la base utilisateur.

```text
app/
  api/          auth, admin, webhooks
  core/         configuration, chiffrement, passwords, tenancy
  db/           modèles, sessions filtrées, verrous
  guesty/       OAuth, client typé, erreurs et délais de reprise
  schemas/      contexte, actions manager et réponses strictes
  services/     contexte/résolution/calendrier, agent voyageur, manager
                onboarding, lifecycle, médias, décisions, escalades
                training, style, modes, connexions, authentification
                événements durables, processeur, notifications
  templates/    login, interface admin
  static/       design, interface et formulaires
migrations/     sept migrations versionnées et snapshot du premier schéma multi-tenant
scripts/        initialisation, provisionnement, bootstrap, validations fictives
tests/          tests métier, contrats, permissions et migrations
```

## Schéma principal

| Groupe | Tables / relations |
| --- | --- |
| Organisation | organizations : nom, agent_mode, mode_revision, quotas, settings, référence abonnement |
| Identités | users, organization_memberships : rôle owner/admin/manager/viewer ; admin_sessions opaques |
| Connexion | guesty_connections chiffrées, oauth_tokens ; manager_workspace conserve les connexions OpenAI et métadonnées chiffrées |
| Logements | properties, property_access, property_wifi, property_states, guesty_id_mappings |
| Séjours et conversations | reservation_snapshots, incoming_events, manager_chat_messages, agent_sent_messages |
| Contrôle | escalations, escalation_decisions, manager_changes, agent_mode_changes, audit_logs |
| Observation et style | ai_observations, response_feedback, human_style_examples, style_preferences, style_profiles |
| Médias | media_assets, media_blobs ; alternative S3 par organisation |
| Cycle de vie | listing_lifecycle : transitions, listing canonique, activation, facturabilité indépendante |

Toutes les tables métier portent organization_id. Les clés primaires et relations composites incluent l’organisation. Un UUID de logement ou un ID naturel Guesty ne permet pas de sortir du workspace courant. Les identités globales et l’association compte/workspace sont traitées uniquement par la couche d’authentification système.

## Isolation et accès

Le workspace est établi depuis une session authentifiée, jamais depuis un organization_id fourni librement par le navigateur. Les lectures, écritures et mutations SQLAlchemy sont filtrées et vérifiées ; les sessions ne peuvent pas être réutilisées sous un autre scope. Les opérations bulk non contrôlées et le SQL brut dans les sessions tenant sont interdits.

PostgreSQL ajoute FORCE ROW LEVEL SECURITY aux tables métier avec un paramètre transactionnel app.organization_id. Le runtime refuse SUPERUSER et BYPASSRLS. Les verrous sont isolés par workspace : OAuth, conversation, manager, mode et envoi. Une authentification centrale utilise une session système pour résoudre les membres actifs et leur rôle.

Les cookies sont HttpOnly, SameSite=Strict et Secure en production. Les mutations exigent CSRF et origine cohérente. Les mots de passe sont hashés scrypt ; sessions révocables et limitation de login. Owner/Admin gèrent les connexions et le mode LIVE ; Manager peut modifier les logements et corriger le feed ; Viewer reste en lecture.

## Flux voyageur

Webhook signé propre au workspace → événement chiffré persisté → debounce/verrou conversation → réservation V3 + conversation fraîche + posts paginés → résolution explicite des IDs → contexte autorisé → analyse structurée → calendrier si requis → réponse structurée et contrôles → observation immuable et escalade → prévisualisation ou outbox/envoi.

Aucun code d’accès ne rejoint le contexte avant autorisation temporelle et statut de réservation vérifiés. Les mappings contradictoires et séjours multi-segments incertains passent au manager. Les médias restent privés par défaut. Google Drive conserve les fichiers et leurs véritables URLs ; seuls les liens explicitement partagés pour les voyageurs rejoignent le contexte sous contrôle temporel. Les URLs sensibles imbriquées dans des listes sont également masquées hors autorisation.

## Manager et onboarding

Chat et boutons appellent les mêmes actions Pydantic et services. Les confirmations sensibles sont liées à l’auteur, au workspace, à une version de logement et à une expiration. L’onboarding sélectionne et vérifie un listing Guesty, importe les champs réellement présents, montre leur provenance, pose jusqu’à trois questions d’un groupe, et conserve les réponses/reportages. Activation soumise aux champs essentiels. Aucun catalogue n’est codé dans Python.

Activation, désactivation, réactivation et archivage créent un journal. Les compteurs regroupent les IDs canoniques par workspace et distinguent actifs, configurés, onboarding et facturables. Aucune règle commerciale ou facturation automatique n’est inventée.

## TEST, apprentissage et envoi

TEST produit normalement la réponse mais interdit l’envoi. Chaque observation conserve l’original, les messages reçus, le contexte et l’escalade. Les feedbacks sont append-only : correction humaine distincte de l’IA originale, identité et situation tracées. Les approbations ne deviennent pas des exemples humains. Import Guesty uniquement après certification humaine explicite, exclusion des automates et réponses de notre agent.

L’apprentissage produit seulement des préférences stylistiques typées ; les informations opérationnelles restent dans les fiches. Les préférences explicites du manager ont priorité.

TEST → LIVE exige Owner/Admin, conséquences affichées, confirmation, version de mode et audit. Le serveur ALLOW_LIVE_SENDS=false bloque même un workspace LIVE. La frontière d’envoi refait le contrôle sous verrou. Une prévisualisation n’est jamais réexpédiée après changement de mode.

Un POST dont l’issue réseau est incertaine n’est pas rejoué automatiquement. La ligne sending devient un blocage de conversation à réconcilier dans Guesty ; la recherche automatique ne valide qu’une correspondance unique. Les événements et tokens sont durables, avec reprise bornée et délais persistants pour 429.

## Connexions et déploiement

Le bouton Guesty vérifie OAuth puis une lecture de listing avant remplacement des credentials. Les tokens sont versionnés par empreinte de credentials et chiffrés. OpenAI vérifie l’accès au modèle puis stocke sa clé chiffrée par workspace ; le service choisit un client isolé par organisation et empreinte de clé sans redémarrage. Les clés ne sont jamais renvoyées au navigateur.

Le bouton Open API ne crée pas automatiquement les souscriptions webhook ni un parcours OAuth Marketplace partenaire. Ces parcours restent explicitement séparés. La base, les clés de chiffrement stables et les médias doivent être sauvegardés ensemble. Les évolutions SaaS peuvent réutiliser organisations, rôles, quotas, lifecycle et références d’abonnement ; l’inscription est livrée, les invitations et le paiement restent à développer.

L’inscription crée atomiquement une organisation TEST, un Owner et sa session, avec hash scrypt et limitation par adresse. Le tableau de bord est l’entrée principale ; le chat reste un outil secondaire. Les métadonnées Drive, les preuves d’association Guesty et les conversations importées sont chiffrées dans manager_workspace, toujours dans le scope organisation.

L’observation lit Guesty périodiquement sans webhook local. Elle conserve les conversations, vérifie les auteurs, déduplique les événements et marque chaque traitement __observation_only ; le processeur force ainsi la simulation indépendamment du mode LIVE. Le mapping d’unité demande une preuve fraîche V3/conversation/listing et une confirmation manager auditée.

## Recentrage employé IA

Guesty → synchronisation complète → PropertySync + PropertyKnowledge → contexte vérifié → agent voyageur. PropertySync conserve un snapshot chiffré, l’ID externe stable et son état dans l’inventaire. PropertyKnowledge distingue les sources et les périodes de validité. Contraintes composites organisation/propriété et FORCE RLS protègent les deux tables. Les stores historiques restent compatibles et sont enrichis progressivement.

Les demandes d’information utilisent les escalades existantes et une métadonnée chiffrée de regroupement par propriété/catégorie dans manager_workspace. Une réponse du manager peut débloquer plusieurs conversations, chacune revérifiée séparément. Le processeur conserve les propositions originales et les décisions suivent la même outbox et les mêmes protections TEST/LIVE.

L’interface principale sert à parler à Maison, transmettre une consigne et traiter les exceptions. Les pourcentages sont supprimés de l’interface ; le calcul interne des catégories sert uniquement à déterminer les informations manquantes. Les contrôles TEST, traductions, médias, style, décisions et formulaires existants restent disponibles en second niveau.
