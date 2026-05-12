# Develly — Serveur de prospection B2B automatisée

Pipeline de cold outreach automatisé : détection d'angle → canal → concurrent → envoi (Brevo automation / WhatsApp / SMS).

---

## Stack

- **Python 3.11+** · FastAPI · Uvicorn
- **APScheduler** — cron d'exécution (pas de trigger API)
- **Brevo** — contacts, listes, automations email
- **Maytapi** — WhatsApp
- **Supabase** — données leads + concurrents par city\_id
- **OpenAI GPT-4o** — génération de messages WhatsApp / SMS
- **SQLite** — logs locaux

---

## Structure

```
develly-server/
├── main.py                  ← FastAPI app (15 lignes — init + scheduler)
├── requirements.txt
├── .env                     ← clés API (non commité)
│
├── api/                     ← routes HTTP (monitoring seulement)
│   ├── status.py            → GET /status
│   └── logs.py              → GET /logs
│
├── scheduler/
│   └── jobs.py              ← pipeline complet + cron APScheduler
│
├── core/
│   ├── db.py                ← SQLite helpers
│   └── state.py             ← état pipeline thread-safe
│
├── services/
│   ├── brevo_service.py     ← contacts, listes, push_to_automation
│   ├── supabase_service.py  ← leads + concurrents par city_id/niche
│   ├── maytapi_service.py   ← WhatsApp check + send
│   ├── sms_service.py       ← SMS (Twilio ou Brevo SMS)
│   ├── pagespeed_service.py ← score PageSpeed mobile
│   └── openai_service.py    ← génération messages GPT-4o
│
├── logic/
│   ├── angle_detector.py    ← INVISIBILITÉ / RÉPUTATION / TECHNIQUE / ESTHÉTISME
│   ├── canal_detector.py    ← EMAIL / WHATSAPP / SMS / MAUVAISE_LISTE
│   └── message_builder.py   ← assemble prompt + appelle OpenAI
│
├── config/
│   └── develly.yaml         ← toute la config métier + cron
│
├── templates/develly/       ← structure des messages par angle
├── prompts/develly/         ← prompts GPT par canal
├── data/                    ← develly.db (SQLite, gitignore)
└── docker/
    ├── Dockerfile
    └── docker-compose.yml
```

---

## Installation locale

### 1. Prérequis

- Python 3.11+
- Un virtualenv (recommandé)

```bash
python -m venv .venv
source .venv/bin/activate        # Mac/Linux
.venv\Scripts\activate           # Windows
```

### 2. Dépendances

```bash
pip install -r requirements.txt
```

### 3. Variables d'environnement

```bash
cp .env.example .env
```

Remplis le `.env` :

```env
# Brevo
BREVO_API_KEY=xkeysib-...

# Maytapi (WhatsApp)
MAYTAPI_PRODUCT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MAYTAPI_PHONE_ID=xxxxx
MAYTAPI_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# OpenAI
OPENAI_API_KEY=sk-...

# Google PageSpeed (optionnel — fonctionne sans pour les appels anonymes)
PAGESPEED_API_KEY=

# Supabase
SUPABASE_URL=https://xxxxxxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...

# SMS (twilio ou brevo)
SMS_PROVIDER=twilio
SMS_API_KEY=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SMS_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SMS_SENDER=+33600000000
```

### 4. Config métier

Ouvre `config/develly.yaml` et ajuste :

```yaml
scheduler:
  cron: "0 9 * * 1-5"   # heure de lancement — Lun-Ven 9h
  timezone: "Europe/Paris"
```

Pour chaque liste EMAIL, renseigne l'`automation_list_id` avec l'**ID de la liste Brevo** qui déclenche ton workflow d'automation :

```yaml
listes:
  - id: 16, nom: "serrurier", niche: "serrurier", ..., automation_list_id: 50
  #                                                                          ↑
  #                              remplace 50 par le vrai ID dans Brevo
```

> Le champ `niche` doit correspondre **exactement** à la valeur dans la colonne `niche` de ta table Supabase `leads`.

### 5. Lancer le serveur

```bash
uvicorn main:app --reload
```

Le serveur démarre, le cron s'enregistre, et le pipeline se lancera automatiquement à l'heure configurée.

---

## Lancer manuellement (CLI)

Le script `cli.py` permet de lancer les pipelines à la demande, sans attendre le cron.

### Commandes disponibles

```bash
# Les deux pipelines en parallèle (comportement identique au cron)
python cli.py develly

# Email uniquement
python cli.py develly --only email

# WhatsApp uniquement
python cli.py develly --only whatsapp
```

### Mode dry run

Génère les angles, les messages GPT et les logs SQLite, **sans envoyer de message et sans modifier Brevo**.
Les logs apparaissent avec `status = DRY_RUN` et le message généré dans la colonne `erreur_detail`.

```bash
# Dry run complet
python cli.py develly --dry-run

# Dry run email uniquement
python cli.py develly --only email --dry-run

# Dry run WhatsApp uniquement
python cli.py develly --only whatsapp --dry-run
```

### Consulter les résultats d'un dry run

```bash
# Via l'API (si le serveur tourne)
curl "http://localhost:8000/logs?status=DRY_RUN&limit=20"

# Directement en SQLite
sqlite3 data/develly.db "SELECT contact_email, angle, canal, erreur_detail FROM logs WHERE status='DRY_RUN' ORDER BY created_at DESC LIMIT 20;"
```

> En dry run, les contacts restent en `STATUS=NOUVEAU` dans Brevo et ne sont pas déplacés entre les listes. Tu peux relancer autant de fois que nécessaire pour affiner les prompts.

---

## Import Supabase → Brevo

Importe les leads Supabase dans les listes Brevo (20 = email, 22 = WhatsApp, 25 = call).

```bash
python scripts/import_supabase_brevo.py
```

Les résultats sont loggés dans `data/import.db` et `data/import.log`. Les leads déjà traités sont ignorés — relançable sans risque de doublons.

---

## Docker

```bash
# Depuis la racine du projet
cd docker
docker-compose up --build
```

Les dossiers `data/`, `config/` et `prompts/` sont montés en volume — pas besoin de rebuild pour modifier la config ou les prompts.

---

## API de monitoring

Le serveur expose uniquement des routes de **lecture**. Le pipeline se lance via le cron, pas via API.

### `GET /status`

État du pipeline en cours.

```json
{
  "running": true,
  "processed": 14,
  "errors": 1,
  "config": "develly",
  "liste_id": 16,
  "started_at": "2024-11-04T09:00:03.412"
}
```

### `GET /logs`

Derniers envois depuis SQLite.

| Paramètre | Type   | Défaut | Description                               |
|-----------|--------|--------|-------------------------------------------|
| `limit`   | int    | 50     | Nombre de lignes (max 500)                |
| `status`  | string | —      | `ENVOYÉ` · `SKIPPÉ` · `ERREUR` · `MAUVAISE_LISTE` |
| `config`  | string | —      | Filtre par config, ex: `develly`          |

```bash
# Tous les envois récents
curl http://localhost:8000/logs

# Uniquement les erreurs
curl "http://localhost:8000/logs?status=ERREUR&limit=20"
```

---

## Pipeline — ordre d'exécution

Pour chaque contact Brevo avec `STATUS = NOUVEAU` :

```
1. PageSpeed mobile → score /100 (None si pas de site)
2. AngleDetector → INVISIBILITÉ / RÉPUTATION / TECHNIQUE / ESTHÉTISME / None (skippé)
3. CanalDetector → EMAIL / WHATSAPP / SMS / None (→ liste 99 manuelle)
4. SupabaseService → top 2 concurrents par city_id + niche
5. Envoi :
     EMAIL    → update attributs Brevo + push_to_automation (retire de la liste froide,
                 ajoute dans la liste automation — Brevo envoie l'email lui-même)
     WHATSAPP → GPT-4o génère le message → Maytapi envoie
     SMS      → GPT-4o génère le message → Twilio/Brevo SMS envoie
6. Update contact Brevo (ANGLE, STATUS=CONTACTÉ, CANAL_PRINCIPAL, DATE_PREMIER_CONTACT...)
7. Log SQLite
8. sleep(delai_entre_envois)
```

---

## Ajouter une nouvelle entreprise

1. Créer `config/nouvelleentreprise.yaml` (copier `develly.yaml` et adapter)
2. Créer `templates/nouvelleentreprise/` avec les 4 YAML d'angle
3. Créer `prompts/nouvelleentreprise/` avec `system.txt`, `email.txt`, `whatsapp.txt`, `sms.txt`

Le scheduler détecte automatiquement tous les fichiers `config/*.yaml` au démarrage. **Zéro modification du code core.**

---

## Attributs Brevo

### Lus (fournis par le scraper)
`NOM` · `PRENOM` · `EMAIL` · `SMS` · `TEL` · `LANDLINE_NUMBER` · `WEBSITE_URL` · `AVERAGE_RATE` · `NUMBER_OF_RATE` · `COMPANY` · `CITY_ID` · `ADDRESS`

### Écrits par ce serveur
| Attribut | Valeurs |
|---|---|
| `ANGLE` | `INVISIBILITÉ` · `RÉPUTATION` · `TECHNIQUE` · `ESTHÉTISME` |
| `STATUS` | `NOUVEAU` · `CONTACTÉ` · `MAUVAISE_LISTE` |
| `CANAL_PRINCIPAL` | `EMAIL` · `WHATSAPP` · `SMS` |
| `WHATSAPP_CHECK` | `OUI` · `NON` |
| `PAGESPEED_SCORE` | entier |
| `DATE_PREMIER_CONTACT` | `YYYY-MM-DD` |
| `CONCURRENT_1_NOM` · `CONCURRENT_1_NOTE` · `CONCURRENT_1_NB_AVIS` | depuis Supabase |
| `CONCURRENT_2_NOM` · `CONCURRENT_2_NOTE` · `CONCURRENT_2_NB_AVIS` | depuis Supabase |
| `ECART_NOTE` · `ECART_AVIS` | écarts calculés |

Ces attributs sont utilisables comme variables dans les templates email de l'automation Brevo.
#   w a - e m a i l - p r o s p e c t i o n  
 