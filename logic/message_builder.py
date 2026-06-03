import random
import re
import unicodedata
from pathlib import Path

import anthropic
import yaml

from services.claude_service import ClaudeService


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

Tu écris AU NOM de Develly pour prospecter {company} ({niche}, {ville}).
Develly est l'expéditeur. {company} est le destinataire. Ne confonds pas les deux.

Les templates ci-dessous contiennent déjà toutes les vraies données du prospect.
Toutes les variables sont substituées — génère directement sans demander d'information.

Règles :
- Conserve sens et structure du template
- N'invente rien, toutes les données réelles sont déjà dans les templates
- Pas d'emoji, pas de "—"
- Respecte les sauts de ligne du template
- Pas de prénom : normal. "Bonjour," ou "une dernière chose," sans nom sont des formulations finales — génère directement sans demander le prénom
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
- Note 0/5, 0 avis, ou écart de note vide : donnée non disponible, reformule sans la mentionner
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
- Un message doit jamais commencer par un espace ou ","
""".strip()

_SYSTEM_EMAIL = """
Email :
- Ligne 1 : OBJET: (court, factuel, non commercial)
- Paragraphes courts, ton professionnel mais accessible
- Pas de signature après la formule de politesse
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
    no_concurrent: bool,
    templates: list[tuple[int, str, dict]],
    company: str = "",
    niche: str = "",
) -> str:
    parts = [
        _SYSTEM_BASE
        .replace("{canal}", canal)
        .replace("{angle}", angle)
        .replace("{company}", company)
        .replace("{niche}", niche)
    ]

    if canal == "WHATSAPP":
        parts.append(_SYSTEM_WHATSAPP)
        if len(templates) > 1:
            parts.append(_SYSTEM_WA_SEQUENCE)
    elif canal == "EMAIL":
        parts.append(_SYSTEM_EMAIL)

    if no_concurrent:
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


def _find_niche_entry(niches: list[dict], niche_val: str) -> dict | None:
    """Match niche_val to the best entry in a per-niche template list.

    Scoring: exact normalized match > longest partial word match > no match.
    'Partial' means every key word is a substring of some niche word
    (handles 'auto' ⊂ 'automobile', 'interieur' ⊂ "d'interieur", etc.)
    Most-specific key (most words) wins on ties.
    """
    def _norm_key(s: str) -> str:
        return _normalize(s.replace("_", " "))

    n_norm = _norm_key(niche_val)
    n_words = n_norm.split()

    def score(entry: dict) -> int:
        k_norm = _norm_key(entry.get("key", ""))
        k_words = k_norm.split()
        if k_norm == n_norm:
            return len(k_words) + 100  # exact match bonus
        if all(any(kw in nw for nw in n_words) for kw in k_words):
            return len(k_words)
        return -1

    scored = [(score(e), e) for e in niches]
    valid = [(s, e) for s, e in scored if s >= 0]
    if not valid:
        return None
    return max(valid, key=lambda x: x[0])[1]


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
        self._claude        = ClaudeService()
        self._templates_dir = Path(f"templates/{config_name}")

    def _template_path(self, angle: str, canal: str, step: int) -> Path:
        a = _ANGLE_SLUG.get(angle, angle.lower())
        c = _CANAL_SLUG.get(canal, canal.lower())
        s = _STEP_SLUG.get(step, f"j{step}")
        if canal == "WHATSAPP":
            wa_dir = self._templates_dir / "wa"
            if wa_dir.exists():
                matches = sorted(wa_dir.glob(f"*_{a}_{s}.yaml"))
                if matches:
                    return matches[0]
        elif canal == "EMAIL":
            email_dir = self._templates_dir / "email"
            if email_dir.exists():
                # Positional match: step 0→1st file, 1→2nd, 2→3rd (sorted alpha)
                matches = sorted(email_dir.glob(f"*_{a}_*.yaml"))
                if step < len(matches):
                    return matches[step]
        return self._templates_dir / f"{a}_{c}_{s}.yaml"

    def _build_variables(self, contact: dict, concurrent_data: dict) -> dict[str, str]:
        attrs       = contact.get("attributes", {})
        company_cfg = self._config.get("company", {})
        c1          = concurrent_data.get("concurrent_1", {})
        c2          = concurrent_data.get("concurrent_2", {})

        niche    = concurrent_data.get("niche") or str(attrs.get("CATEGORIE") or "")
        if niche:
            niche = niche[0].lower() + niche[1:]
        note_val = attrs.get("AVERAGE_RATE") or concurrent_data.get("lead_note")
        avis_val = attrs.get("NUMBER_OF_RATE") or concurrent_data.get("lead_avis")

        return {
            "prenom":               str(attrs.get("PRENOM") or attrs.get("FIRSTNAME") or ""),
            "company":              str(attrs.get("COMPANY") or ""),
            "niche":                niche,
            "ville":                concurrent_data.get("ville_nom") or str(attrs.get("CITY_ID") or ""),
            "note":                 _note(note_val),
            "nb_avis":              str(int(avis_val) if avis_val else ""),
            "sender":               str(company_cfg.get("sender") or ""),
            "expediteur":           str(company_cfg.get("sender") or ""),
            "calendly":             str(company_cfg.get("calendly") or ""),
            "concurrent_1_nom":     str(c1.get("nom") or ""),
            "concurrent_1_note":    _note(c1.get("note")) or "0",
            "concurrent_1_nb_avis": str(c1.get("nb_avis") or "0"),
            "concurrent_2_nom":     str(c2.get("nom") or ""),
            "concurrent_2_note":    _note(c2.get("note")) or "0",
            "concurrent_2_nb_avis": str(c2.get("nb_avis") or "0"),
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
                continue
            tpl = yaml.safe_load(tpl_path.read_text(encoding="utf-8"))

            if "niches" in tpl:
                niche_val = variables.get("niche", "")
                entry = _find_niche_entry(tpl["niches"], niche_val)
                if entry is None:
                    continue
                filled = subst(entry.get("message", ""))
                if canal == "EMAIL":
                    raw_objet = entry.get("objet", "")
                    if isinstance(raw_objet, list):
                        raw_objet = random.choice(raw_objet) if raw_objet else ""
                    if raw_objet:
                        filled = f"OBJET: {subst(raw_objet)}\n\n{filled}"
                punchlines = entry.get("punchlines", {}) if canal == "WHATSAPP" else {}
            else:
                filled = subst(tpl.get("message", ""))
                if canal == "EMAIL":
                    raw_objet = tpl.get("objet", "")
                    if isinstance(raw_objet, list):
                        raw_objet = random.choice(raw_objet) if raw_objet else ""
                    if raw_objet:
                        filled = f"OBJET: {subst(raw_objet)}\n\n{filled}"
                punchlines = tpl.get("punchlines", {}) if canal == "WHATSAPP" else {}

            filled = re.sub(r'Bonjour\s+,', 'Bonjour,', filled)
            filled = re.sub(r'(?m)^, ', '', filled)  # "{prenom}, texte" → ", texte" when prenom empty
            loaded.append((step, filled, punchlines))

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
            no_concurrent=variables.get("_no_concurrent", False),
            templates=loaded,
            company=variables.get("company", ""),
            niche=variables.get("niche", ""),
        )

        try:
            prenom_note = (
                " Pas de prénom — normal, les salutations sont déjà adaptées."
                if not variables.get("prenom") else ""
            )
            raw, _ = self._claude.complete(
                system=system_prompt,
                user=(
                    f"Prospect : {variables.get('company')} ({variables.get('ville')}).{prenom_note}\n"
                    "Génère directement au format ===MESSAGE_N===."
                ),
                max_tokens=1200,
            )
        except anthropic.RateLimitError as exc:
            print(f"  [Claude] RateLimitError : {exc}")
            raise

        # Filet de sécurité : SKIP retourné par le LLM
        if raw.strip().upper() == "SKIP":
            print(f"  ← SKIP LLM — {canal} non généré (prospect hors niche)")
            return {"__skip__": True}

        # Détection réponse hors format (LLM confus / demande de variables)
        if "===MESSAGE_" not in raw:
            print(f"  [Claude] Réponse hors format ({canal} {angle}) — ignoré")
            print(f"  [Claude] Début réponse : {raw[:120]!r}")
            remaining = set()
            for _, content, _ in loaded:
                remaining.update(re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', content))
            if remaining:
                print(f"  [Claude] Placeholders NON substitués dans templates : {sorted(remaining)}")
            else:
                print("  [Claude] Templates complets (aucun placeholder non substitué)")
            for i, (step, content, _) in enumerate(loaded, start=1):
                preview = content[:300].replace("\n", "↵")
                print(f"  [Claude] Template {i} (step={step}) → {preview!r}")
            return {}

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
