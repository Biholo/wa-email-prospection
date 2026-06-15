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
# Les deux pipelines (email + whatsapp) — comportement identique au cron
python cli.py develly

# Email uniquement
python cli.py develly --only email

# WhatsApp uniquement
python cli.py develly --only whatsapp

# Pipeline apporteur uniquement (Expert-comptable → liste #30)
python cli.py develly --only apporteur
```

### Mode dry run

Génère les angles, les messages GPT et les logs SQLite, **sans envoyer de message et sans modifier Brevo**.
Les logs apparaissent avec `status = DRY_RUN` et le message généré dans la colonne `erreur_detail`.

```bash
# Dry run complet (email + whatsapp)
python cli.py develly --dry-run

# Dry run email uniquement
python cli.py develly --only email --dry-run

# Dry run WhatsApp uniquement
python cli.py develly --only whatsapp --dry-run

# Dry run apporteur
python cli.py develly --only apporteur --dry-run
```

> En dry run, les contacts restent en `STATUS=NOUVEAU` dans Brevo et ne sont pas déplacés entre les listes.

### Mode test WhatsApp — envoi réel sur un numéro cible

Envoie `WA_1` à un numéro de test **réel**, mais force dry-run pour tout le reste (aucune modification Brevo/Supabase).

```bash
# Numéro format français (06/07)
python cli.py develly --test-phone 0641552699

# Numéro format international
python cli.py develly --test-phone 33641552699
```

- Force automatiquement `--only whatsapp` et `--dry-run`
- Envoie un seul message (premier contact éligible) puis s'arrête
- Utile pour valider les messages GPT générés sans toucher aux vraies données

### Mode test liste 28

Lit les contacts depuis **liste 28** (liste de test Brevo) au lieu de la source réelle, mais exécute les actions réelles (move to list, update contact, envoi WA).

```bash
python cli.py develly --test-mode
```

- Force automatiquement `--only whatsapp`
- Peuple liste 28 manuellement dans Brevo avec des contacts de test

### Consulter les résultats d'un dry run

```bash
# Via l'API (si le serveur tourne)
curl "http://localhost:8000/logs?status=DRY_RUN&limit=20"

# Directement en SQLite
sqlite3 data/develly.db "SELECT contact_email, angle, canal, erreur_detail FROM logs WHERE status='DRY_RUN' ORDER BY created_at DESC LIMIT 20;"
```

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

## Moteur d'audit de site web

Deux routes distinctes pour l'analyse de site. Les deux sauvegardent automatiquement un JSON dans `/output/` et des screenshots dans `/output/screenshots/`.

---

### `POST /api/audit` - Analyse solo

Analyse complète d'un site en 7 blocs + intelligence signals.

```bash
curl -X POST http://localhost:8000/api/audit \
  -H "Content-Type: application/json" \
  -d '{"url": "https://monsite.fr", "max_pages": 3}'
```

| Champ | Type | Defaut | Description |
|-------|------|--------|-------------|
| `url` | string | requis | URL a analyser (avec https://) |
| `max_pages` | int | 1 | Pages nav a auditer en plus (max 5 ou AUDIT_MAX_PAGES) |

**Reponse :**

```json
{
  "url": "https://monsite.fr",
  "domain": "monsite.fr",
  "analyzedAt": "2026-06-15T10:00:00Z",
  "globalScore": 67,
  "grade": "FAIR",
  "summary": "Ton site a des bases correctes mais...",
  "cms": "Next.js",
  "targetKeyword": "Agence web Lyon",
  "techStack": {
    "Framework JS": ["Next.js"],
    "Analytics": ["Google Analytics 4"],
    "Polices": ["Inter", "Montserrat"],
    "Serveur": ["Vercel"]
  },
  "messaging": {
    "h1": "Titre principal de la page",
    "metaDescription": "Description meta...",
    "lang": "fr"
  },
  "freshness": {
    "lastModified": "2025-11-01",
    "copyrightYear": 2025,
    "hasBlog": true,
    "recentArticleCount": 4
  },
  "acquisitionProfile": {
    "channels": ["SEO", "Paid", "Email"],
    "channelCount": 3,
    "level": "multi-canal"
  },
  "marketingBudget": {
    "level": "modere",
    "score": 5,
    "signals": ["Google Ads", "HubSpot"]
  },
  "internationalisation": {
    "hreflang": ["fr", "en"],
    "i18nTools": [],
    "isMultilingual": true
  },
  "socialPresence": {
    "linkedin": "https://linkedin.com/company/monsite",
    "facebook": null,
    "instagram": "https://instagram.com/monsite",
    "tiktok": null,
    "youtube": null,
    "twitter": null,
    "pinterest": null,
    "score": 2,
    "total": 7
  },
  "screenshots": {
    "desktop": "output/screenshots/desktop_monsite_fr_20260615_100000.png",
    "mobile": "output/screenshots/mobile_monsite_fr_20260615_100000.png"
  },
  "blocks": {
    "performance": {"score": 18, "maxScore": 25, "checks": [...]},
    "seo":         {"score": 20, "maxScore": 25, "checks": [...]},
    "legal":       {"score": 15, "maxScore": 20, "checks": [...]},
    "conversion":  {"score": 10, "maxScore": 20, "checks": [...]},
    "mobile":      {"score": 7,  "maxScore": 10, "checks": [...]},
    "geo":         {"score": 0,  "maxScore": 0,  "geoScore": 45, "checks": [...]},
    "security":    {"score": 0,  "maxScore": 0,  "securityScore": 60, "checks": [...]}
  },
  "topIssues": [...],
  "pages": [...]
}
```

**Blocs de scoring :**

| Bloc | Max | Ce qu'il mesure |
|------|-----|-----------------|
| performance | 25 | PSI mobile/desktop, LCP, CLS, INP |
| seo | 25 | title, meta, H1, sitemap, robots, canonical, schema, OG image, Twitter Card |
| legal | 20 | cookie banner, mentions legales, politique confidentialite, consentement |
| conversion | 20 | CTA, formulaire, telephone, preuve sociale |
| mobile | 10 | viewport, score mobile PSI, taille texte |
| geo | bonus /100 | crawlers IA, llms.txt, JSON-LD richesse, sameAs, FAQPage |
| security | bonus /100 | HSTS, CSP, X-Frame-Options, X-Content-Type-Options |

`globalScore = performance + seo + legal + conversion + mobile` (max 100)

Les blocs `geo` et `security` sont informatifs (hors globalScore).

**CLI :**

```bash
# Audit simple
python cli_audit.py audit https://monsite.fr

# Audit multi-page (analyse 3 pages de la nav)
python cli_audit.py audit https://monsite.fr --pages 3

# Detail complet de tous les checks
python cli_audit.py audit https://monsite.fr --detail

# Uniquement les fails et warnings
python cli_audit.py audit https://monsite.fr --issues

# JSON brut
python cli_audit.py audit https://monsite.fr --raw
```

---

### `POST /api/audit/compare` - Analyse concurrentielle

Compare 2 a 5 sites en parallele. Le premier URL est le client, les suivants sont les concurrents.

```bash
curl -X POST http://localhost:8000/api/audit/compare \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://client.fr", "https://concurrent1.fr", "https://concurrent2.fr"]}'
```

| Champ | Type | Description |
|-------|------|-------------|
| `urls` | list[string] | 2 a 5 URLs - la 1ere est le client |

**Reponse :**

```json
{
  "urls": ["https://client.fr", "https://concurrent1.fr"],
  "results": [
    {
      "url": "https://client.fr",
      "domain": "client.fr",
      "isClient": true,
      "globalScore": 42,
      "grade": "FAIR",
      "targetKeyword": "Plombier Paris",
      "techStack": {...},
      "messaging": {...},
      "freshness": {...},
      "acquisitionProfile": {...},
      "marketingBudget": {...},
      "socialPresence": {...},
      "screenshots": {"desktop": "output/screenshots/...", "mobile": "..."},
      "blocks": {...},
      "topIssues": [...]
    }
  ],
  "gaps": [
    {
      "block": "legal",
      "checkId": "cookie_banner",
      "label": "Bandeau cookies / consentement RGPD",
      "clientStatus": "fail",
      "pointsMissed": 6,
      "competitors": ["concurrent1.fr"],
      "fix": "Installer une solution de consentement (Axeptio, Tarteaucitron...)."
    }
  ],
  "opportunities": [
    {
      "block": "geo",
      "checkId": "faq_schema",
      "label": "Schema FAQPage",
      "pointsGainable": 6,
      "failingCount": 2,
      "message": "Aucun concurrent ne l'a - premier arrive, premier servi"
    }
  ],
  "competitiveIntel": {
    "clientRank": 2,
    "totalSites": 2,
    "avgCompetitorScore": 71,
    "scoreGap": -29,
    "strongestCompetitor": "concurrent1.fr",
    "weakestCompetitor": "concurrent1.fr",
    "clientAdvantages": [...],
    "criticalGaps": ["cookie_banner", "cta_above_fold", "schema_present"]
  },
  "matrix": {
    "cookie_banner":  {"client.fr": "fail", "concurrent1.fr": "pass"},
    "cta_above_fold": {"client.fr": "pass", "concurrent1.fr": "pass"},
    "schema_present": {"client.fr": "fail", "concurrent1.fr": "pass"}
  }
}
```

**Champs cles :**

| Champ | Description |
|-------|-------------|
| `gaps[]` | Checks que le client rate et qu'au moins un concurrent reussit, tries par points perdus |
| `opportunities[]` | Checks que TOUS les sites ratent = first-mover advantage |
| `competitiveIntel.scoreGap` | Score client moins moyenne concurrents (negatif = en retard) |
| `competitiveIntel.clientAdvantages` | Checks que le client reussit et que les concurrents ratent |
| `matrix` | Tableau croise {checkId: {domain: status}} - vue complete de qui a quoi |

**CLI :**

```bash
# Comparaison (1er = client)
python cli_audit.py compare https://client.fr https://concurrent.fr

# Avec plusieurs concurrents
python cli_audit.py compare https://client.fr https://conc1.fr https://conc2.fr https://conc3.fr

# JSON brut
python cli_audit.py compare https://client.fr https://concurrent.fr --raw
```

**Output CLI compare :**
1. SCORES - tableau des scores globaux par bloc
2. TECH STACK - side-by-side par categorie
3. PROPOSITION DE VALEUR - H1, canaux, budget, internationalisation
4. FRAICHEUR - copyright year, blog, date modification
5. AVANTAGES CONCURRENTS (gaps) - checks rates avec points manques
6. PLAN D'ACTION - top 5 fixes priorises
7. OPPORTUNITES FIRST-MOVER - checks que personne n'a encore
8. SYNTHESE CONCURRENTIELLE - rang, ecart de score, avantages client
9. MOT-CLE CIBLE - keyword cible par site
10. PRESENCE SOCIALE - qui est sur quelles plateformes
11. SECURITE HEADERS - score /100 par site
12. SCREENSHOTS - chemins vers les fichiers PNG

---

### Capture email post-audit

Apres un audit solo, enregistre l'email du visiteur et envoie un email de rapport.

```bash
curl -X PATCH http://localhost:8000/api/audit/{audit_id}/email \
  -H "Content-Type: application/json" \
  -d '{"email": "contact@monsite.fr"}'
```

---

### Variables d'environnement audit

```env
PAGESPEED_API_KEY=AIza...       # Google PageSpeed API key (recommande)
AUDIT_MAX_PAGES=10              # Nombre max de pages nav a auditer
```

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