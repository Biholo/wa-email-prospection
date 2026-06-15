# Brainstorm - Endpoints Audit

---

## SCOPE - Features validées (à implémenter)

| Feature | /api/audit (solo) | /api/audit/compare | Notes |
|---------|:-----------------:|:------------------:|-------|
| **Security headers block** | ✅ nouveau bloc | ✅ side-by-side | HSTS, CSP, X-Frame-Options, X-Content-Type |
| **Open Graph / Twitter Card** | ✅ checks SEO | ✅ comparaison | og:image, og:title, twitter:card |
| **Font name extraction** | ✅ dans techStack | ✅ side-by-side | Regex sur URL Google Fonts family= |
| **Word count + FAQ schema** | ✅ contentMetrics | ✅ comparaison contenu | FAQPage JSON-LD + count mots body |
| **Social presence (toutes plateformes + URL)** | ✅ champ dédié | ✅ qui est présent où | LinkedIn, Facebook, Instagram, TikTok, YouTube, X/Twitter |
| **Matrice concurrentielle** | - | ✅ uniquement compare | check x site → tableau |
| **Keyword targeting extraction** | ✅ dans messaging | ✅ comparaison ciblage | Extrait depuis H1 + title |
| **Screenshot Playwright** | ✅ above-the-fold | ✅ side-by-side visuel | PNG sauvegardé dans /output |

---

### Détail - Social presence

Détecter dans le HTML (footer, header, liens) toutes les présences social media **avec l'URL du compte** :

```json
"socialPresence": {
  "linkedin":   "https://linkedin.com/company/develly",
  "facebook":   "https://facebook.com/develly",
  "instagram":  "https://instagram.com/develly",
  "tiktok":     null,
  "youtube":    "https://youtube.com/@develly",
  "twitter":    null,
  "pinterest":  null,
  "score": 3,
  "total": 7
}
```

Pattern de détection : chercher tous les `<a href>` contenant `linkedin.com/company/`, `facebook.com/`, `instagram.com/`, `tiktok.com/@`, `youtube.com/@` ou `/channel/`, `twitter.com/` ou `x.com/`, `pinterest.com/`.

---

### Détail - Screenshot Playwright

- Solo : capture above-the-fold desktop (1280x800) + mobile (390x844)
- Compare : capture above-the-fold de chaque site, sauvegarde côte à côte
- Fichier : `output/screenshot_{domain}_{date}.png`
- Champ JSON : `"screenshots": {"desktop": "output/...", "mobile": "output/..."}`
- Dans le CLI compare : affiche les chemins, pas d'affichage inline

---

### Détail - Keyword targeting

Extraire le mot-clé cible principal de chaque site :

```python
# Priorité : title > H1 > meta description
# Enlever le nom de marque si détecté, garder le reste
# Ex: "Agence Web Lyon | Develly" → "Agence Web Lyon"
```

```json
"targetKeyword": "agence web Lyon"
```

---

## 1. POST /api/audit - Analyse solo

### Ce qui est déjà en place
- Performance : PSI mobile/desktop, LCP, CLS, INP
- SEO technique : title, meta desc, H1, sitemap, robots, HTTPS, redirect 301, canonical, schema, lang, alt images, broken links
- Légal/RGPD : cookie banner, mentions légales, politique confidentialité, case consentement
- Conversion : CTA, formulaire, téléphone, preuve sociale, carte, chat
- Mobile : viewport, score mobile, taille texte, ressources bloquantes
- GEO/IA : crawlers IA, llms.txt, org schema, sameAs, speakable, BreadcrumbList, AggregateRating
- Détection : CMS, tech stack (15 catégories), messaging, freshness, acquisition profile, budget marketing, internationalisation
- Multi-page nav audit

---

### Axes d'amélioration

#### A. Checks SEO manquants

| Check | Valeur | Difficulté |
|-------|--------|-----------|
| Open Graph complet (og:image, og:title, og:description) | Partage social = trafic | Facile |
| Twitter Card (twitter:card, twitter:image) | Partage X/LinkedIn | Facile |
| Favicon présent | Signal de confiance, onglet navigateur | Facile |
| Contenu textuel (word count) | < 300 mots = page thin content | Facile |
| Ratio texte/HTML | Trop de code vs contenu = mauvais signal | Facile |
| H1 contient le mot-clé cible (vs title) | Cohérence sémantique | Moyen |
| Links internes (densité, pas juste broken) | Maillage interne = distribution PageRank | Moyen |
| Structured data completeness | Schema présent mais champs vides/manquants | Moyen |
| AMP ou Core Web Vitals champ (CrUX) | Lab vs real user data = différence énorme | Difficile |

#### B. Checks Sécurité (headers HTTP)

Détectables depuis les headers de réponse - zéro appel externe.

| Header | Ce que ça vérifie |
|--------|------------------|
| `Strict-Transport-Security` | HSTS actif |
| `X-Content-Type-Options` | nosniff = pas de MIME sniffing |
| `X-Frame-Options` | Clickjacking protection |
| `Content-Security-Policy` | XSS protection niveau avancé |
| `Referrer-Policy` | Contrôle des données de référent |

Score sécurité = nouveau bloc de 10-15 pts.

#### C. Checks Conversion manquants

| Check | Valeur |
|-------|--------|
| Exit intent / pop-up détecté | Signal de stratégie de capture |
| Vidéo intégrée (YouTube, Vimeo, `<video>`) | +80% temps sur page en moyenne |
| FAQ ou accordéon détecté | Signal de conversion + rich snippet |
| Prix ou tarifs affichés | Conversion directe pour e-commerce/SaaS |
| Garantie / labels de confiance (texte) | "satisfait ou remboursé", certifications |
| Newsletter form distinct du form contact | Canal email = canal long terme |

#### D. Checks Technique avancés

| Check | Comment |
|-------|---------|
| Page 404 personnalisée | GET `/page-inexistante-xyz123` - si 200 = soft 404 |
| Redirect chain | Suivre les redirects, détecter A→B→C (2+ sauts) |
| SSL expiry | Via `ssl.get_server_certificate()` - expire dans < 30j = warning |
| Poids de la page | PSI `total-byte-weight` audit |
| Scripts tiers (count) | PSI `third-party-summary` - chaque script tiers = risque |
| Lazy loading images | `loading="lazy"` sur les images hors fold |
| Preload fonts | `<link rel="preload" as="font">` |

#### E. Checks GEO/IA manquants

| Check | Valeur |
|-------|--------|
| Mentions dans des annuaires (Trustpilot, Pages Jaunes) | Détectable via sameAs + liens sortants |
| `about` page présente | Les IA cherchent une page À Propos |
| FAQ schema (FAQPage) | Apparaît dans les AI Overviews Google |
| `contactPoint` dans Organization | Les IA peuvent référencer un numéro/email |
| Lien GBP dans sameAs | Signal local fort |
| Présence réseaux sociaux | LinkedIn, Facebook détectés dans les liens |

#### F. Extraction de polices (Google Fonts)

Depuis l'URL `fonts.googleapis.com/css?family=Inter:400,700&display=swap` :
```python
re.findall(r'family=([^&:]+)', url)  # → ["Inter"]
```
Ajouter dans techStack "Polices" : les noms réels, pas juste "Google Fonts".

---

### JSON output - champs à ajouter

```json
{
  "executionTimeMs": 4230,
  "schemaVersion": "2.0",
  "security": {
    "hsts": true,
    "xFrameOptions": "SAMEORIGIN",
    "csp": false,
    "xContentType": true,
    "score": 3
  },
  "contentMetrics": {
    "wordCount": 412,
    "textHtmlRatio": 0.18,
    "hasVideo": false,
    "hasFaq": true
  },
  "socialPresence": {
    "linkedin": "https://linkedin.com/company/...",
    "facebook": null,
    "instagram": null
  },
  "techStack": {
    "Polices": ["Inter", "Montserrat"]  // noms extraits, pas juste "Google Fonts"
  }
}
```

---

## 2. POST /api/audit/compare - Analyse concurrentielle

### Ce qui est déjà en place
- Audit complet pour chaque URL (parallèle)
- `results[]` : score global, blocs, techStack, messaging, freshness, acquisitionProfile, marketingBudget, internationalisation
- `gaps[]` : checks que le client rate et qu'au moins un concurrent réussit
- `opportunities[]` : checks que TOUS les sites ratent (first-mover advantage)
- `competitiveIntel{}` : rang, score gap vs moyenne, avantages client, gaps critiques

---

### Axes d'amélioration

#### A. Analyses comparatives manquantes

| Analyse | Description | Difficulté |
|---------|-------------|-----------|
| **Keyword targeting** | Mot-clé cible de chaque site basé sur H1+title+meta | Facile |
| **Schema richness score** | Nombre de types JSON-LD différents par site | Facile |
| **Social proof comparison** | Count d'avis, note moyenne détectée (schema AggregateRating) | Facile |
| **Content length comparison** | Word count side by side - proxy pour E-E-A-T | Facile |
| **Trust signals** | Certifications, labels, garanties détectés (texte + schema) | Moyen |
| **Funnel mapping** | Form fields count, CTA wording, prix affichés | Moyen |
| **Blog activity** | Présence blog + fréquence estimée (nb liens /blog/) | Moyen |
| **GBP linkage** | Qui a son GBP dans sameAs | Facile |

#### B. Score de positionnement (nouveau champ)

Pour chaque site, calculer automatiquement son positionnement perçu basé sur :
- H1 et meta description
- Mots-clés dans les CTA
- Prix affiché ou non
- Niveau de preuve sociale

```json
"positioning": {
  "type": "premium",       // "premium" | "prix" | "expertise" | "local" | "inconnu"
  "targetKeyword": "agence web Lyon",
  "ctaStyle": "urgency",   // "urgency" | "soft" | "demo" | "contact"
  "confidenceScore": 0.72
}
```

#### C. Matrice concurrentielle

Nouveau champ `matrix` dans la réponse compare - une ligne par check, une colonne par site.

```json
"matrix": {
  "cookie_banner":    {"client.fr": "pass", "concurrent.fr": "fail", "autre.fr": "pass"},
  "schema_present":   {"client.fr": "fail", "concurrent.fr": "pass", "autre.fr": "pass"},
  "cta_above_fold":   {"client.fr": "pass", "concurrent.fr": "pass", "autre.fr": "fail"}
}
```

Permet de générer un tableau HTML/PDF de comparaison propre.

#### D. Intelligence acquisition avancée

Comparer les canaux côte à côte + calculer l'écart d'investissement.

```json
"acquisitionComparison": {
  "channelCoverage": {
    "SEO":        {"clientFr": true, "concurrentFr": true, "autreFr": false},
    "Paid":       {"clientFr": false, "concurrentFr": true, "autreFr": true},
    "Email":      {"clientFr": false, "concurrentFr": true, "autreFr": false}
  },
  "budgetGap": {
    "clientScore": 2,
    "maxCompetitorScore": 8,
    "gap": -6,
    "interpretation": "Les concurrents investissent 4x plus en paid marketing"
  }
}
```

#### E. Analyse des angles morts

Aller plus loin que `opportunities[]` (first-mover) - identifier les patterns :

1. **Angle mort sectoriel** : checks que TOUS les acteurs du secteur ratent (industrie entière en retard)
2. **Faiblesse commune** : "3/4 des concurrents n'ont pas de FAQ schema"
3. **Segment non adressé** : personne n'a `internationalisation` → marché FR uniquement

#### F. Vitesse d'exécution

Problème actuel : 5 sites * ~18s = 90s de timeout max. Solutions :

| Option | Description | Effort |
|--------|-------------|--------|
| Job ID async | POST → retourne `{jobId}`, GET `/api/audit/compare/{jobId}` polling | Moyen |
| SSE streaming | Server-Sent Events, envoie chaque résultat dès qu'il arrive | Moyen |
| Browser pool | 1 Playwright browser partagé, context par thread | Difficile |
| Cache domaine | Si le domaine a été audité dans les 4h, retourner le cache | Facile |

---

### LLM - Ou brancher (résumé)

| Point | Description | Valeur |
|-------|-------------|--------|
| **Narratif compare** | JSON compare → paragraphes en FR pour le dirigeant | Très haute |
| **Email commercial** | JSON gaps → email prospection personnalisé "J'ai analysé votre site vs X" | Très haute |
| **Plan d'action priorisé** | gaps + secteur détecté → priorités adaptées au contexte métier | Haute |
| **Interpretation budget** | "Votre concurrent utilise AB Tasty + HubSpot = stratégie CRO + nurturing email forte" | Moyenne |
| **Positioning summary** | H1 + meta de chaque site → 1 phrase de positionnement par acteur | Moyenne |
| **Détection** | Jamais - regex = déterministe, pas d'hallucinations | Zero |

---

## Priorités d'implémentation

### Rapide (< 2h chacun)
1. Security headers block (nouveau bloc, valeur perçue forte)
2. Open Graph / Twitter Card checks
3. Font name extraction depuis Google Fonts URL
4. Word count + FAQ schema checks
5. Social presence (liens LinkedIn/Facebook détectés)

### Moyen terme (demi-journée chacun)
1. Matrice concurrentielle dans compare
2. Cache domaine 4h (dict en mémoire ou fichier JSON)
3. Keyword targeting extraction
4. Acquisition comparison avancée
5. Schéma richness score

### Long terme (feature complète)
1. Job ID async + polling pour compare
2. LLM narratif + email commercial
3. PDF export du compare
4. CrUX field data (Google CrUX API - gratuit)
5. Screenshot Playwright pour comparison visuelle
