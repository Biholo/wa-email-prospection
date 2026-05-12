import os
from pathlib import Path

from openai import OpenAI


class OpenAIService:
    def __init__(self):
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = "gpt-4o-mini"

    def _build_variables(self, contact: dict, concurrent_data: dict) -> dict:
        attrs = contact.get("attributes", {})

        def _c(key: str, field: str) -> str:
            return str(concurrent_data.get(key, {}).get(field) or "")

        return {
            "prenom": str(attrs.get("PRENOM") or ""),
            "nom": str(attrs.get("NOM") or ""),
            "entreprise": str(attrs.get("COMPANY") or ""),
            "ville": str(attrs.get("CITY_ID") or ""),
            "note": str(attrs.get("AVERAGE_RATE") or ""),
            "nb_avis": str(attrs.get("NUMBER_OF_RATE") or ""),
            "site": str(attrs.get("WEBSITE_URL") or ""),
            "pagespeed_score": str(attrs.get("PAGESPEED_SCORE") or ""),
            "angle": str(attrs.get("ANGLE") or ""),
            "plus_avis_nom": _c("plus_avis", "nom"),
            "plus_avis_note": _c("plus_avis", "note"),
            "plus_avis_nb_avis": _c("plus_avis", "nb_avis"),
            "meilleure_note_nom": _c("meilleure_note", "nom"),
            "meilleure_note_note": _c("meilleure_note", "note"),
            "meilleure_note_nb_avis": _c("meilleure_note", "nb_avis"),
            "meilleur_ratio_nom": _c("meilleur_ratio", "nom"),
            "meilleur_ratio_note": _c("meilleur_ratio", "note"),
            "meilleur_ratio_nb_avis": _c("meilleur_ratio", "nb_avis"),
        }

    def generate(
        self,
        system: str,
        canal_prompt: str,
        contact: dict,
        concurrent_data: dict,
        extra_vars: dict | None = None,
    ) -> dict:
        """
        Génère un message et retourne {"objet": str, "corps": str}.
        Pour les canaux non-email, objet est vide.
        Le prompt canal peut contenir {angle}, {calendly}, {prenom}, etc.
        """
        variables = self._build_variables(contact, concurrent_data)
        if extra_vars:
            variables.update({k: str(v) for k, v in extra_vars.items()})

        prompt = canal_prompt
        for key, value in variables.items():
            prompt = prompt.replace(f"{{{key}}}", value)

        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=600,
        )

        raw = response.choices[0].message.content.strip()

        # Email : GPT préfixe l'objet avec "OBJET: ..."
        objet = ""
        corps = raw
        lines = raw.splitlines()
        if lines and lines[0].upper().startswith("OBJET:"):
            objet = lines[0][len("OBJET:"):].strip()
            corps = "\n".join(lines[1:]).strip()

        return {"objet": objet, "corps": corps}

    # Conservé pour compatibilité avec message_builder.py
    def generer_message(
        self,
        prompt_path: str,
        contact: dict,
        concurrent_data: dict,
        canal: str,
        extra_vars: dict | None = None,
    ) -> str:
        prompt_dir = Path(prompt_path)
        system_content = (prompt_dir / "system.txt").read_text(encoding="utf-8")
        canal_content = (prompt_dir / f"{canal.lower()}.txt").read_text(encoding="utf-8")

        result = self.generate(
            system=system_content,
            canal_prompt=canal_content,
            contact=contact,
            concurrent_data=concurrent_data,
            extra_vars=extra_vars,
        )
        objet_line = f"OBJET: {result['objet']}\n\n" if result["objet"] else ""
        return f"{objet_line}{result['corps']}"
