# Mettre Maison en ligne sur GitHub et Render

## 1. GitHub

Créer un **nouveau dépôt privé** `airbnb-ai-agent-v2` dans le compte `alexandrefellous-png` : https://github.com/new.

Décompresser `Maison-GitHub.zip`. Dans le dépôt vide, choisir **uploading an existing file**, puis déposer **le contenu du dossier** : `app`, `scripts`, `migrations`, `render.yaml`, etc. `render.yaml` doit être à la racine du dépôt. Valider avec **Commit changes**. Ne jamais ajouter `private`, `.env`, `agent.db` ou `.venv`.

Le dépôt local est aussi prêt à pousser avec Git ; l'archive permet de publier sans installer d'outil supplémentaire. GitHub conserve le code ; Render exécute l'agent.

## 2. Render

Sur https://dashboard.render.com, choisir **New → Blueprint**, connecter GitHub et autoriser uniquement le nouveau dépôt. Sélectionner `airbnb-ai-agent-v2`.

Render lit automatiquement `render.yaml` :

- un serveur Python permanent en Europe (`frankfurt`, 512 Mo) ;
- une base PostgreSQL 18 persistante dans la même région, 5 Go ;
- les migrations avant chaque démarrage ;
- le traitement continu des messages et la synchronisation des logements ;
- `TEST_MODE=true` et `ALLOW_LIVE_SENDS=false` ;
- une vérification de santé de la base et des tâches de l'agent.

**Ces deux ressources sont payantes.** Vérifier le total affiché par Render avant de créer les ressources. L'offre gratuite met les services en veille et ne convient pas au fonctionnement permanent. Tarifs : https://render.com/pricing. Le coût des appels OpenAI s'ajoute à l'hébergement.

Render demande `TOKEN_ENCRYPTION_KEY`. Cette clé Fernet protège les connexions et les données en base. Pour cette installation, sa valeur est dans le fichier local privé `private/render-secrets.env` ; copier uniquement la valeur après `=` dans le champ Render. **Ce fichier n'est pas dans l'archive GitHub et ne doit jamais être publié.** Sauvegarder cette clé séparément et la conserver lors des redéploiements.

Sur une autre installation, générer une clé avec :

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Cliquer sur **Deploy Blueprint**. Attendre que la base et le service soient disponibles, puis ouvrir l'adresse HTTPS indiquée par Render, suivie de `/admin`. L'URL publique est automatiquement reconnue par Maison.

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
