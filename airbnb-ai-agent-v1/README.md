# Airbnb AI Agent — Guesty

V1 de l'agent de messagerie Airbnb via Guesty.

## Sécurité de la V1

- `TEST_MODE=true` par défaut : **aucun message n'est envoyé au voyageur**.
- L'agent ne se déclenche que sur `reservation.messageReceived`.
- Un logement Guesty inconnu n'est jamais auto-répondu.
- Les demandes sensibles retournent `ESCALATE` et ne sont pas envoyées.
- Les secrets Guesty/OpenAI doivent être stockés uniquement dans les variables d'environnement Render.

## Render

Build command:

    pip install -r requirements.txt

Start command:

    uvicorn main:app --host 0.0.0.0 --port $PORT

Variables d'environnement:

    GUESTY_CLIENT_ID=...
    GUESTY_CLIENT_SECRET=...
    OPENAI_API_KEY=...
    OPENAI_MODEL=gpt-5.6
    TEST_MODE=true

## Important avant le premier test

Dans `main.py`, remplacer:

    PUT_GUESTY_LISTING_ID_HERE

par l'identifiant Guesty réel du logement "31 rue du Caire".

## Webhook Guesty

Quand Render fournit l'URL publique, l'endpoint sera:

    https://VOTRE-SERVICE.onrender.com/guesty/webhook

Il faut ensuite enregistrer cet endpoint dans Guesty pour l'événement:

    reservation.messageReceived

## Passage en production

Ne passer `TEST_MODE=false` qu'après avoir vérifié plusieurs messages réels dans les logs Render.
