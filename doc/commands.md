# Pipeline Commands

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill environment variables
cp .env.example .env

# Start the server (cron-based)
python main.py
```

---

## Manual Pipeline Runs

All manual runs go through `cli.py`. Always run from the project root.

### Run both pipelines

```bash
python cli.py develly
```

### Dry run (no writes to Brevo)

```bash
python cli.py develly --dry-run
```

### Email pipeline only

```bash
python cli.py develly --only email
```

### WhatsApp pipeline only

```bash
python cli.py develly --only whatsapp
```

---

## Pipeline Logic

### Email pipeline (`scheduler/pipeline_email.py`)

**Source:** Brevo list `#20` (ATTENTE ENVOI EMAIL)  
**Target:** Brevo list `#21` (AUTOMATISATION EMAIL)

1. Fetch contacts from list `#20` filtered by `PROPRIETAIRE`
2. For each contact:
   - Run PageSpeed on their website URL
   - Detect angle — `INVISIBILITÉ` is excluded (email requires a website)
   - If no angle → remove from list `#20`, log SKIPPÉ, continue
   - Fetch 2 best competitors from Supabase (same city + niche)
   - Generate 3 emails in one OpenAI call (j0, j3, j7) using templates:
     - `templates/{config}/{angle}_email_j0.yaml`
     - `templates/{config}/{angle}_email_j3.yaml`
     - `templates/{config}/{angle}_email_j7.yaml`
   - Update contact in Brevo:
     - `ANGLE`, `CANAL_PRINCIPAL=EMAIL`
     - `EMAIL_1_OBJET` / `EMAIL_1_CORPS`
     - `EMAIL_2_OBJET` / `EMAIL_2_CORPS`
     - `EMAIL_3_OBJET` / `EMAIL_3_CORPS`
     - `TOTAL_MESSAGE_ENVOYE=1`
     - `DATE_DERNIERE_ENVOI`, `PROCHAINE_RELANCE` (today + 3 days)
   - Move contact: `#20` → `#21`

**Angles available for email:** `RÉPUTATION`, `TECHNIQUE`, `ESTHÉTISME`  
**Config keys:** `workflow.email.max_per_day`, `rules.send_delay`

---

### WhatsApp pipeline (`scheduler/pipeline_whatsapp.py`)

**Source:** Brevo list `#22` (WORKFLOW WHATSAPP)

The pipeline runs in two phases within the same daily quota.

#### Phase 1 — Follow-up (relances)

Contacts where `TOTAL_MESSAGE_ENVOYE >= 1` and `PROCHAINE_RELANCE <= today`.  
These already have `WA_2` stored from their first contact.

1. Read pre-stored `WA_2` from Brevo attributes
2. Send via Maytapi
3. Update contact:
   - `TOTAL_MESSAGE_ENVOYE + 1`
   - `DATE_DERNIERE_ENVOI`, `PROCHAINE_RELANCE=null`

No OpenAI call. No angle detection.

#### Phase 2 — New contacts (nouveaux)

Contacts where `TOTAL_MESSAGE_ENVOYE = 0` and `PROCHAINE_RELANCE` is null.  
Runs only if quota not yet reached after Phase 1.

1. `check_whatsapp(SMS)` — skip if no WhatsApp, set `CANAL_PRINCIPAL=SMS`
2. Run PageSpeed on their website URL
3. Detect angle (all angles allowed including `INVISIBILITÉ`)
4. If no angle → log SKIPPÉ, continue
5. Fetch 2 best competitors from Supabase
6. Generate `WA_1` + `WA_2` in one OpenAI call using templates:
   - `templates/{config}/{angle}_wa_j0.yaml`
   - `templates/{config}/{angle}_wa_j3.yaml`
7. Send `WA_1` via Maytapi
8. Update contact in Brevo:
   - `ANGLE`, `CANAL_PRINCIPAL=WHATSAPP`
   - `WA_1`, `WA_2` (WA_2 stored for Phase 1 next run)
   - `TOTAL_MESSAGE_ENVOYE=1`
   - `DATE_DERNIERE_ENVOI`, `PROCHAINE_RELANCE` (today + 3 days)

**Config keys:** `workflow.whatsapp.max_per_day`, `rules.send_delay`

---

## Angles

| Angle | Condition | Email | WhatsApp |
|-------|-----------|-------|----------|
| `INVISIBILITÉ` | No website URL | ✗ excluded | ✓ |
| `RÉPUTATION` | `AVERAGE_RATE < rules.thresholds.bad_rating` | ✓ | ✓ |
| `TECHNIQUE` | PageSpeed score `< rules.thresholds.bad_pagespeed` | ✓ | ✓ |
| `ESTHÉTISME` | Site creation year `< rules.thresholds.old_site` | ✓ | ✓ |

Priority order is set in `config/{name}.yaml` under `rules.angle_priority`.

---

## Config file (`config/develly.yaml`)

Key sections:

```yaml
company:
  owner:    "Develly"    # filters contacts by PROPRIETAIRE Brevo field
  sender:   "..."        # injected into email templates as {expediteur}
  calendly: "..."        # injected into templates as {calendly}

workflow:
  email:
    source_list_id: 20
    target_list_id: 21
    max_per_day:    200
  whatsapp:
    source_list_id: 22
    max_per_day:    100

rules:
  send_delay: 30          # seconds between sends
  angle_priority: [...]   # detection order
  thresholds:
    bad_rating:    3.5
    bad_pagespeed: 50
    old_site:      2020
```

---

## Brevo Lists

| ID | Name | Role |
|----|------|------|
| 20 | ATTENTE ENVOI EMAIL | Email pipeline source |
| 21 | AUTOMATISATION EMAIL | Email pipeline target |
| 22 | WORKFLOW WHATSAPP | WhatsApp pipeline source |
| 23 | SORTI INTÉRESSÉ | Manual |
| 24 | SORTI NON INTÉRESSÉ | Manual |
| 25 | À APPELER | Manual / cold fallback |
