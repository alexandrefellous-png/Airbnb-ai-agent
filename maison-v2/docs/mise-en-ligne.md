# Mettre Maison en ligne sur GitHub et Render

## 1. GitHub

Dans le dépôt existant `alexandrefellous-png/Airbnb-ai-agent`, ouvrir Add file → Upload files. Glisser uniquement le dossier `maison-v2` du Bureau, puis Commit changes. Ce dossier de déploiement contient moins de 100 fichiers. Les tests et outils de validation restent disponibles dans le projet source et l’archive complète ; ils ne sont pas nécessaires au serveur.

Conserver `airbnb-ai-agent-v1` intact. Les anciens dossiers `1-PREMIER-ENVOI` et `2-DEUXIEME-ENVOI` ne sont pas utilisés pour ce déploiement.

## 2. Render

Créer un Blueprint depuis ce dépôt et sélectionner le chemin `maison-v2/render.yaml` pour le fichier Blueprint. Le service utilise `rootDir: maison-v2` : ses commandes s’exécutent dans le bon dossier. Les ressources sont payantes ; vérifier le total affiché avant création. Configuration : serveur permanent et PostgreSQL en Europe, TEST_MODE=true, ALLOW_LIVE_SENDS=false.

La clé TOKEN_ENCRYPTION_KEY se trouve uniquement sur le Mac dans `airbnb-ai-agent-v2/private/render-secrets.env`. Copier sa valeur dans le champ privé demandé par Render, jamais dans GitHub. Aucun fichier privé n’est inclus dans le dossier d’envoi.

## 3. Premier accès en ligne

Cliquer sur **Créer mon compte** et choisir ses propres identifiants. Aucun code fourni par le développeur n'est nécessaire. Chaque inscription crée un workspace isolé en TEST.

La nouvelle base Render est vide : les comptes, clés de connexion, fichiers et conversations de la version locale **ne sont pas inclus dans le dépôt Git**. Ils restent conservés sur le Mac. Créer son compte en ligne et reconnecter Guesty et OpenAI depuis **Mes outils** suffit pour démarrer une nouvelle observation. Une reprise des données locales exige une migration privée de la base avec la même clé de chiffrement ; publier l'archive ne réalise pas cette migration.

Après connexion Guesty, les logements sont découverts automatiquement. Dans **Améliorer Maison**, activer la lecture automatique des messages Guesty. Les nouvelles propositions apparaissent en TEST, sans envoi réel. La génération nécessite également une connexion OpenAI valide.

Pour recevoir les événements en temps réel, configurer le webhook Guesty avec **l'URL exacte affichée dans Maison** et son secret de signature. Ne pas inventer l'URL ou un identifiant. L'observation périodique peut fonctionner indépendamment du webhook et reste toujours sans envoi.

Google Drive exige une connexion Google configurée avec le callback HTTPS affiché par Maison ; la publication sur Render ne connecte pas automatiquement Drive.

## Vérification avant de fermer le Mac

1. Le déploiement Render est **Live**, `/health` répond `ok: true` et `allow_live_sends: false`.
2. Le site HTTPS permet de se connecter, Guesty/OpenAI sont connectés et le suivi automatique est activé.
3. Une nouvelle proposition TEST apparaît dans **Améliorer Maison** après un nouveau message admissible. Les anciens messages ne sont pas rejoués indéfiniment.
4. Arrêter le serveur local, puis vérifier que le site Render et la lecture continuent. Cela évite de faire tourner deux observateurs sur deux bases différentes.

Les mises à jour GitHub redéploient le serveur ; les données restent dans PostgreSQL. La base payante bénéficie des sauvegardes Render : vérifier leur disponibilité dans le tableau de bord et conserver aussi la clé de chiffrement. Un serveur permanent peut connaître des pannes ; ce premier déploiement n'est pas une infrastructure multi-région à disponibilité garantie.

Documentation officielle : [Blueprint](https://render.com/docs/blueprint-spec), [plans](https://render.com/docs/compute-plans), [limites gratuites](https://render.com/docs/free), [PostgreSQL](https://render.com/docs/postgresql-creating-connecting).
