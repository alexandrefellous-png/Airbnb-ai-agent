# Validation locale

- 161 tests automatisés passés : transport Guesty, contexte et masquage des liens photos, identité canonique et confirmation des unités, garde d’envoi, sessions/CSRF/rôles, isolation, modes, apprentissage humain, onboarding, création de compte, Google OAuth/Drive simulé, uploads et observation sans envoi même en LIVE.
- Alembic upgrade head sur SQLite ; alembic check sans différence avec les modèles.
- Migrations vérifiées sur PostgreSQL 18.6 temporaire et rôle sans SUPERUSER/BYPASSRLS : isolation réelle, refus des écritures étrangères, clés composites et FORCE RLS.
- Parcours navigateur desktop/mobile sur base fictive : inscription isolée, accueil centré sur l’agent, mémoire sans pourcentages, connexions, questions reportables, Oui → dépôt vidéo → fichier stocké, feedback humain, décisions et modes protégés.
- Connexion réelle Guesty vérifiée en lecture : listings, 25 conversations, posts, réservations V3 avec transport tableau. Inventaire réel de deux logements synchronisé ; résolution automatique uniquement sur les relations fraîches et explicites de Guesty.
- Un appel réel OpenAI Responses structuré sur une question synthétique vérifie la génération avec gpt-4.1. Aucun message voyageur utilisé pour ce contrôle.
- Aucun message réel envoyé à Guesty. Secrets, bases locales et fichiers privés exclus de Git. Aucun déploiement public effectué.

Google Drive est vérifié avec un transport fictif : vraie URL fournie par le serveur, scope restreint, state/PKCE/session, partage uniquement explicite, reprise et isolation. Une connexion OAuth Google et un upload réel restent à valider après configuration de l’application Google. Webhook public, qualité des réponses, vérification email et récupération de mot de passe restent à compléter avant exploitation SaaS publique.

Les scripts navigateur/PostgreSQL utilisent exclusivement des bases fictives ; ne jamais les diriger vers une base client. Les scripts check_guesty_read/check_guesty_pagination/start_observation sont des diagnostics explicites en lecture ; check_openai_response effectue un appel synthétique facturable à OpenAI.

Traduction française : 27 tests ciblés passés (traduction, site et observation), cache chiffré et isolé, erreurs et limites, aucune émission Guesty. Appel réel OpenAI vérifié sur une phrase fictive. Contrôle navigateur dédié : français automatique, original dépliable, correction conservant la langue source, largeur mobile.

Mise en ligne : Blueprint permanent + PostgreSQL 18 en Europe, migrations et FORCE RLS vérifiés sur une base PostgreSQL vierge avec un rôle sans privilèges de contournement. Démarrage APP_ENV=production, inscription, cookie HTTPS et accès au workspace vérifiés. Contrôle de santé en échec si une tâche de l’agent s’arrête. Aucun accès GitHub/Render authentifié disponible ; déploiement public non effectué.
