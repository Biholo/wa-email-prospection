import os
from urllib.parse import quote

import httpx

BREVO_API_BASE = "https://api.brevo.com/v3"
_PAGE_SIZE = 500


class BrevoService:
    def __init__(self):
        self.api_key = os.getenv("BREVO_API_KEY")
        self.headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_contacts(
        self,
        liste_id: int,
        attr_equals: dict | None = None,
        attr_not_equals: dict | None = None,
        attr_lte: dict | None = None,
        attr_gte: dict | None = None,
        attr_is_null: dict | None = None,
        brevo_filter: str | None = None,
        limit: int | None = None,
        debug: bool = False,
    ) -> list:
        """
        Fetch contacts from a Brevo list with client-side filters.

        brevo_filter  : raw Brevo API filter string, e.g. 'equals(PROPRIETAIRE,"Develly")'
        attr_equals   : {"PROPRIETAIRE": "x"} — skips if PROPRIETAIRE empty/absent
        attr_not_equals: {"CATEGORIE": "Expert-comptable"} — rejects if field matches value
        attr_lte      : {"PROCHAINE_RELANCE": "2024-11-05"} — passes if None/empty or <= threshold
        attr_gte      : {"TOTAL_MESSAGE_ENVOYE": 1} — passes only if numeric value >= threshold
        attr_is_null  : {"PROCHAINE_RELANCE": True} — True=must be null/empty, False=must not
        limit         : stop collecting once this many contacts pass all filters
        debug         : print raw attributes of first contact + rejection stats
        """
        url = f"{BREVO_API_BASE}/contacts"
        contacts = []
        offset = 0
        total_api = None
        reject_counts: dict[str, int] = {}
        first_contact_shown = False

        while True:
            params: dict = {"limit": _PAGE_SIZE, "offset": offset, "listIds": liste_id}
            if brevo_filter:
                params["filter"] = brevo_filter
            with httpx.Client(headers=self.headers, timeout=30) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            batch = data.get("contacts", [])
            total_api = data.get("count", 0)

            if offset == 0:
                pages = max(1, (total_api + _PAGE_SIZE - 1) // _PAGE_SIZE)
                print(f"       [Brevo] liste #{liste_id} : {total_api} contacts, {pages} page(s)")
                if debug and brevo_filter:
                    print(f"       [DEBUG] filtre API               : {brevo_filter}")
                if debug and attr_equals:
                    print(f"       [DEBUG] filtres attr_equals      : {attr_equals}")
                if debug and attr_not_equals:
                    print(f"       [DEBUG] filtres attr_not_equals  : {attr_not_equals}")
                if debug and attr_lte:
                    print(f"       [DEBUG] filtres attr_lte     : {attr_lte}")
                if debug and attr_gte:
                    print(f"       [DEBUG] filtres attr_gte     : {attr_gte}")
                if debug and attr_is_null:
                    print(f"       [DEBUG] filtres attr_is_null : {attr_is_null}")

            for contact in batch:
                attrs = contact.get("attributes", {})

                if debug and not first_contact_shown:
                    first_contact_shown = True
                    print(f"       [DEBUG] 1er contact brut : email={contact.get('email')}")
                    for k, v in sorted(attrs.items()):
                        if v not in (None, "", False):
                            print(f"              {k} = {repr(v)}")

                skip_reason = None

                if attr_equals:
                    for k, v in attr_equals.items():
                        val = attrs.get(k)
                        if (val or "") != str(v):
                            skip_reason = f"attr_equals[{k}]={repr(val)} != {repr(v)}"
                            break

                if skip_reason is None and attr_not_equals:
                    for k, v in attr_not_equals.items():
                        val = attrs.get(k)
                        if (val or "") == str(v):
                            skip_reason = f"attr_not_equals[{k}]={repr(val)} == {repr(v)}"
                            break

                if skip_reason is None and attr_lte:
                    for k, threshold in attr_lte.items():
                        val = attrs.get(k)
                        if val is None or val == "":
                            continue
                        if str(val)[:10] > str(threshold)[:10]:
                            skip_reason = f"attr_lte[{k}]={repr(val)} > {repr(threshold)}"
                            break

                if skip_reason is None and attr_gte:
                    for k, threshold in attr_gte.items():
                        val = attrs.get(k)
                        if val is None or val == "":
                            skip_reason = f"attr_gte[{k}]=null < {repr(threshold)}"
                            break
                        try:
                            if float(val) < float(threshold):
                                skip_reason = f"attr_gte[{k}]={repr(val)} < {repr(threshold)}"
                                break
                        except (ValueError, TypeError):
                            if str(val)[:10] < str(threshold)[:10]:
                                skip_reason = f"attr_gte[{k}]={repr(val)} < {repr(threshold)}"
                                break

                if skip_reason is None and attr_is_null:
                    for k, should_be_null in attr_is_null.items():
                        val = attrs.get(k)
                        is_null = val is None or val == ""
                        if should_be_null and not is_null:
                            skip_reason = f"attr_is_null[{k}]={repr(val)} not null"
                            break
                        if not should_be_null and is_null:
                            skip_reason = f"attr_is_null[{k}] is null"
                            break

                if skip_reason:
                    key = skip_reason.split("[")[1].split("]")[0]
                    reject_counts[key] = reject_counts.get(key, 0) + 1
                    continue

                contacts.append(contact)
                if limit and len(contacts) >= limit:
                    if debug and reject_counts:
                        print(f"       [DEBUG] rejets par filtre : {reject_counts}")
                    return contacts

            offset += len(batch)
            if offset >= total_api or not batch:
                break

        if debug and reject_counts:
            print(f"       [DEBUG] rejets par filtre : {reject_counts}")

        return contacts

    def update_contact(self, email: str, fields: dict) -> None:
        if not email:
            return
        url = f"{BREVO_API_BASE}/contacts/{quote(email, safe='')}"
        with httpx.Client(headers=self.headers, timeout=30) as client:
            resp = client.put(url, json={"attributes": fields})
            resp.raise_for_status()

    def move_to_list(self, email: str, from_list_id: int, to_list_id: int) -> None:
        if not email:
            return
        with httpx.Client(headers=self.headers, timeout=30) as client:
            resp = client.post(
                f"{BREVO_API_BASE}/contacts/lists/{to_list_id}/contacts/add",
                json={"emails": [email]},
            )
            resp.raise_for_status()
            resp = client.post(
                f"{BREVO_API_BASE}/contacts/lists/{from_list_id}/contacts/remove",
                json={"emails": [email]},
            )
            resp.raise_for_status()
