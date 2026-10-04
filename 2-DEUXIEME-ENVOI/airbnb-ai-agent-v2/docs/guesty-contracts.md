# Contrats officiels consultés

Vérification documentaire effectuée le 2 octobre 2026. Les intégrations ont été conçues pour la V2 à partir de ces sources; aucun composant de la V1 n'a été repris.

| Sujet | Procédure retenue | Documentation officielle |
| --- | --- | --- |
| OAuth | POST form-urlencoded; token réutilisé et chiffré; expiration persistante; verrou et cooldown | [Authentication](https://open-api-docs.guesty.com/docs/authentication) |
| Réservations V3 | GET `/reservations-v3` avec `reservationIds`; réponse tableau; sélection exacte `_id`; segments `stay[]` | [Retrieve Reservations](https://open-api-docs.guesty.com/reference/reservationsopenapicontroller_getreservationsbyids) |
| IDs d'unités | Ne pas assimiler type, unité et listing; mapping explicite et résolution conservatrice | [Reservations V3 Booking Flow](https://open-api-docs.guesty.com/docs/reservations-v3-booking-flow) |
| Conversation | GET `/communication/conversations/{id}`; enveloppe `data`; metadata reservations/listing | [Get conversation](https://open-api-docs.guesty.com/reference/get_communication-conversations-conversationid) |
| Historique | GET posts; enveloppe `data.posts`, pagination `cursor.after`/`cursorAfter`, tri `createdAt` | [Get posts](https://open-api-docs.guesty.com/reference/get_communication-conversations-conversationid-posts) |
| Envoi | POST `send-message`, objet `module`, texte `body`; réponse `data._id` et conversation vérifiées | [Send message](https://open-api-docs.guesty.com/reference/post_communication-conversations-conversationid-send-message) |
| Webhooks | `reservation.messageReceived`, relecture des objets; inquiries parfois sans ID conversation | [Communication webhooks](https://open-api-docs.guesty.com/docs/webhooks-messages) |
| Signature | Vérification Svix sur bytes bruts et headers, déduplication persistante | [Subscribe to webhooks](https://open-api-docs.guesty.com/docs/webhooks) |
| Listing | GET listing par ID; import limité aux champs documentés d'identité | [Get listing](https://open-api-docs.guesty.com/reference/propertieslistingget) |
| Catalogue | Pagination `skip`/`limit`; guide et référence diffèrent sur l'enveloppe, deux formes explicites acceptées | [List listings](https://open-api-docs.guesty.com/reference/propertieslistinglist) |
| Calendrier | Listing exact, startDate/endDate, includeAllotment; allotment prioritaire, nuits hors checkout | [Calendar single listing](https://open-api-docs.guesty.com/reference/get_availability-pricing-api-calendar-listings-id) |
| Blocs et enveloppe | `data.days`, restrictions CTA/CTD et minimum de nuits | [Calendar block types](https://open-api-docs.guesty.com/docs/calendar-block-types) |
| OpenAI | Responses `parse`, schémas Pydantic stricts, refus/absence de sortie traités, `store=false` | [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) |
| Render | Blueprint, variables secrètes et migrations preDeploy | [Blueprint reference](https://render.com/docs/blueprint-spec) |

## Points volontairement conservateurs

La référence V3 représente parfois des IDs comme objets sans décrire leur sérialisation, tandis que ses exemples utilisent des chaînes. Seules les chaînes documentées dans les exemples sont acceptées; aucun parcours récursif d'un objet pour chercher un ID.

Le vocabulaire unit/type varie entre certaines descriptions publiques. Le code n'utilise aucune équivalence implicite. Une conversation avec plusieurs séjours ou un type partagé ne fournit pas à elle seule l'identité physique certaine.

`fromThirdParty` peut être un email de plateforme ou d'expéditeur non identifié; il n'est pas traité automatiquement comme un voyageur. L'auteur et le contenu des messages sont également vérifiés contre les posts fraîchement récupérés. Si votre compte expose une autre sérialisation de provenance, il faudra ajouter un adaptateur documenté après inspection d'un exemple réel anonymisé.

`isAutomatic` n'est pas garanti dans le schéma public des posts. Une classification manager est prévue pour les exemples humains; l'absence du champ n'est jamais interprétée comme une preuve d'origine humaine.

Une liste complète et certaine de jours est requise pour confirmer une disponibilité. Les structures inattendues donnent `unknown`, jamais « disponible ». Une réservation déjà confirmée n'est pas revalidée sur ses propres dates.

Aucune création de réservation, modification financière ni action sur un logement Guesty n'est implémentée. Les mises à jour manager portent sur la base de configuration locale.

Contrats vérifiés en lecture sur le compte connecté : conversations sans projection (enveloppe data.conversations), posts avec sentBy=guest/host, réservations V3 via reservationIds[]=ID. La variante reservationIds=ID est refusée HTTP 400 par ce compte. Des sources from/sentBy contradictoires sont classées inconnues et ne déclenchent pas de proposition. Les relations d’unité sont confirmées depuis les champs V3 et la métadonnée du listing canonique, jamais par égalité supposée.

Inventaire automatique : [List listings](https://open-api-docs.guesty.com/reference/propertieslistinglist) ; l’absence des filtres active/pmsActive/listed inclut les différents états. Pagination limit=100/skip, tri _id vérifié sur le compte. Les champs Wi-Fi, manuel, stationnement et collecte sont documentés dans [Property Location Details](https://open-api-docs.guesty.com/docs/listing-location). Les équipements absents ne sont pas déduits d’une omission ; amenitiesNotIncluded est utilisé seulement lorsqu’explicitement présent dans la réponse. Une extraction de texte conserve une citation exacte du document source.
