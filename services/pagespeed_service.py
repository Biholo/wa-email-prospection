import os

import httpx

PAGESPEED_API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


class PageSpeedService:
    def __init__(self):
        self.api_key = os.getenv("PAGESPEED_API_KEY")

    async def analyse_full_async(self, url: str, strategy: str = "mobile") -> dict:
        """Returns raw PageSpeed Insights JSON for audit engine (mobile or desktop)."""
        if not self.api_key:
            return {"error": "PAGESPEED_API_KEY not set — skipping PSI"}
        params = {"url": url, "strategy": strategy, "category": "performance", "key": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(PAGESPEED_API_URL, params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            return {"error": str(exc)}

    def analyse(self, url: str) -> dict:
        if not url:
            return {"score": None, "vitesse": None, "https": False, "mobile": False}

        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        params = {
            "url": url,
            "strategy": "mobile",
            "category": "performance",
        }
        if self.api_key:
            params["key"] = self.api_key

        try:
            with httpx.Client(timeout=45) as client:
                resp = client.get(PAGESPEED_API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            lhr = data.get("lighthouseResult", {})
            categories = lhr.get("categories", {})
            perf_score = categories.get("performance", {}).get("score")
            score = int(perf_score * 100) if perf_score is not None else None

            audits = lhr.get("audits", {})
            fcp = audits.get("first-contentful-paint", {}).get("displayValue", "")
            mobile_ok = audits.get("viewport", {}).get("score", 0) == 1

            return {
                "score": score,
                "vitesse": fcp,
                "https": url.startswith("https://"),
                "mobile": mobile_ok,
            }
        except Exception:
            return {
                "score": None,
                "vitesse": None,
                "https": url.startswith("https://"),
                "mobile": False,
            }
