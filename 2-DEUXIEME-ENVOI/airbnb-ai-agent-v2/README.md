# Airbnb / Guesty AI — V2

Projet indépendant créé dans `/Users/alexfellous/Desktop/airbnb-ai-agent-v2`. Le projet V1 reste intact. Application Python modulaire, interface française bleu marine, organisations isolées, aucun logement hardcodé dans le code métier.

## Mise en ligne H24

Voir le [guide GitHub → Render](docs/mise-en-ligne.md). Le Blueprint crée un serveur sans mise en veille et une base PostgreSQL persistante, en TEST avec les envois réels bloqués. Aucun secret ni aucune donnée client ne sont inclus dans le code.

## Démarrer en local

```bash
cd /Users/alexfellous/Desktop/airbnb-ai-agent-v2
source .venv/bin/activate
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Ouvrir http://127.0.0.1:8000/admin. Le compte initial est `admin`, avec le mot de passe initial généré dans `private/local.env` sous `ADMIN_SECRET`. Ce fichier est privé et exclu de Git. Les comptes utilisent ensuite un hash de mot de passe en base : changer la variable ne change pas un compte existant.

Les clés OpenAI et Guesty sont configurées par workspace depuis le site et chiffrées en base. Aucune clé réelle ne doit figurer dans Git. Les IDs sont vérifiés auprès de Guesty.

## Connexions depuis le site

Dans **Connexions**, un Owner/Admin peut connecter les fournisseurs sans redéployer.

- **Connecter OpenAI** : créer une clé dans https://platform.openai.com/api-keys, la saisir dans le formulaire. Le serveur vérifie l’accès au modèle configuré, chiffre la clé par workspace et l’active immédiatement. Cette vérification ne génère pas de réponse : un échange en TEST reste nécessaire pour valider la génération et la facturation API.
- **Connecter Guesty** : dans Guesty, ouvrir Integrations → Developer tools → OAuth applications et créer une application Open API. Saisir son Client ID et son Client Secret dans le site. Le bouton obtient/réutilise un token puis vérifie l’accès en lecture aux logements. Il conserve la connexion précédente si la vérification échoue. Les credentials et tokens sont chiffrés et isolés par organisation.
- **Webhook Guesty** : publier le site en HTTPS, souscrire les événements `reservation.messageReceived` à l’URL propre au workspace affichée dans Réglages, puis renseigner le secret de signature correspondant. Une connexion API seule ne crée pas une souscription webhook. Le localhost n’est pas accessible directement depuis Guesty.

Le bouton Open API nécessite une saisie initiale des credentials. Une connexion par autorisation Marketplace pour un produit distribué demande un parcours partenaire Guesty distinct, à convenir avec Guesty. Guide officiel : https://open-api-docs.guesty.com/docs/quick-start-guide.

Ne transmettre aucune clé dans le chat manager ou une conversation d’assistance.

## TEST et LIVE

Chaque workspace démarre avec `agent_mode=test`. Les réponses sont générées et stockées dans le feed **AI WOULD REPLY**, sans envoi Guesty. Approbations, corrections et entraînement ne rejouent jamais une prévisualisation.

Le manager comprend « Passe l’agent en production », « Active les réponses réelles » et « Remets l’agent en test ». TEST → LIVE exige un Owner/Admin, une proposition explicite des conséquences, une confirmation et un audit. Le retour TEST est immédiat. Le badge reste visible.

Le serveur exige aussi `ALLOW_LIVE_SENDS=true` et `APP_ENV=production` pour permettre un envoi. La valeur livrée est **false**. `TEST_MODE=true` reste une sécurité de configuration initiale ; le mode opérationnel est celui stocké en base. Le client Guesty recontrôle les autorisations à la frontière d’envoi sous verrou.

## Utiliser les logements et le manager

Guesty connecté déclenche la découverte de tous les logements accessibles. Maison synchronise ensuite l’inventaire toutes les cinq minutes, sans filtre active/listed, avec pagination. Le Listing ID canonique est unique par workspace. Les logements disparus ou désactivés restent en mémoire, mais l’agent bloque leurs réponses opérationnelles. Un échec de pagination ne supprime aucune connaissance.

L’accueil est centré sur l’agent et les interventions utiles. **Mes biens** permet de consulter sa mémoire, ajouter des médias et parler de chaque logement ; les formulaires sont secondaires. Aucun pourcentage de remplissage n’est affiché. L’ajout manuel ne sert qu’aux biens hors intégration. Les propositions TEST et traductions sont dans **Améliorer Maison**, les conversations Guesty repliées par défaut.

« CAIRE1 l’ascenseur remarche » modifie son état temporaire sans effacer ses caractéristiques permanentes. Les mêmes services alimentent chat, boutons et formulaires. Les codes sont masqués par défaut ; leur consultation est explicite et auditée. Les fichiers sont stockés en base chiffrée ou S3, limités et rattachés à un logement après confirmation.

Les IDs listing, unit et unit_type ne sont jamais assimilés. Pour un listing synchronisé, la relation est vérifiée depuis une réservation V3 unique, sa conversation et une lecture fraîche du listing canonique. Une association manuelle reste disponible pour les cas ambigus. Les contradictions ou réservations multi-segments ambiguës passent en escalade.

Les états onboarding / active / inactive / archived sont persistants et journalisés. Un listing désactivé conserve son historique. Les compteurs par workspace et le flag indépendant `is_billable` préparent une facturation par listing ; aucun abonnement ni paiement n’est actuellement exécuté.

Le feed TEST conserve la réponse IA originale et les corrections humaines séparément. Seuls des exemples humains validés alimentent le style. Une approbation IA ne devient jamais un exemple humain. Les préférences manuelles ont priorité et le profil stylistique ne peut pas introduire de faits opérationnels.

## Nouveau poste ou workspace

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
python -m scripts.init_local
alembic upgrade head
python -m scripts.create_workspace --name "Ma conciergerie" --email "owner@example.com"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Le script demande le mot de passe sans le placer dans les arguments. Sur une base migrée depuis le premier schéma, préciser `--organization-id` pour rattacher le premier propriétaire au workspace de reprise affiché en base. Les organisations créées par le script ont un ID propre et aucune entreprise unique n’est hardcodée.

Un catalogue privé peut être importé avec `python -m scripts.bootstrap private/properties.json --organization-id ID`. Ce script n’écrase pas les fiches existantes et crée des brouillons inactifs. Les nouveaux logements peuvent ensuite être ajoutés entièrement depuis le site.

## Configuration et production

`DATABASE_URL` : SQLite local, PostgreSQL obligatoire en production. `TOKEN_ENCRYPTION_KEY` : clé Fernet stable à sauvegarder séparément ; la perdre rend les secrets existants illisibles. `OPENAI_MODEL` : modèle Responses/Structured Outputs, défaut `gpt-4.1`. `OPENAI_API_KEY` : fallback facultatif fourni par l’opérateur ; préférer la connexion chiffrée par workspace. `ALLOW_LIVE_SENDS=false` : verrou global. `WORKER_ENABLED` : traitement durable des événements. `MEDIA_STORAGE=database|s3`, bucket/région/endpoint S3 facultatifs. Un seul worker Uvicorn pour SQLite.

`render.yaml` prépare un déploiement web + PostgreSQL, sans rien publier. Exécuter les migrations avant démarrage puis créer un Owner. Utiliser HTTPS et un rôle PostgreSQL sans SUPERUSER/BYPASSRLS : le serveur refuse ces rôles en production. Les tables métier ont des politiques FORCE RLS et des références composites incluant l’organisation. Les sessions d’authentification sont gérées par une couche système séparée. Pour les advisory locks, utiliser une connexion directe plutôt qu’un pooler transactionnel.

La création de compte est disponible dans /admin/register et crée un workspace TEST isolé. Les invitations, la vérification email, la récupération de mot de passe, la facturation et le portail super-admin restent des évolutions ; les organisations, rôles, quotas et références d’abonnement sont préparés sans simuler une facturation.

## Vérifier

```bash
pytest -q
alembic check
```

Tests synthétiques : contexte, résolutions Guesty, transport, garde d’envoi, isolation, permissions, confirmations, corrections, onboarding et migrations. Aucun test n’utilise de credentials réels. Les parcours navigateur sont vérifiés avec `scripts.browser_fixture` et `scripts/check_browser.py` sur une base fictive séparée. `scripts/check_postgres.py` valide les migrations/RLS sur un PostgreSQL temporaire de test, jamais sur une base client.

La lecture réelle Guesty (conversations, posts et réservations V3) et un appel OpenAI Responses structuré ont été vérifiés. La qualité des réponses reste à valider dans le feed TEST. Un POST dont le résultat est incertain n’est jamais renvoyé automatiquement ; il bloque la conversation jusqu’à réconciliation explicite dans Guesty. Les pièces jointes des voyageurs ne sont pas analysées visuellement. Les fichiers uploadés sont consultables par le manager authentifié ; un lien externe d’accès fourni et validé peut être utilisé dans les réponses, les liens Drive explicitement partagés pour les voyageurs sont disponibles selon les mêmes contrôles d’accès que les codes. Les fichiers privés ne sont jamais placés dans leur contexte.

Voir [architecture](docs/architecture.md), [contrats Guesty](docs/guesty-contracts.md) et [validation](docs/validation.md).

## Observation continue des messages

Dans **Améliorer Maison**, « Lire mes messages Guesty » importe les conversations et prépare les propositions TEST. Le suivi automatique est activable par workspace, avec une lecture toutes les 60 secondes, 25 conversations récentes puis une page supplémentaire tournante. Jusqu’à cinq nouveaux messages récents sont traités par lecture ; les événements sont dédupliqués. Le service d’observation reste toujours sans envoi, même si le workspace passe LIVE. Sur Render payant, le processus tourne même quand le Mac est éteint. Activer le suivi automatique dans le workspace hébergé ; GitHub seul n’exécute pas l’agent.

Maison résout automatiquement les relations explicites réservation/conversation/listing des biens synchronisés. Il ne suppose jamais que unitId, unitTypeId et listingId sont égaux. Une contradiction bloque la réponse et crée une demande de vérification. Les anciennes alertes d’association sont clôturées seulement après nouvelle vérification réussie.

## Mémoire et apprentissage utile

La migration 5fcbbed24089 ajoute `property_sync` et `property_knowledge` : propriété et organisation, valeur chiffrée, source/référence, confiance, permanence ou temporalité, dates et état actif. Les connaissances existantes sont reprises dans ce registre sans supprimer les stores d’accès/Wi-Fi. Les faits opérationnels Guesty sont prioritaires dans leur domaine ; les précisions du manager sont conservées séparément et prioritaires sur les inférences. Une inférence incertaine ne rejoint jamais le contexte voyageur.

Les textes Guesty sont lus progressivement en arrière-plan (au plus trois nouveaux documents de logements par passage). Une extraction exige une valeur et une citation copiées du document exact ; les résultats sont chiffrés et mis en cache. Les données structurées sont importées immédiatement, sans attendre cette lecture. Les secrets restent dans les stores d’accès séparés et les règles temporelles continuent à s’appliquer.

Une question sans information connue crée une demande dédupliquée par logement et catégorie. La réponse naturelle du manager peut enregistrer plusieurs champs ; les changements sensibles conservent leur confirmation. Maison reprend ensuite les conversations encore en attente, relit Guesty et prépare la réponse via le même système d’envoi sécurisé. En TEST, elle reste une proposition ; en LIVE, un envoi nécessite aussi le verrou serveur ouvert. Les événements d’observation restent toujours en TEST. Les demandes déjà répondues par un hôte et les événements anciens ne sont pas relancés.

Les autorisations explicites `early_checkin_from` et `late_checkout_until` permettent de traiter les horaires dans leur plage sans modifier les horaires Guesty. Les règles conditionnelles restent du texte et nécessitent une clarification si leur application est incertaine. L’agent n’autorise aucun remboursement ou geste commercial de lui-même.

## Photos, vidéos et Google Drive

Répondre « Oui » à une question média ouvre le dépôt du fichier pour le logement choisi. L’enregistrement local chiffré est conservé si Drive échoue, avec un bouton de reprise. La réponse Oui seule ne complète pas la fiche.

Une application Google OAuth doit être créée avec l’API Drive activée et le scope drive.file. Configurer son Client ID/Secret dans Connexions (ou GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET côté serveur). Enregistrer exactement l’URL callback affichée, puis autoriser son compte via **Connecter Google Drive**. PUBLIC_BASE_URL doit correspondre à l’adresse HTTPS publique en production. Les tokens sont chiffrés par workspace ; state, PKCE et session empêchent l’échange entre comptes.

Une fois connecté, le dépôt crée automatiquement le fichier dans le dossier Drive du logement et conserve son véritable webViewLink. Les fichiers sont privés par défaut. La case explicite de partage voyageur autorise un lien accessible sans compte ; elle seule permet de fournir ce lien à l’agent. Aucun faux lien Drive n’est généré. La connexion Google réelle et un upload réel restent à vérifier après configuration Google.

## Lecture en français

Les messages et propositions visibles sont automatiquement traduits en français. « Voir l’original » conserve le texte source ; les formulaires de correction utilisent toujours la réponse originale. Les traductions sont chiffrées et mises en cache par workspace, limitées à 300 nouveaux textes par heure ; une erreur affiche l’original et permet de réessayer. Cette traduction ne modifie ni les messages Guesty, ni les exemples d’apprentissage, ni la langue des réponses envoyées.
