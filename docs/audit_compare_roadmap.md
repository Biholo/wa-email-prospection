# Roadmap - Audit & Comparaison Concurrentielle

## Features validées à implémenter

### 1. Budget marketing estimé
Basé sur les outils détectés dans `techStack`.

| Niveau | Signaux |
|--------|---------|
| Faible | Rien ou juste GA |
| Modéré | GTM + GA + 1 pixel |
| Élevé | Google Ads + LinkedIn + Meta + CRM |
| Très élevé | Ads + AB Testing + CRM avancé (Salesforce, Marketo) |

Champs : `marketingBudget.level`, `marketingBudget.score` (/10), `marketingBudget.signals[]`

---

### 2. Opportunités inversées
Checks où **tous les sites** échouent = avantage compétitif pour le premier.

Champs : `opportunities[].label`, `.pointsGainable`, `.failingCount`, `.message`

---

### 3. Profil acquisition
Canaux détectés depuis `techStack` :
- SEO (sitemap + schema)
- Paid (Google Ads, LinkedIn Ads, Meta Pixel, Bing Ads)
- Social (Facebook, TikTok Pixel)
- Email (Brevo, Mailchimp, Klaviyo)
- Retargeting (Criteo, Outbrain, pixels)
- CRO (AB Tasty, Optimizely, VWO)

Champs : `acquisitionProfile.channels[]`, `.channelCount`, `.level`

---

### 4. Propositions de valeur comparées
Extraire H1 + meta description de chaque site.

Champs : `messaging.h1`, `messaging.metaDescription`, `messaging.lang`

---

### 5. Fraicheur du site
- `dateModified` dans JSON-LD
- Copyright year dans footer (regex)
- Présence d'un blog (liens /blog, /actualites, /news)
- Nombre d'articles récents détectés

Champs : `freshness.lastModified`, `.copyrightYear`, `.hasBlog`, `.recentArticleCount`

---

### 6. Internationalisation
- Hreflang tags présents
- Outils i18n détectés (Weglot, Crowdin)
- Langues ciblées

Champs : `internationalisation.hreflang[]`, `.i18nTools[]`, `.isMultilingual`

---

## Intelligence compétitive globale (calcul auto)

A ajouter dans la réponse `/api/audit/compare` :

```json
"competitiveIntel": {
  "clientRank": 2,
  "avgCompetitorScore": 67,
  "scoreGap": -25,
  "strongestCompetitor": "brevo.com",
  "weakestCompetitor": "concurrent2.fr",
  "clientAdvantages": ["check_id_1", "check_id_2"],
  "criticalGaps": ["cookie_banner", "aggregate_rating", "cta_above_fold"]
}
```

---

## Intégration LLM - Ou ca apporte le plus de valeur

### Niveau 1 - Narratif de synthese (ROI immédiat)
Prendre le JSON compare complet et générer en 3-5 paragraphes :
- Situation actuelle vs concurrents
- Points forts du client
- Priorités d'action

Prompt type : "Tu es un consultant SEO. Voici l'analyse comparative [JSON]. Rédige une synthèse professionnelle en français pour le dirigeant."

### Niveau 2 - Email commercial personnalisé (ROI fort)
Après un compare, générer un email de prospection :
- "J'ai analysé [client.fr] vs [concurrent.fr]"
- "Votre concurrent investi X, vous avez Y en retard"
- CTA : "Je vous propose un audit complet"

C'est la killer feature : 30min de travail manuel → 3 secondes.

### Niveau 3 - Plan d'action priorisé avec contexte metier
Basé sur gaps + secteur détecté (artisan, SaaS, e-commerce) :
- LLM réordonne les priorités selon ROI estimé pour CE secteur
- "Pour un artisan local, la priorité 1 est X pas Y"

### Ou NE PAS utiliser LLM
- Detection de techno (regex = deterministe, pas d'hallucinations)
- Scoring (rules-based = auditable)
- Fetching de données

---

## Structure JSON cible (compare complet)

Voir `docs/compare_json_structure.md`
