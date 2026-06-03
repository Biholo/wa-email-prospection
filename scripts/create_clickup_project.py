#!/usr/bin/env python3
"""Create a ClickUp list with all project tasks for a new client."""

import os
import sys
import time
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("CLICKUP_API_KEY")
FOLDER_ID = 901211388694
KILIAN_ID = 302468508
RAPHAEL_ID = 99799341
PHASE_FIELD_ID = "fde08aed-917f-4058-b6c5-485be8797014"

PHASE_IDS = {
    "Admin":               "1aef17ab-fe1a-4906-91ba-5ccbd2e2c21a",
    "Onboarding":          "95fcc4ef-b5bc-4c91-99d4-c4a0eff5953d",
    "Contenu & Stratégie": "1c3821b0-9443-4977-b03f-70b6d50c9bb0",
    "Design":              "b2d7f8d5-8dce-45c8-85ea-32b0109ef743",
    "Développement":       "4f3fc524-c550-4fc0-b092-2e337b6f0772",
    "QA / Recette":        "e43cfd4c-e188-47b1-9354-2d093f9fb5aa",
    "Go-live":             "5e01f40c-8fb2-45d4-a7bf-321a76bac198",
    "Livraison":           "11851e99-daa2-45b3-9933-5d0592115949",
    "Diffusion":           "ea451877-bd77-43b6-9960-7d1af3b0ac51",
}

TASKS = [
    # Phase 0 — Admin
    # Toutes les deadlines calculées depuis la date de signature (J+0)
    {"name": "Envoyer le contrat",                    "assignees": [KILIAN_ID],  "phase": "Admin", "priority": 1, "time_estimate": 900000,  "due_days": 0},
    {"name": "Confirmer la signature du contrat",     "assignees": [KILIAN_ID],  "phase": "Admin", "priority": 1, "time_estimate": 900000,  "due_days": 1},
    {"name": "Envoyer la facture d'acompte (50%)",    "assignees": [KILIAN_ID],  "phase": "Admin", "priority": 1, "time_estimate": 900000,  "due_days": 0},
    {"name": "Confirmer réception du paiement",       "assignees": [KILIAN_ID],  "phase": "Admin", "priority": 1, "time_estimate": 900000,  "due_days": 3},

    # Phase 1 — Onboarding
    {"name": "Envoyer le document de bienvenue",                                      "assignees": [KILIAN_ID], "phase": "Onboarding", "priority": 2, "time_estimate": 1800000, "due_days": 1},
    {"name": "Envoyer le lien formulaire onboarding",                                 "assignees": [KILIAN_ID], "phase": "Onboarding", "priority": 2, "time_estimate": 900000,  "due_days": 1},
    {"name": "Vérifier que la clause de pénalité de retard assets est bien comprise", "assignees": [KILIAN_ID], "phase": "Onboarding", "priority": 3, "time_estimate": 900000,  "due_days": 2},
    {"name": "Vérifier réception des assets client",                                  "assignees": [KILIAN_ID], "phase": "Onboarding", "priority": 2, "time_estimate": 1800000, "due_days": None},
    {"name": "Kick-off call (30 min)",                                                "assignees": [KILIAN_ID], "phase": "Onboarding", "priority": 1, "time_estimate": 1800000, "due_days": 5},

    # Phase 2 — Contenu & Stratégie
    # Pas de deadline fixe — dépend de la réception des assets
    {"name": "Définir l'arborescence du site",          "assignees": [KILIAN_ID], "phase": "Contenu & Stratégie", "priority": 2, "time_estimate": 3600000,  "due_days": None},
    {"name": "Valider l'arborescence avec le client",   "assignees": [KILIAN_ID], "phase": "Contenu & Stratégie", "priority": 2, "time_estimate": 1800000,  "due_days": None},
    {"name": "Rédiger les textes de toutes les pages",  "assignees": [KILIAN_ID], "phase": "Contenu & Stratégie", "priority": 2, "time_estimate": 28800000, "due_days": None},

    # Phase 3 — Design
    # Pas de deadline fixe — dépend des assets et du go-live
    {"name": "Créer la charte graphique",                 "assignees": [RAPHAEL_ID], "phase": "Design", "priority": 2, "time_estimate": 28800000, "due_days": None},
    {"name": "Validation charte graphique par le client", "assignees": [KILIAN_ID],  "phase": "Design", "priority": 1, "time_estimate": 1800000,  "due_days": None},
    {"name": "Créer les wireframes UX",                   "assignees": [RAPHAEL_ID], "phase": "Design", "priority": 2, "time_estimate": 28800000, "due_days": None},
    {"name": "Validation wireframes par le client",       "assignees": [KILIAN_ID],  "phase": "Design", "priority": 1, "time_estimate": 1800000,  "due_days": None},
    {"name": "Créer l'UI complète",                       "assignees": [RAPHAEL_ID], "phase": "Design", "priority": 2, "time_estimate": 57600000, "due_days": None},
    {"name": "Validation UI par le client",               "assignees": [KILIAN_ID],  "phase": "Design", "priority": 1, "time_estimate": 1800000,  "due_days": None},

    # Phase 4 — Développement
    {"name": "Configurer Next.js (site.config, routes, structure)",               "assignees": [KILIAN_ID],  "phase": "Développement", "priority": 1, "time_estimate": 7200000,  "due_days": None},
    {"name": "Configurer l'hébergement Vercel",                                   "assignees": [KILIAN_ID],  "phase": "Développement", "priority": 2, "time_estimate": 3600000,  "due_days": None},
    {"name": "Intégrer les politiques de confidentialité, mentions légales, CGU", "assignees": [KILIAN_ID],  "phase": "Développement", "priority": 2, "time_estimate": 3600000,  "due_days": None},
    {"name": "Configurer le bandeau cookies (CMP)",                               "assignees": [KILIAN_ID],  "phase": "Développement", "priority": 2, "time_estimate": 1800000,  "due_days": None},
    {"name": "Configurer le SEO technique (meta, OG, robots.txt, sitemap)",       "assignees": [KILIAN_ID],  "phase": "Développement", "priority": 2, "time_estimate": 7200000,  "due_days": None},
    {"name": "Connecter Google Search Console + GA4 / GTM",                       "assignees": [KILIAN_ID],  "phase": "Développement", "priority": 2, "time_estimate": 3600000,  "due_days": None},
    {"name": "Intégrer les composants UI et animations",                          "assignees": [RAPHAEL_ID], "phase": "Développement", "priority": 2, "time_estimate": 57600000, "due_days": None},
    {"name": "Convertir toutes les images en WebP",                               "assignees": [RAPHAEL_ID], "phase": "Développement", "priority": 3, "time_estimate": 3600000,  "due_days": None},
    {"name": "Intégrer les textes et assets",                                     "assignees": [RAPHAEL_ID], "phase": "Développement", "priority": 3, "time_estimate": 7200000,  "due_days": None},
    {"name": "Vérifier le bon fonctionnement des formulaires de contact",         "assignees": [RAPHAEL_ID], "phase": "Développement", "priority": 2, "time_estimate": 1800000,  "due_days": None},

    # Phase 5 — QA / Recette
    {"name": "Tests responsive (mobile, tablette, desktop)",      "assignees": [RAPHAEL_ID], "phase": "QA / Recette", "priority": 2, "time_estimate": 7200000,  "due_days": None},
    {"name": "Tests cross-browser",                               "assignees": [RAPHAEL_ID], "phase": "QA / Recette", "priority": 2, "time_estimate": 3600000,  "due_days": None},
    {"name": "Tests performance Core Web Vitals / Lighthouse",    "assignees": [KILIAN_ID],  "phase": "QA / Recette", "priority": 2, "time_estimate": 3600000,  "due_days": None},
    {"name": "Tester la vitesse sur mobile (PageSpeed Insights)", "assignees": [KILIAN_ID],  "phase": "QA / Recette", "priority": 2, "time_estimate": 1800000,  "due_days": None},
    {"name": "Tests formulaires, CTA, liens",                     "assignees": [KILIAN_ID],  "phase": "QA / Recette", "priority": 2, "time_estimate": 3600000,  "due_days": None},
    {"name": "Vérifier les balises meta et OG sur chaque page",   "assignees": [KILIAN_ID],  "phase": "QA / Recette", "priority": 3, "time_estimate": 3600000,  "due_days": None},
    {"name": "Vérifier le sitemap soumis dans GSC",               "assignees": [KILIAN_ID],  "phase": "QA / Recette", "priority": 3, "time_estimate": 1800000,  "due_days": None},
    {"name": "Vérification RGPD (cookies, mentions légales)",     "assignees": [KILIAN_ID],  "phase": "QA / Recette", "priority": 2, "time_estimate": 1800000,  "due_days": None},

    # Phase 6 — Go-live
    {"name": "Configurer DNS + SSL",                                        "assignees": [KILIAN_ID], "phase": "Go-live", "priority": 1, "time_estimate": 3600000, "due_days": None},
    {"name": "Vérifier les redirections si ancienne URL existante",         "assignees": [KILIAN_ID], "phase": "Go-live", "priority": 2, "time_estimate": 1800000, "due_days": None},
    {"name": "Mettre en ligne en production",                               "assignees": [KILIAN_ID], "phase": "Go-live", "priority": 1, "time_estimate": 1800000, "due_days": None},
    {"name": "Vérification post-live complète",                             "assignees": [KILIAN_ID], "phase": "Go-live", "priority": 1, "time_estimate": 3600000, "due_days": None},
    {"name": "Vérifier l'indexation dans Google Search Console post-live",  "assignees": [KILIAN_ID], "phase": "Go-live", "priority": 2, "time_estimate": 1800000, "due_days": None},

    # Phase 7 — Livraison
    {"name": "Préparer la fiche technique d'utilisation",          "assignees": [RAPHAEL_ID], "phase": "Livraison", "priority": 2, "time_estimate": 7200000, "due_days": None},
    {"name": "Session de formation client (20-30 min)",            "assignees": [KILIAN_ID],  "phase": "Livraison", "priority": 2, "time_estimate": 1800000, "due_days": None},
    {"name": "Envoyer la facture de solde (50%)",                  "assignees": [KILIAN_ID],  "phase": "Livraison", "priority": 1, "time_estimate": 900000,  "due_days": None},
    {"name": "Envoyer le document de remerciement",                "assignees": [KILIAN_ID],  "phase": "Livraison", "priority": 3, "time_estimate": 900000,  "due_days": None},
    {"name": "Envoyer les liens Trustpilot + Google Reviews",      "assignees": [KILIAN_ID],  "phase": "Livraison", "priority": 3, "time_estimate": 900000,  "due_days": None},
    {"name": "Demander le témoignage vidéo",                       "assignees": [KILIAN_ID],  "phase": "Livraison", "priority": 4, "time_estimate": 900000,  "due_days": None},
    {"name": "Confirmer accord client pour communication publique", "assignees": [KILIAN_ID], "phase": "Livraison", "priority": 2, "time_estimate": 900000,  "due_days": None},

    # Phase 8 — Diffusion
    {"name": "Préparer les visuels pour les publications",    "assignees": [KILIAN_ID], "phase": "Diffusion", "priority": 3, "time_estimate": 7200000, "due_days": None},
    {"name": "Publier Instagram story + Réels",               "assignees": [KILIAN_ID], "phase": "Diffusion", "priority": 3, "time_estimate": 1800000, "due_days": None},
    {"name": "Publier LinkedIn post statique",                "assignees": [KILIAN_ID], "phase": "Diffusion", "priority": 3, "time_estimate": 1800000, "due_days": None},
    {"name": "Publier sur Google My Business",                "assignees": [KILIAN_ID], "phase": "Diffusion", "priority": 4, "time_estimate": 900000,  "due_days": None},
    {"name": "Publier sur Pinterest",                         "assignees": [KILIAN_ID], "phase": "Diffusion", "priority": 4, "time_estimate": 900000,  "due_days": None},
    {"name": "Publier dans /realisations sur le site Develly","assignees": [KILIAN_ID], "phase": "Diffusion", "priority": 3, "time_estimate": 1800000, "due_days": None},
]

TOTAL = len(TASKS)
TEMPLATE_LIST_ID = 901218333882

TEMPLATE_STATUSES = [
    {"status": "backlog",   "color": "#87909e", "orderindex": 0, "type": "open"},
    {"status": "en cours",  "color": "#008844", "orderindex": 1, "type": "custom"},
    {"status": "vérifié",   "color": "#656f7d", "orderindex": 2, "type": "done"},
    {"status": "terminé",   "color": "#008844", "orderindex": 3, "type": "closed"},
]


def create_list(client_name: str) -> str:
    url = f"https://api.clickup.com/api/v2/folder/{FOLDER_ID}/list"
    headers = {"Authorization": API_KEY, "Content-Type": "application/json"}
    payload = {"name": client_name}
    r = requests.post(url, json=payload, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()["id"]


def get_first_open_status(list_id: str) -> str:
    url = f"https://api.clickup.com/api/v2/list/{list_id}"
    headers = {"Authorization": API_KEY}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    statuses = r.json().get("statuses", [])
    print(f"  → statuts disponibles : {[s['status'] for s in statuses]}")
    for s in statuses:
        if s.get("type") == "open":
            return s["status"]
    if statuses:
        return statuses[0]["status"]
    raise RuntimeError("Aucun statut trouvé sur la liste")


def create_task(list_id: str, task: dict, status: str, now_ms: int) -> bool:
    url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
    headers = {"Authorization": API_KEY, "Content-Type": "application/json"}
    payload = {
        "name": task["name"],
        "assignees": task["assignees"],
        "status": status,
        "custom_fields": [
            {
                "id": PHASE_FIELD_ID,
                "value": PHASE_IDS[task["phase"]],
            }
        ],
    }
    if task.get("priority") is not None:
        payload["priority"] = task["priority"]
    if task.get("time_estimate") is not None:
        payload["time_estimate"] = task["time_estimate"]
    if task.get("due_days") is not None:
        payload["due_date"] = now_ms + task["due_days"] * 86400 * 1000
    r = requests.post(url, json=payload, headers=headers, timeout=10)
    if r.status_code not in (200, 201):
        print(f"       → {r.status_code} {r.text}")
    return r.status_code in (200, 201)


def main():
    if not API_KEY:
        print("Erreur : variable d'environnement CLICKUP_API_KEY manquante.")
        sys.exit(1)

    client_name = input("Nom du client : ").strip()
    if not client_name:
        print("Erreur : nom du client vide.")
        sys.exit(1)

    print(f"\nCréation de la liste « {client_name} »...")
    try:
        list_id = create_list(client_name)
        first_status = get_first_open_status(list_id)
    except (requests.HTTPError, RuntimeError) as e:
        print(f"✗ Échec : {e}")
        sys.exit(1)
    print(f"✓ Liste créée, statut open = « {first_status} » (id={list_id})\n")

    now_ms = int(time.time() * 1000)
    success = 0
    for i, task in enumerate(TASKS, 1):
        ok = create_task(list_id, task, first_status, now_ms)
        icon = "✓" if ok else "✗"
        if ok:
            success += 1
        print(f"  [{i:02d}/{TOTAL}] {icon} {task['name']}")
        time.sleep(0.3)

    print(f"\nRécap : {success}/{TOTAL} tâches créées.")


if __name__ == "__main__":
    main()
