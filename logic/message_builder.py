import re
import unicodedata
from pathlib import Path

import openai
import yaml

from services.openai_service import OpenAIService

_OWNER_WA = "33641552699"  # Kilian — alertes critiques


def _notify_openai_quota() -> None:
    try:
        from services.wasender_service import WasenderService
        WasenderService().send_whatsapp(
            _OWNER_WA,
            "⚠️ Develly: plus de crédits OpenAI — pipeline arrêté. Rechargez sur platform.openai.com.",
        )
        print("[OpenAI] Notification quota envoyée → WA perso")
    except Exception as e:
        print(f"[OpenAI] Erreur envoi notification quota : {e}")


# ---------------------------------------------------------------------------
# Utilitaires de nettoyage — appliqués AVANT tout appel LLM
# ---------------------------------------------------------------------------

def short_name(name: str) -> str:
    """Coupe après |, :, –, —, -, ,"""
    return re.split(r"[|:–—\-,]", name)[0].strip()


def _normalize(name: str) -> str:
    """Minuscules, sans accents, sans espaces superflus."""
    name = short_name(name).lower().strip()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name


def remove_self_competitors(variables: dict) -> dict:
    """Retire un concurrent identique au prospect (insensible casse/accents).
    Fait remonter concurrent_2 si concurrent_1 est retiré.
    """
    company = variables.get("company", "")
    if not company:
        return variables

    company_norm = _normalize(company)

    for slot in ("concurrent_1", "concurrent_2"):
        nom_key = f"{slot}_nom"
        if variables.get(nom_key) and _normalize(variables[nom_key]) == company_norm:
            variables[nom_key] = ""
            variables[f"{slot}_note"] = ""
            variables[f"{slot}_nb_avis"] = ""

    if not variables.get("concurrent_1_nom") and variables.get("concurrent_2_nom"):
        for field in ("nom", "note", "nb_avis"):
            variables[f"concurrent_1_{field}"] = variables.get(f"concurrent_2_{field}", "")
            variables[f"concurrent_2_{field}"] = ""

    return variables


def validate_reputation_vars(variables: dict) -> dict:
    try:
        ecart = float(variables.get("ecart_note", "0").replace(",", "."))
        if ecart < 0.3:
            variables["ecart_note"] = ""
    except (ValueError, TypeError):
        variables["ecart_note"] = ""
    return variables


def prepare_variables(variables: dict, angle: str) -> dict:
    """Nettoie les variables et pose le flag _no_concurrent."""
    # 1. Noms courts
    for key in ("concurrent_1_nom", "concurrent_2_nom", "company"):
        if variables.get(key):
            variables[key] = short_name(variables[key])

    # 2. Retire les concurrents == prospect
    variables = remove_self_competitors(variables)

    # 3. Flag no_concurrent
    variables["_no_concurrent"] = not bool(
        variables.get("concurrent_1_nom") or variables.get("concurrent_2_nom")
    )

    # 4. Validation angle RÉPUTATION
    if angle == "RÉPUTATION":
        variables = validate_reputation_vars(variables)

    return variables


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------

_ANGLE_SLUG: dict[str, str] = {
    "INVISIBILITÉ": "invisibilite",
    "RÉPUTATION":   "reputation",
    "ESTHÉTISME":   "esthetisme",
}

_CANAL_SLUG: dict[str, str] = {
    "EMAIL":    "email",
    "WHATSAPP": "wa",
    "SMS":      "sms",
}

_STEP_SLUG: dict[int, str] = {0: "j0", 1: "j3", 2: "j7"}


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_SYSTEM_BASE = """
Tu prospectes en B2B pour Develly (agence web, visibilité, conversion).
Canal : {canal} — Angle : {angle}

Règles :
- Conserve sens et structure du template
- N'invente rien, utilise uniquement les variables fournies
- Pas d'emoji, pas de "—"
- Sans prénom : adapte la salutation sans laisser de vide
- Noms de concurrents/entreprises : raccourcis-les dans cet ordre :
  1. Coupe après "|", "–", "-", ":", ","
  2. Retire : société, entreprise, agence, services, sarl, sas, eurl, groupe
  3. Tout en minuscules → capitalise chaque mot
  4. Acronymes en majuscules : ne pas toucher
- Si un concurrent est identique ou très proche de {company}, ignore-le.
  S'il n'en reste qu'un, reformule sans laisser de vide.
- Si le nom d'un concurrent indique clairement une activité sans rapport avec {niche},
  ignore ce concurrent uniquement, continue avec les autres.
- La variable {niche} est au singulier. Dans les phrases où elle désigne
  un groupe ("les {niche}", "beaucoup de {niche}"), accorde-la au pluriel
  et applique l'élision si nécessaire (ex: "les Électriciens", "d'Électriciens")
- Si le nom est entièrement en majuscules sans être un acronyme connu
  (ex: "ENTREPRISE CALVET"), convertis en capitalisation normale : "Calvet"
  après application du short_name
- Si un nom de concurrent contient "et" (ex: "Esteban et Fils"), raccourcis-le
  pour supprimer le suffixe après "et" : "Esteban et Fils" → "Esteban".
  Cela évite d'avoir deux "et" dans la même phrase.
- Si le nom de {company} indique clairement une activité sans rapport avec {niche},
  ne génère pas le message et réponds uniquement : SKIP
""".strip()

_SYSTEM_WHATSAPP = """
WhatsApp :
- Pas d'objet ni de signature
- Max 3 blocs, phrases courtes, ton naturel
- Punchlines hook : amorce du paragraphe suivant, jamais en dernière position
- Punchlines closing : remplacent uniquement la dernière phrase
- Format obligatoire : hook → corps (1-2 phrases) → closing
- Un hook ne peut pas précéder directement une closing
- Choisis l'alternative qui s'intègre le mieux au contexte
""".strip()

_SYSTEM_EMAIL = """
Email :
- Ligne 1 : OBJET: (court, factuel, non commercial)
- Signature {expediteur} en fin de message
- Paragraphes courts, ton professionnel mais accessible
""".strip()

_SYSTEM_NO_CONCURRENT = """
Pas de concurrent identifié. Ne le mentionne pas.
Oriente sur l'opportunité de capter le trafic local en premier.
""".strip()

_SYSTEM_WA_SEQUENCE = """
Ces messages forment une séquence. Le second prolonge le premier sans répéter ses arguments : plus direct, plus concret, orienté décision.
""".strip()


def _system_format(n: int) -> str:
    lines = [
        "Retourne uniquement les messages dans ce format exact,",
        "sans commentaire, sans texte avant ou après :",
        "",
    ]
    for i in range(1, n + 1):
        lines.append(f"===MESSAGE_{i}===")
        lines.append("(message réécrit)")
        lines.append("")
    return "\n".join(lines).strip()


def _build_system_prompt(
    canal: str,
    angle: str,
    expediteur: str,
    no_concurrent: bool,
    templates: list[tuple[int, str, dict]],
) -> str:
    parts = [
        _SYSTEM_BASE.replace("{canal}", canal).replace("{angle}", angle)
    ]

    if canal == "WHATSAPP":
        parts.append(_SYSTEM_WHATSAPP)
        if len(templates) > 1:
            parts.append(_SYSTEM_WA_SEQUENCE)
    elif canal == "EMAIL":
        parts.append(_SYSTEM_EMAIL.replace("{expediteur}", expediteur))

    if no_concurrent and angle == "INVISIBILITÉ":
        parts.append(_SYSTEM_NO_CONCURRENT)

    tpl_blocks = []
    for i, (step, content, punchlines) in enumerate(templates, start=1):
        block = f"TEMPLATE {i} ({_STEP_SLUG.get(step, f'j{step}')}) :\n{content}"
        if canal == "WHATSAPP" and punchlines:
            for group_name, options in punchlines.items():
                if options:
                    opts_str = "\n".join(f'  - "{opt}"' for opt in options)
                    block += f"\n\nAlternatives [{group_name}] (choisis-en une) :\n{opts_str}"
        tpl_blocks.append(block)
    parts.append("\n\n".join(tpl_blocks))
    parts.append(_system_format(len(templates)))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _note(val) -> str:
    try:
        f = float(val)
        if f == 0:
            return ""
        return f"{f:.1f}".replace(".", ",")
    except (ValueError, TypeError):
        return str(val or "")


def _clean_placeholders(text: str) -> str:
    return re.sub(r'\{[a-zA-Z_][a-zA-Z0-9_]*\}', '', text).strip()


def _parse_multi_response(raw: str, canal: str) -> list[dict]:
    blocks = re.split(r'===MESSAGE_\d+===', raw)
    blocks = [b.strip() for b in blocks if b.strip()]
    results = []
    for block in blocks:
        objet = ""
        corps = block
        if canal == "EMAIL":
            lines = block.splitlines()
            if lines and lines[0].upper().startswith("OBJET:"):
                objet = lines[0][len("OBJET:"):].strip()
                corps = "\n".join(lines[1:]).strip()
        results.append({
            "objet": _clean_placeholders(objet),
            "corps": _clean_placeholders(corps),
        })
    return results


# ---------------------------------------------------------------------------
# MessageBuilder
# ---------------------------------------------------------------------------

class MessageBuilder:
    def __init__(self, config_name: str, config: dict):
        self._config_name   = config_name
        self._config        = config
        self._openai        = OpenAIService()
        self._templates_dir = Path(f"templates/{config_name}")

    def _template_path(self, angle: str, canal: str, step: int) -> Path:
        a = _ANGLE_SLUG.get(angle, angle.lower())
        c = _CANAL_SLUG.get(canal, canal.lower())
        s = _STEP_SLUG.get(step, f"j{step}")
        return self._templates_dir / f"{a}_{c}_{s}.yaml"

    def _build_variables(self, contact: dict, concurrent_data: dict) -> dict[str, str]:
        attrs       = contact.get("attributes", {})
        company_cfg = self._config.get("company", {})
        c1          = concurrent_data.get("concurrent_1", {})
        c2          = concurrent_data.get("concurrent_2", {})

        niche    = concurrent_data.get("niche") or str(attrs.get("CATEGORIE") or "")
        note_val = attrs.get("AVERAGE_RATE") or concurrent_data.get("lead_note")
        avis_val = attrs.get("NUMBER_OF_RATE") or concurrent_data.get("lead_avis")

        return {
            "prenom":               str(attrs.get("PRENOM") or ""),
            "company":              str(attrs.get("COMPANY") or ""),
            "niche":                niche,
            "ville":                concurrent_data.get("ville_nom") or str(attrs.get("CITY_ID") or ""),
            "note":                 _note(note_val),
            "nb_avis":              str(int(avis_val) if avis_val else ""),
            "sender":               str(company_cfg.get("sender") or ""),
            "expediteur":           str(company_cfg.get("sender") or ""),
            "calendly":             str(company_cfg.get("calendly") or ""),
            "concurrent_1_nom":     str(c1.get("nom") or ""),
            "concurrent_1_note":    _note(c1.get("note")),
            "concurrent_1_nb_avis": str(c1.get("nb_avis") or ""),
            "concurrent_2_nom":     str(c2.get("nom") or ""),
            "concurrent_2_note":    _note(c2.get("note")),
            "concurrent_2_nb_avis": str(c2.get("nb_avis") or ""),
            "ecart_note":           _note(concurrent_data.get("ecart_note")),
            "ecart_avis":           str(concurrent_data.get("ecart_avis") or "0"),
        }

    def _load_and_substitute(
        self,
        angle: str,
        canal: str,
        steps: list[int],
        variables: dict[str, str],
    ) -> list[tuple[int, str, dict]]:
        def subst(text: str) -> str:
            for key, val in variables.items():
                if not key.startswith("_"):
                    text = text.replace(f"{{{key}}}", val)
            return text

        loaded = []
        for step in steps:
            tpl_path = self._template_path(angle, canal, step)
            if not tpl_path.exists():
                print(f"  [SKIP] template introuvable : {tpl_path.name}")
                continue
            tpl    = yaml.safe_load(tpl_path.read_text(encoding="utf-8"))
            filled = subst(tpl.get("message", ""))
            if canal == "EMAIL" and tpl.get("objet"):
                filled = f"OBJET: {subst(tpl['objet'])}\n\n{filled}"
            punchlines = tpl.get("punchlines", {}) if canal == "WHATSAPP" else {}
            loaded.append((step, filled, punchlines))
            print(f"  [LOAD] {tpl_path.name} → step {step}")

        return loaded

    def _generate_canal(
        self,
        contact: dict,
        angle: str,
        canal: str,
        steps: list[int],
        concurrent_data: dict,
    ) -> dict[str, dict]:
        variables  = prepare_variables(self._build_variables(contact, concurrent_data), angle)
        canal_slug = _CANAL_SLUG.get(canal, canal.lower())

        loaded = self._load_and_substitute(angle, canal, steps, variables)
        if not loaded:
            return {}

        system_prompt = _build_system_prompt(
            canal=canal,
            angle=angle,
            expediteur=variables["expediteur"],
            no_concurrent=variables.get("_no_concurrent", False),
            templates=loaded,
        )

        filled_vars = {k: v for k, v in variables.items() if v and not k.startswith("_")}
        empty_vars  = [k for k, v in variables.items() if not v and not k.startswith("_")]
        print(f"\n  [{canal}] appel OpenAI — {len(loaded)} template(s)")
        print(f"  → variables remplies  : {filled_vars}")
        print(f"  → variables vides     : {empty_vars}")
        print(f"  → system prompt :\n{system_prompt}")

        try:
            response = self._openai._client.chat.completions.create(
                model=self._openai.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": "Génère les messages."},
                ],
                temperature=0.7,
                max_tokens=1200,
            )
        except openai.RateLimitError as exc:
            if "insufficient_quota" in str(exc):
                _notify_openai_quota()
            raise
        raw   = response.choices[0].message.content.strip()
        usage = response.usage
        print(f"  ← réponse :\n{raw}")
        print(f"  ← {usage.prompt_tokens}p + {usage.completion_tokens}c tokens")

        # Filet de sécurité : SKIP retourné par le LLM
        if raw.strip().upper() == "SKIP":
            print(f"  ← SKIP LLM — {canal} non généré (prospect hors niche)")
            return {"__skip__": True}

        parsed  = _parse_multi_response(raw, canal)
        results = {}
        for i, (step, _, _punchlines) in enumerate(loaded):
            key          = f"{canal_slug}_{step + 1}"
            results[key] = parsed[i] if i < len(parsed) else {"objet": "", "corps": ""}

        return results

    def build_all(
        self,
        contact: dict,
        angle: str,
        concurrent_data: dict,
        combos_filter: list[str] | None = None,
    ) -> dict:
        """
        2 appels OpenAI max par angle :
        - 1 pour les emails  (steps 0, 1, 2 → email_1, email_2, email_3)
        - 1 pour les WA      (steps 0, 1    → wa_1, wa_2)
        """
        results = {}

        email_steps = [s for s in [0, 1, 2]
                       if combos_filter is None or f"email_{s + 1}" in combos_filter]
        if email_steps:
            canal_result = self._generate_canal(contact, angle, "EMAIL", email_steps, concurrent_data)
            if canal_result.get("__skip__"):
                return {"__skip__": True}
            results.update(canal_result)

        wa_steps = [s for s in [0, 1]
                    if combos_filter is None or f"wa_{s + 1}" in combos_filter]
        if wa_steps:
            canal_result = self._generate_canal(contact, angle, "WHATSAPP", wa_steps, concurrent_data)
            if canal_result.get("__skip__"):
                return {"__skip__": True}
            results.update(canal_result)

        return results
