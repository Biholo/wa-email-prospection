# API Reference

Base URL: `https://api.develly.io`

Auth: Bearer token sur toutes les routes.
```
Authorization: Bearer <API_SECRET_KEY>
```

---

## POST /api/audit

Analyse complète d'un site.

**Body**
```json
{
  "url": "https://www.example.com",
  "max_pages": 1
}
```

| Champ | Type | Défaut | Description |
|-------|------|--------|-------------|
| `url` | string | requis | URL du site (doit commencer par `http://` ou `https://`) |
| `max_pages` | int | `1` | Nombre de pages à crawler (max 5) |

**Query params optionnels**

| Param | Description |
|-------|-------------|
| `utm_source` | Source UTM pour tracking |
| `utm_campaign` | Campagne UTM pour tracking |

**Exemple**
```bash
curl -X POST https://api.develly.io/api/audit \
  -H "Authorization: Bearer <API_SECRET_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.develly.io", "max_pages": 1}'
```

**Output**
```json
{
  "domain": "www.develly.io",
  "globalScore": 74,
  "grade": "GOOD",
  "cms": "WordPress",
  "techStack": { ... },
  "messaging": { ... },
  "freshness": { ... },
  "acquisitionProfile": { ... },
  "marketingBudget": { ... },
  "internationalisation": { ... },
  "socialPresence": { ... },
  "targetKeyword": "agence web",
  "screenshots": { ... },
  "blocks": {
    "performance": {
      "score": 18,
      "maxScore": 25,
      "checks": [
        {
          "id": "lcp",
          "label": "LCP < 2.5s",
          "status": "pass",
          "points": 5,
          "maxPoints": 5,
          "fix": ""
        }
      ]
    }
  },
  "topIssues": [
    {
      "id": "no_ssl",
      "label": "HTTPS manquant",
      "fix": "Installer un certificat SSL"
    }
  ]
}
```

**Grades** : `EXCELLENT` (85-100) · `GOOD` (65-84) · `FAIR` (40-64) · `CRITICAL` (0-39)

**Erreurs**

| Code | Raison |
|------|--------|
| `400` | URL invalide |
| `401` | Token invalide |
| `429` | Limite 5 audits/heure par IP |
| `504` | Timeout (> 60s) |

---

## POST /api/audit/compare

Compare un site client contre des concurrents.

**Body**
```json
{
  "urls": [
    "https://www.client.com",
    "https://www.concurrent1.com",
    "https://www.concurrent2.com"
  ]
}
```

| Champ | Type | Description |
|-------|------|-------------|
| `urls` | array | 2 à 5 URLs. La **première** est le client. |

**Exemple**
```bash
curl -X POST https://api.develly.io/api/audit/compare \
  -H "Authorization: Bearer <API_SECRET_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://www.develly.io", "https://www.concurrent.fr"]}'
```

**Output**
```json
{
  "urls": ["https://www.develly.io", "https://www.concurrent.fr"],
  "results": [
    {
      "url": "https://www.develly.io",
      "domain": "www.develly.io",
      "isClient": true,
      "globalScore": 74,
      "grade": "GOOD",
      "blocks": { ... }
    },
    {
      "url": "https://www.concurrent.fr",
      "domain": "www.concurrent.fr",
      "isClient": false,
      "globalScore": 61,
      "grade": "FAIR",
      "blocks": { ... }
    }
  ],
  "gaps": [
    {
      "block": "seo",
      "checkId": "meta_description",
      "label": "Meta description présente",
      "clientStatus": "fail",
      "pointsMissed": 5,
      "competitors": ["www.concurrent.fr"],
      "fix": "Ajouter une meta description unique par page"
    }
  ],
  "opportunities": [
    {
      "block": "performance",
      "checkId": "webp_images",
      "label": "Images en WebP",
      "pointsGainable": 4,
      "failingCount": 2,
      "message": "Aucun concurrent ne l'a - premier arrivé, premier servi"
    }
  ],
  "competitiveIntel": {
    "clientRank": 1,
    "totalSites": 2,
    "avgCompetitorScore": 61,
    "scoreGap": 13,
    "strongestCompetitor": "www.concurrent.fr",
    "weakestCompetitor": "www.concurrent.fr",
    "clientAdvantages": [ ... ],
    "criticalGaps": ["meta_description", "lcp", "ssl"]
  },
  "matrix": {
    "meta_description": {
      "www.develly.io": "fail",
      "www.concurrent.fr": "pass"
    }
  }
}
```

**Erreurs**

| Code | Raison |
|------|--------|
| `400` | Moins de 2 URLs ou plus de 5 |
| `401` | Token invalide |
| `429` | Limite 5 audits/heure par IP |
| `504` | Timeout (> 90s) |

---

## PATCH /api/audit/{audit_id}/email

Capture l'email d'un prospect après audit et déclenche l'envoi du rapport.

**Body**
```json
{
  "email": "contact@example.com"
}
```

**Exemple**
```bash
curl -X PATCH https://api.develly.io/api/audit/abc123/email \
  -H "Authorization: Bearer <API_SECRET_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"email": "contact@example.com"}'
```

**Output**
```json
{
  "ok": true,
  "audit_id": "abc123",
  "email": "contact@example.com"
}
```
