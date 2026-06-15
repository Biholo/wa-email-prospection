"""Site audit engine  -  orchestrates all checks and returns a scored report."""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from services.pagespeed_service import PageSpeedService
from services.supabase_service import SupabaseService

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_FETCH_TIMEOUT = 10
_AI_CRAWLERS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "PerplexityBot", "Google-Extended"]
_HEADERS = {"User-Agent": "DevellyAuditBot/1.0"}
_MAX_NAV_PAGES = int(os.getenv("AUDIT_MAX_PAGES", "5"))
_OUTPUT_SCREENSHOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output", "screenshots")

_SOCIAL_PATTERNS: dict[str, list[str]] = {
    "linkedin":  ["linkedin.com/company/", "linkedin.com/in/"],
    "facebook":  ["facebook.com/"],
    "instagram": ["instagram.com/"],
    "tiktok":    ["tiktok.com/@"],
    "youtube":   ["youtube.com/@", "youtube.com/channel/", "youtube.com/c/"],
    "twitter":   ["twitter.com/", "x.com/"],
    "pinterest": ["pinterest.com/", "pinterest.fr/"],
}

_TECH_SIGNATURES: dict[str, dict[str, list[str]]] = {
    "Framework JS": {
        "React": ["__reactfiber", "data-reactroot", "react-dom"],
        "Vue.js": ["__vue__", "data-v-", "vue.min.js", "cdn.jsdelivr.net/npm/vue"],
        "Next.js": ["__next_data__", "_next/static"],
        "Nuxt.js": ["__nuxt", "/_nuxt/"],
        "Angular": ["ng-version=\"", "angular.min.js", "ng-app="],
        "Svelte": ["__svelte", "svelte/internal"],
        "Astro": ["astro-island", "data-astro"],
        "Alpine.js": ["x-data=", "cdn.jsdelivr.net/npm/alpinejs"],
        "Stimulus": ["stimulus"],
        "SolidJS": ["solid-js"],
    },
    "Librairies JS": {
        "jQuery": ["jquery.min.js", "jquery/dist", "cdn.jsdelivr.net/npm/jquery"],
        "jQuery Migrate": ["jquery-migrate"],
        "Bootstrap": ["bootstrap.min.js", "bootstrap.min.css", "bootstrap.bundle"],
        "Lodash": ["lodash.min.js", "cdn.jsdelivr.net/npm/lodash"],
        "Moment.js": ["moment.min.js", "cdn.jsdelivr.net/npm/moment"],
        "D3.js": ["d3.min.js", "d3.v"],
        "Reveal.js": ["reveal.js", "reveal.initialize"],
        "Handlebars": ["handlebars.min.js", "handlebars.compile"],
        "Select2": ["select2.min.js", "select2.min.css"],
        "Swiper": ["swiper.min.js", "swiper-wrapper"],
        "GSAP": ["gsap.min.js", "tweenmax"],
        "Three.js": ["three.min.js", "three.module"],
        "Chart.js": ["chart.min.js", "chart.umd"],
    },
    "Analytics": {
        "Google Analytics 4": ["gtag('config", "/gtag/js"],
        "Google Analytics UA": ["google-analytics.com/analytics.js", "ua-"],
        "Microsoft Clarity": ["clarity.ms", "ms.clarity"],
        "Amplitude": ["amplitude.com/libs", "amplitude.getinstance"],
        "Fullstory": ["fullstory.com/s/fs.js", "_fs_debug"],
        "Hotjar": ["hotjar.com", "hotjar.identify"],
        "Mixpanel": ["mixpanel.com", "mixpanel.track"],
        "Segment": ["cdn.segment.com"],
        "Plausible": ["plausible.io/js"],
        "Matomo": ["matomo.js", "/piwik.js"],
        "Albacross": ["albacross.com"],
        "Heap": ["cdn.heapanalytics.com"],
        "Posthog": ["posthog.com", "posthog.capture"],
        "Lucky Orange": ["luckyorange.com"],
        "Mouseflow": ["mouseflow.com"],
    },
    "Gestionnaire de balises": {
        "Google Tag Manager": ["googletagmanager.com/gtm.js", "gtm-"],
        "Adobe Launch": ["assets.adobedtm.com", "/launch-"],
        "Tealium": ["tealium.com", "utag.js"],
    },
    "Publicite": {
        "LinkedIn Insight": ["snap.licdn.com", "_linkedin_partner_id"],
        "Google Ads": ["googleadservices.com", "googleads.g.doubleclick"],
        "Facebook Pixel": ["connect.facebook.net/", "fbq('init"],
        "Microsoft/Bing Ads": ["bat.bing.com", "uetq.push"],
        "TikTok Pixel": ["analytics.tiktok.com"],
        "Tapfiliate": ["tapfiliate.com"],
        "Criteo": ["criteo.com", "criteo_q"],
        "Outbrain": ["widgets.outbrain.com"],
        "Taboola": ["cdn.taboola.com"],
    },
    "A/B Testing": {
        "AB Tasty": ["abtasty.com"],
        "Optimizely": ["optimizely.com"],
        "VWO": ["visualwebsiteoptimizer.com", "vwocode"],
        "BrowserUpdate": ["browser-update.org"],
    },
    "CMS & Ecommerce": {
        "WordPress": ["wp-content/", "wp-json"],
        "Shopify": ["cdn.shopify.com"],
        "Wix": ["static.wixstatic.com"],
        "Squarespace": ["assets.squarespace.com"],
        "Webflow": ["assets-global.website-files.com"],
        "Framer": ["framerusercontent.com"],
        "PrestaShop": ["prestashop"],
        "Ghost": ["ghost-theme"],
        "Contentful": ["contentful.com"],
        "Sanity": ["sanity.io"],
    },
    "CDN": {
        "Cloudflare": ["cdnjs.cloudflare.com", "cloudflareinsights.com"],
        "jsDelivr": ["cdn.jsdelivr.net"],
        "unpkg": ["unpkg.com"],
        "Vercel": ["_vercel/insights"],
        "Fastly": ["fastly.net"],
    },
    "Cookies & Consentement": {
        "OneTrust": ["onetrust", "optanonalert"],
        "Axeptio": ["axeptio"],
        "Tarteaucitron": ["tarteaucitron"],
        "Cookiebot": ["cookiebot", "cookieconsent"],
        "Didomi": ["didomi"],
        "CookieYes": ["cookieyes"],
    },
    "Chat & Support": {
        "Intercom": ["intercom.io", "intercomsettings"],
        "Crisp": ["client.crisp.chat", "crisp_website_id"],
        "Tidio": ["code.tidio.co"],
        "Tawk.to": ["tawk.to"],
        "Drift": ["drift.com/drift.js"],
        "Zendesk": ["zendesk.com", "zopim"],
        "Freshchat": ["freshchat.com"],
    },
    "CRM & Email Marketing": {
        "HubSpot": ["js.hs-scripts.com", "hbspt.forms"],
        "Brevo": ["sibautomation.com", "sendinblue.com"],
        "Mailchimp": ["chimpstatic.com"],
        "ActiveCampaign": ["trackcmp.net"],
        "Klaviyo": ["klaviyo.com"],
        "Marketo": ["munchkin.js"],
    },
    "Polices": {
        "Google Fonts": ["fonts.googleapis.com", "fonts.gstatic.com"],
        "Font Awesome": ["fontawesome.com", "font-awesome"],
        "Typekit": ["use.typekit.net"],
        "Google Material Icons": ["material-icons"],
    },
    "Securite": {
        "reCAPTCHA": ["google.com/recaptcha", "grecaptcha"],
        "hCaptcha": ["hcaptcha.com"],
        "Cloudflare Turnstile": ["challenges.cloudflare.com"],
        "Kount": ["kount.net"],
    },
    "Internationalisation": {
        "Weglot": ["weglot.com", "cdn.weglot.com"],
        "Crowdin": ["crowdin.com"],
    },
    "Interface & Graphiques": {
        "Open Graph": ["og:type", "og:title", "og:image"],
        "Twitter Card": ["twitter:card"],
        "Lottie": ["lottiefiles.com", "lottie.min.js"],
    },
}


# ---------------------------------------------------------------------------
# module-level helpers
# ---------------------------------------------------------------------------

def _c(id, label, status, value, points, max_points, severity, impact, fix):
    return {
        "id": id, "label": label, "status": status, "value": str(value),
        "points": points, "maxPoints": max_points, "severity": severity,
        "impact": impact, "fix": fix,
    }


def _grade(score):
    if score <= 48:
        return "CRITICAL", "Ton site présente des problèmes critiques qui freinent ton acquisition de clients."
    if score <= 78:
        return "FAIR", "Ton site a des bases correctes mais laisse passer des opportunités significatives."
    if score <= 102:
        return "GOOD", "Bon travail  -  quelques optimisations ciblées peuvent encore améliorer tes résultats."
    return "EXCELLENT", "Excellent. Ton site est bien optimisé."


def _norm_fr(text: str) -> str:
    text = text.lower()
    for chars, rep in [("éèêë", "e"), ("àâä", "a"), ("ùûü", "u"), ("îï", "i"), ("ôö", "o"), ("ç", "c")]:
        for ch in chars:
            text = text.replace(ch, rep)
    return text


def _parse_jsonld(soup: BeautifulSoup) -> list[dict]:
    schemas = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, list):
                schemas.extend(data)
            elif isinstance(data, dict):
                if "@graph" in data:
                    schemas.extend(i for i in data["@graph"] if isinstance(i, dict))
                else:
                    schemas.append(data)
        except Exception:
            pass
    return schemas


def _parse_robots_for_ai(text: str) -> dict[str, str]:
    groups: dict[str, list[str]] = {}
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            current = []
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key == "user-agent":
            current.append(val)
            groups.setdefault(val, [])
        elif key == "disallow" and current:
            for agent in current:
                groups.setdefault(agent, []).append(val)

    def _blocked(agent):
        disallows = groups.get(agent) if agent in groups else groups.get("*", [])
        return "/" in disallows

    return {c: ("blocked" if _blocked(c) else "allowed") for c in _AI_CRAWLERS}


def _detect_cms(html: str, headers: dict | None = None) -> str:
    h = html.lower()
    hdr = {k.lower(): v.lower() for k, v in (headers or {}).items()}
    gen = hdr.get("x-generator", "")
    if gen:
        return gen.split("/")[0].strip().title()
    if "wp-content/" in h or "wp-json" in h:
        return "WordPress"
    if "cdn.shopify.com" in h or "/cdn/shop/" in h:
        return "Shopify"
    if "static.wixstatic.com" in h:
        return "Wix"
    if "assets.squarespace.com" in h:
        return "Squarespace"
    if 'data-wf-' in h or "assets-global.website-files.com" in h:
        return "Webflow"
    if "framerusercontent.com" in h:
        return "Framer"
    if "__next_data__" in h or "_next/static" in h:
        return "Next.js"
    if "__nuxt" in h or "/_nuxt/" in h:
        return "Nuxt.js"
    if "prestashop" in h:
        return "PrestaShop"
    if "/components/com_" in h and "joomla" in h:
        return "Joomla"
    if "drupal.settings" in h or ("drupal" in h and "/sites/default/" in h):
        return "Drupal"
    if "ghost-theme" in h:
        return "Ghost"
    return "Inconnu"


def _detect_tech_stack(html: str, headers: dict | None = None) -> dict[str, list[str]]:
    h = html.lower()
    hdr = {k.lower(): v.lower() for k, v in (headers or {}).items()}
    result: dict[str, list[str]] = {}

    server_techs = []
    server = hdr.get("server", "")
    powered = hdr.get("x-powered-by", "")
    if "nginx" in server:
        server_techs.append("Nginx")
    if "apache" in server:
        server_techs.append("Apache")
    if "cloudflare" in server:
        server_techs.append("Cloudflare")
    if "vercel" in server:
        server_techs.append("Vercel")
    if "php" in powered:
        php_v = powered.replace("php/", "PHP ").split(" ")[0].strip().title()
        server_techs.append(php_v)
    elif "node" in powered or "express" in powered:
        server_techs.append("Node.js")
    if server_techs:
        result["Serveur"] = server_techs

    for category, techs in _TECH_SIGNATURES.items():
        found = [tech for tech, patterns in techs.items() if any(p.lower() in h for p in patterns)]
        if found:
            result[category] = found

    # Extract actual Google Fonts family names from URL params
    if "Polices" in result and "Google Fonts" in result["Polices"]:
        font_names: list[str] = []
        for url_m in re.finditer(r'fonts\.googleapis\.com/css[^\s"\'<>]*', html, re.IGNORECASE):
            for fam_m in re.finditer(r'[Ff]amily=([^&"\'<>\s]+)', url_m.group(0)):
                for raw in fam_m.group(1).split("|"):
                    clean = raw.split(":")[0].split("@")[0].replace("+", " ").strip()
                    if clean and clean not in font_names:
                        font_names.append(clean)
        if font_names:
            result["Polices"] = [t for t in result["Polices"] if t != "Google Fonts"] + font_names

    return result


_BUDGET_SIGNALS: dict[str, int] = {
    "Google Ads": 3,
    "LinkedIn Insight": 2,
    "Facebook Pixel": 2,
    "Microsoft/Bing Ads": 1,
    "TikTok Pixel": 2,
    "Criteo": 2,
    "Outbrain": 1,
    "Taboola": 1,
    "AB Tasty": 3,
    "Optimizely": 3,
    "VWO": 2,
    "HubSpot": 2,
    "Marketo": 3,
    "ActiveCampaign": 1,
    "Amplitude": 2,
    "Fullstory": 2,
    "Heap": 2,
}


def _detect_social_presence(soup: BeautifulSoup) -> dict:
    found: dict[str, str | None] = {k: None for k in _SOCIAL_PATTERNS}
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        for platform, patterns in _SOCIAL_PATTERNS.items():
            if found[platform] is None and any(p in href for p in patterns):
                found[platform] = href
    score = sum(1 for v in found.values() if v is not None)
    return {**found, "score": score, "total": len(_SOCIAL_PATTERNS)}


def _extract_target_keyword(soup: BeautifulSoup, domain: str) -> str:
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    h1_tag = soup.find("h1")
    h1 = h1_tag.get_text(strip=True) if h1_tag else ""
    brand = domain.split(".")[0].lower().replace("-", "").replace("_", "")
    for sep in [" | ", " - ", " :: ", " : "]:
        parts = [p.strip() for p in title.split(sep) if p.strip()]
        if len(parts) > 1:
            for part in parts:
                if brand not in part.lower().replace(" ", ""):
                    return part[:80]
    if h1 and brand not in h1.lower().replace(" ", ""):
        return h1[:80]
    return (title or h1)[:80]


def _detect_messaging(soup: BeautifulSoup) -> dict:
    h1 = soup.find("h1")
    h1_text = h1.get_text(strip=True) if h1 else ""
    md_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = md_tag.get("content", "").strip() if md_tag else ""
    html_tag = soup.find("html")
    lang = html_tag.get("lang", "").strip() if html_tag else ""
    return {"h1": h1_text, "metaDescription": meta_desc, "lang": lang}


def _detect_freshness(soup: BeautifulSoup) -> dict:
    schemas = _parse_jsonld(soup)
    last_modified = None
    for s in schemas:
        dm = s.get("dateModified") or s.get("datePublished")
        if dm:
            last_modified = str(dm)
            break

    footer = soup.find("footer")
    footer_text = footer.get_text(" ") if footer else soup.get_text(" ")
    cp_match = re.search(r'(?:©|Copyright)[^\d]*(\d{4})', footer_text, re.IGNORECASE)
    copyright_year = int(cp_match.group(1)) if cp_match else None

    blog_patterns = ["/blog", "/actualites", "/news", "/articles", "/ressources", "/insights"]
    links = [a.get("href", "").lower() for a in soup.find_all("a", href=True)]
    has_blog = any(any(pat in lnk for pat in blog_patterns) for lnk in links)
    article_count = sum(1 for lnk in links if any(pat in lnk for pat in ["/blog/", "/article/", "/actualite/"]))

    return {
        "lastModified": last_modified,
        "copyrightYear": copyright_year,
        "hasBlog": has_blog,
        "recentArticleCount": article_count,
    }


def _detect_acquisition_profile(tech_stack: dict, has_sitemap: bool = False) -> dict:
    channels: list[str] = []

    analytics = tech_stack.get("Analytics", [])
    if analytics or has_sitemap:
        channels.append("SEO")

    paid = tech_stack.get("Publicite", [])
    if any(t in paid for t in ["Google Ads", "Facebook Pixel", "Microsoft/Bing Ads", "TikTok Pixel", "LinkedIn Insight"]):
        channels.append("Paid")

    if any(t in paid for t in ["Facebook Pixel", "TikTok Pixel", "LinkedIn Insight"]):
        channels.append("Social")

    crm = tech_stack.get("CRM & Email Marketing", [])
    if crm:
        channels.append("Email")

    if any(t in paid for t in ["Criteo", "Outbrain", "Taboola"]):
        channels.append("Retargeting")

    ab = tech_stack.get("A/B Testing", [])
    if ab:
        channels.append("CRO")

    count = len(channels)
    level = "omni-canal" if count > 3 else ("multi-canal" if count > 1 else "basique")
    return {"channels": channels, "channelCount": count, "level": level}


def _detect_marketing_budget(tech_stack: dict) -> dict:
    all_tools = [t for ts in tech_stack.values() for t in ts]
    raw_score = 0
    signals: list[str] = []
    for tool, pts in _BUDGET_SIGNALS.items():
        if tool in all_tools:
            raw_score += pts
            signals.append(tool)

    if raw_score == 0:
        level = "minimal"
    elif raw_score <= 3:
        level = "faible"
    elif raw_score <= 7:
        level = "modéré"
    elif raw_score <= 12:
        level = "élevé"
    else:
        level = "très élevé"

    return {"level": level, "score": min(round(raw_score / 1.5), 10), "signals": signals}


def _detect_internationalisation(soup: BeautifulSoup, tech_stack: dict) -> dict:
    hreflang_tags = [
        tag for tag in soup.find_all("link")
        if "alternate" in (tag.get("rel") or []) and tag.get("hreflang")
    ]
    langs = list({tag["hreflang"].lower() for tag in hreflang_tags})
    i18n_tools = tech_stack.get("Internationalisation", [])
    return {
        "hreflang": langs,
        "i18nTools": i18n_tools,
        "isMultilingual": len(langs) > 1 or bool(i18n_tools),
    }


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

class SiteAuditService:
    def __init__(self):
        self.psi = PageSpeedService()
        try:
            self._supa = SupabaseService()
        except RuntimeError:
            self._supa = None

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _fetch_html(self, url: str) -> tuple[str, BeautifulSoup, dict]:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            html = resp.text
            return html, BeautifulSoup(html, "html.parser"), dict(resp.headers)

    @staticmethod
    def _playwright_fetch_sync(url: str, screenshots_dir: str | None = None) -> tuple[str, dict, dict]:
        import sys
        import asyncio as _asyncio
        if sys.platform == "win32":
            _asyncio.set_event_loop_policy(_asyncio.WindowsProactorEventLoopPolicy())
        from playwright.sync_api import sync_playwright
        screenshots: dict[str, str] = {}
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                # Desktop context - fetch HTML + optional screenshot
                dctx = browser.new_context(viewport={"width": 1280, "height": 800})
                dpage = dctx.new_page()
                resp = dpage.goto(url, wait_until="load", timeout=20000)
                dpage.wait_for_timeout(1500)
                html = dpage.content()
                hdrs = dict(resp.headers) if resp else {}
                if screenshots_dir:
                    os.makedirs(screenshots_dir, exist_ok=True)
                    slug = re.sub(r"[^\w.-]", "_", url.replace("https://", "").replace("http://", "").split("/")[0])[:30]
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    d_path = os.path.join(screenshots_dir, f"desktop_{slug}_{ts}.png")
                    dpage.screenshot(path=d_path, clip={"x": 0, "y": 0, "width": 1280, "height": 800})
                    screenshots["desktop"] = d_path
                dpage.close()
                dctx.close()
                # Mobile context - screenshot only
                if screenshots_dir:
                    mctx = browser.new_context(
                        viewport={"width": 390, "height": 844},
                        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
                    )
                    mpage = mctx.new_page()
                    mpage.goto(url, wait_until="load", timeout=20000)
                    mpage.wait_for_timeout(1000)
                    m_path = os.path.join(screenshots_dir, f"mobile_{slug}_{ts}.png")
                    mpage.screenshot(path=m_path, clip={"x": 0, "y": 0, "width": 390, "height": 844})
                    screenshots["mobile"] = m_path
                    mpage.close()
                    mctx.close()
                return html, hdrs, screenshots
            finally:
                browser.close()

    async def _fetch_html_rendered(self, url: str, screenshots_dir: str | None = None) -> tuple[str, BeautifulSoup, dict, dict]:
        try:
            loop = asyncio.get_event_loop()
            html, hdrs, screenshots = await loop.run_in_executor(
                None, self._playwright_fetch_sync, url, screenshots_dir
            )
            if screenshots and self._supa:
                public_urls: dict[str, str] = {}
                for device, local_path in screenshots.items():
                    try:
                        with open(local_path, "rb") as f:
                            png_bytes = f.read()
                        filename = os.path.basename(local_path)
                        public_url = self._supa.upload_screenshot(filename, png_bytes)
                        public_urls[device] = public_url
                    except Exception:
                        public_urls[device] = local_path
                screenshots = public_urls
            return html, BeautifulSoup(html, "html.parser"), hdrs, screenshots
        except Exception:
            html, soup, hdrs = await self._fetch_html(url)
            return html, soup, hdrs, {}

    async def _fetch_with_text(self, url: str) -> tuple[int, str]:
        try:
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url, headers=_HEADERS)
                return resp.status_code, resp.text
        except Exception:
            return 0, ""

    async def _fetch_status(self, url: str) -> int:
        try:
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url, headers=_HEADERS)
                return resp.status_code
        except Exception:
            return 0

    async def _check_redirect(self, domain: str) -> tuple[bool, int]:
        try:
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=False) as client:
                resp = await client.get(f"http://{domain}", headers=_HEADERS)
                loc = resp.headers.get("location", "")
                return loc.startswith("https://"), resp.status_code
        except Exception:
            return False, 0

    def _extract_internal_links(self, soup: BeautifulSoup, domain: str) -> list[str]:
        seen: set[str] = set()
        result = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            if href.startswith("/") and not href.startswith("//"):
                link = f"https://{domain}{href}"
            elif domain in href and href.startswith("http"):
                link = href
            else:
                continue
            if link not in seen:
                seen.add(link)
                result.append(link)
        return result

    def _extract_nav_links(self, soup: BeautifulSoup, domain: str, root: str) -> list[str]:
        seen: set[str] = set()
        result = []
        containers = soup.find_all(["nav", "header"]) or [soup]
        for container in containers:
            for a in container.find_all("a", href=True):
                href = a["href"].strip().split("?")[0].split("#")[0]
                if not href or href in ("", "/"):
                    continue
                if href.startswith("/") and not href.startswith("//"):
                    link = f"https://{domain}{href}"
                elif domain in href and href.startswith("http"):
                    link = href.rstrip("/")
                else:
                    continue
                if link.rstrip("/") == root.rstrip("/"):
                    continue
                if link not in seen:
                    seen.add(link)
                    result.append(link)
        return result[:8]

    async def _quick_page_audit(self, url: str) -> dict:
        try:
            html, soup, _, __ = await self._fetch_html_rendered(url)
        except Exception as exc:
            return {"url": url, "error": str(exc), "score": 0, "maxScore": 15, "checks": []}

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        tl = len(title)
        checks = []

        if not title:
            checks.append(_c("title", "Balise <title>", "fail", "-", 0, 3, "critical",
                "Aucune balise title.", "Ajouter une balise title unique."))
        elif 50 <= tl <= 60:
            checks.append(_c("title", "Balise <title>", "pass", title[:60], 3, 3, "critical", "", ""))
        else:
            checks.append(_c("title", "Balise <title>", "warning", f"{tl} car.", 1, 3, "medium",
                f"Title {'court' if tl < 50 else 'long'} ({tl} car.).", "Viser 50-60 caractères."))

        md_tag = soup.find("meta", attrs={"name": "description"})
        md = md_tag.get("content", "").strip() if md_tag else ""
        checks.append(_c("meta_desc", "Meta description", "pass" if md else "fail",
            md[:80] if md else "-", 2 if md else 0, 2, "high",
            "Meta description présente." if md else "Meta description absente.",
            "" if md else "Ajouter une meta description de 150-160 car."))

        h1_tags = soup.find_all("h1")
        h1_count = len(h1_tags)
        if h1_count == 1:
            checks.append(_c("h1", "H1 unique", "pass", h1_tags[0].get_text(strip=True)[:60], 4, 4, "high", "", ""))
        elif h1_count == 0:
            checks.append(_c("h1", "H1 unique", "fail", "Aucun H1", 0, 4, "high",
                "Aucun H1 sur cette page.", "Ajouter un H1 unique."))
        else:
            checks.append(_c("h1", "H1 unique", "warning", f"{h1_count} H1", 0, 4, "high",
                f"{h1_count} H1 trouvés.", "Conserver un seul H1."))

        schema_ok = bool(soup.find_all("script", type="application/ld+json"))
        checks.append(_c("schema", "JSON-LD", "pass" if schema_ok else "warning",
            "Présent" if schema_ok else "Absent", 3 if schema_ok else 0, 3, "medium",
            "Données structurées présentes." if schema_ok else "Aucune donnée structurée.",
            "" if schema_ok else "Ajouter des données structurées JSON-LD."))

        canon = soup.find("link", rel="canonical")
        canon_ok = canon is not None
        checks.append(_c("canonical", "Canonical", "pass" if canon_ok else "warning",
            canon.get("href", "-")[:60] if canon_ok else "-", 2 if canon_ok else 0, 2, "medium",
            "Balise canonical présente." if canon_ok else "Aucune balise canonical.",
            "" if canon_ok else "Ajouter une balise canonical."))

        html_tag = soup.find("html")
        lang_ok = bool(html_tag and html_tag.get("lang", "").strip()) if html_tag else False
        checks.append(_c("lang", "Attribut lang", "pass" if lang_ok else "warning",
            html_tag.get("lang", "-") if html_tag else "-", 1 if lang_ok else 0, 1, "low",
            "Langue déclarée.", "" if lang_ok else 'Ajouter lang="fr" sur <html>.'))

        score = sum(c["points"] for c in checks)
        max_score = sum(c["maxPoints"] for c in checks)
        return {"url": url, "title": title, "score": score, "maxScore": max_score, "checks": checks}

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    async def run(self, url: str, utm_source=None, utm_campaign=None, max_pages: int = 1) -> dict:
        return await self._run_core(url, utm_source, utm_campaign, max_pages)

    async def _run_core(
        self, url: str, utm_source=None, utm_campaign=None, max_pages: int = 1
    ) -> dict:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        root = f"https://{domain}"
        analyzed_at = datetime.now(timezone.utc).isoformat()

        (
            html_res, sitemap_res, robots_res, llms_res,
            redirect_res, mob_res, desk_res,
        ) = await asyncio.gather(
            self._fetch_html_rendered(url, _OUTPUT_SCREENSHOTS_DIR),
            self._fetch_with_text(root + "/sitemap.xml"),
            self._fetch_with_text(root + "/robots.txt"),
            self._fetch_status(root + "/llms.txt"),
            self._check_redirect(domain),
            self.psi.analyse_full_async(url, "mobile"),
            self.psi.analyse_full_async(url, "desktop"),
            return_exceptions=True,
        )

        if isinstance(html_res, Exception):
            raise RuntimeError(f"Homepage inaccessible : {html_res}")

        html, soup, resp_headers, screenshots = html_res
        sitemap_ok = isinstance(sitemap_res, tuple) and sitemap_res[0] == 200
        robots_ok = isinstance(robots_res, tuple) and robots_res[0] == 200
        robots_text = robots_res[1] if robots_ok and isinstance(robots_res, tuple) else ""
        llms_ok = isinstance(llms_res, int) and llms_res == 200
        redirect_ok, redirect_code = redirect_res if isinstance(redirect_res, tuple) else (False, 0)
        mob = mob_res if isinstance(mob_res, dict) else {}
        desk = desk_res if isinstance(desk_res, dict) else {}

        internal_links = self._extract_internal_links(soup, domain)[:10]
        link_statuses = await asyncio.gather(
            *[self._fetch_status(lnk) for lnk in internal_links],
            return_exceptions=True,
        )
        broken_count = sum(1 for s in link_statuses if isinstance(s, int) and s == 404)

        perf = self._block_perf(mob, desk)
        seo = self._block_seo(soup, url, domain, sitemap_ok, robots_ok, redirect_ok, redirect_code, internal_links, broken_count)
        legal = self._block_legal(soup)
        conv = self._block_conv(soup, html)
        mobile = self._block_mobile(soup, mob)
        geo = self._block_geo(soup, robots_text, llms_ok)
        security = self._block_security(resp_headers)
        cms = _detect_cms(html, resp_headers)
        tech_stack = _detect_tech_stack(html, resp_headers)
        local_seo = self._block_local_seo(soup)
        ecommerce = self._block_ecommerce(soup, tech_stack)
        messaging = _detect_messaging(soup)
        freshness = _detect_freshness(soup)
        acquisition_profile = _detect_acquisition_profile(tech_stack, has_sitemap=sitemap_ok)
        marketing_budget = _detect_marketing_budget(tech_stack)
        internationalisation = _detect_internationalisation(soup, tech_stack)
        social_presence = _detect_social_presence(soup)
        target_keyword = _extract_target_keyword(soup, domain)

        actual_max = min(max_pages, _MAX_NAV_PAGES)
        pages_results: list[dict] = []
        if actual_max > 1:
            nav_links = self._extract_nav_links(soup, domain, root)
            nav_links = nav_links[:actual_max - 1]
            if nav_links:
                page_audits = await asyncio.gather(
                    *[self._quick_page_audit(lnk) for lnk in nav_links],
                    return_exceptions=True,
                )
                for pa in page_audits:
                    if isinstance(pa, Exception):
                        pages_results.append({"error": str(pa)})
                    else:
                        pages_results.append(pa)

        global_score = min(
            perf["score"] + seo["score"] + legal["score"] + conv["score"] + mobile["score"] + geo["score"],
            120,
        )
        grade, summary = _grade(global_score)

        scored_blocks = {"performance": perf, "seo": seo, "legal": legal, "conversion": conv, "mobile": mobile}
        failed = [
            {**ch, "_blk": blk}
            for blk, bd in scored_blocks.items()
            for ch in bd["checks"]
            if ch["status"] == "fail"
        ]
        failed.sort(key=lambda c: _SEVERITY_RANK.get(c["severity"], 99))
        top_issues = [
            {"block": c["_blk"], **{k: v for k, v in c.items() if k != "_blk"}}
            for c in failed[:3]
        ]

        if self._supa:
            try:
                self._supa.log_audit({
                    "url": url,
                    "domain": domain,
                    "global_score": global_score,
                    "perf_score": perf["score"],
                    "seo_score": seo["score"],
                    "legal_score": legal["score"],
                    "conversion_score": conv["score"],
                    "mobile_score": mobile["score"],
                    "geo_score": geo.get("geoScore", 0),
                    "email": None,
                    "analyzed_at": analyzed_at,
                    "utm_source": utm_source,
                    "utm_campaign": utm_campaign,
                })
            except Exception:
                pass

        report = {
            "url": url,
            "domain": domain,
            "analyzedAt": analyzed_at,
            "globalScore": global_score,
            "grade": grade,
            "summary": summary,
            "cms": cms,
            "techStack": tech_stack,
            "messaging": messaging,
            "freshness": freshness,
            "acquisitionProfile": acquisition_profile,
            "marketingBudget": marketing_budget,
            "internationalisation": internationalisation,
            "socialPresence": social_presence,
            "targetKeyword": target_keyword,
            "screenshots": screenshots,
            "blocks": {**scored_blocks, "geo": geo, "security": security, "localSeo": local_seo, "ecommerce": ecommerce},
            "topIssues": top_issues,
        }
        if pages_results:
            report["pages"] = pages_results
        return report

    # ------------------------------------------------------------------
    # BLOCK 0  -  SECURITE HEADERS (score /100, hors globalScore)
    # ------------------------------------------------------------------

    def _block_security(self, resp_headers: dict) -> dict:
        h = {k.lower(): v for k, v in resp_headers.items()}
        checks = []

        hsts = "strict-transport-security" in h
        checks.append(_c("hsts", "Strict-Transport-Security (HSTS)",
            "pass" if hsts else "fail",
            h.get("strict-transport-security", "-"),
            4 if hsts else 0, 4, "high",
            "HSTS actif - le navigateur force HTTPS pour toutes les visites futures." if hsts
                else "HSTS absent - un attaquant peut intercepter la premiere connexion HTTP.",
            "Maintenir HSTS." if hsts else "Ajouter l'en-tete Strict-Transport-Security: max-age=31536000; includeSubDomains"))

        xcto = h.get("x-content-type-options", "").strip().lower() == "nosniff"
        checks.append(_c("x_content_type", "X-Content-Type-Options: nosniff",
            "pass" if xcto else "fail",
            h.get("x-content-type-options", "-"),
            3 if xcto else 0, 3, "medium",
            "MIME sniffing desactive - protege contre les attaques de type sniffing." if xcto
                else "X-Content-Type-Options absent - les navigateurs peuvent interpreter des fichiers de maniere incorrecte.",
            "Maintenir X-Content-Type-Options: nosniff." if xcto
                else "Ajouter l'en-tete X-Content-Type-Options: nosniff"))

        xfo = h.get("x-frame-options", "").strip().lower()
        xfo_ok = xfo in ("deny", "sameorigin")
        checks.append(_c("x_frame_options", "X-Frame-Options (clickjacking)",
            "pass" if xfo_ok else ("warning" if xfo else "fail"),
            h.get("x-frame-options", "-"),
            3 if xfo_ok else 0, 3, "medium",
            f"Clickjacking protege ({h.get('x-frame-options', '')})." if xfo_ok
                else "X-Frame-Options absent - le site peut etre integre dans une iframe malveillante.",
            "Maintenir X-Frame-Options." if xfo_ok
                else "Ajouter X-Frame-Options: SAMEORIGIN"))

        csp = "content-security-policy" in h
        checks.append(_c("csp", "Content-Security-Policy (CSP)",
            "pass" if csp else "warning",
            (h.get("content-security-policy") or "-")[:60],
            4 if csp else 0, 4, "high",
            "CSP presente - protege contre les attaques XSS." if csp
                else "CSP absente - les scripts injectes peuvent s'executer librement.",
            "Maintenir et renforcer la CSP." if csp
                else "Ajouter une Content-Security-Policy adaptee a votre stack."))

        rp = "referrer-policy" in h
        checks.append(_c("referrer_policy", "Referrer-Policy",
            "pass" if rp else "warning",
            h.get("referrer-policy", "-"),
            2 if rp else 0, 2, "low",
            "Referrer-Policy presente - controle les donnees envoyees aux sites tiers." if rp
                else "Referrer-Policy absente - les URLs de navigation sont transmises aux sites externes.",
            "Maintenir Referrer-Policy." if rp
                else "Ajouter Referrer-Policy: strict-origin-when-cross-origin"))

        security_max = sum(c["maxPoints"] for c in checks)
        security_earned = sum(c["points"] for c in checks)
        security_score = round(security_earned / security_max * 100) if security_max else 0
        return {"score": 0, "maxScore": 0, "securityScore": security_score, "checks": checks}

    # ------------------------------------------------------------------
    # BLOCK 1  -  PERFORMANCE (25 pts)
    # ------------------------------------------------------------------

    def _block_perf(self, mob: dict, desk: dict) -> dict:
        mob_lhr = mob.get("lighthouseResult", {})
        desk_lhr = desk.get("lighthouseResult", {})
        mob_err = not mob_lhr
        desk_err = not desk_lhr
        mob_audits = mob_lhr.get("audits", {})
        checks = []

        # score_mobile  -  8 pts
        raw = mob_lhr.get("categories", {}).get("performance", {}).get("score") if not mob_err else None
        mob_s = int(raw * 100) if raw is not None else None
        if mob_s is None:
            checks.append(_c("score_mobile", "Score performance mobile", "unavailable", " - ", 0, 8, "critical",
                "Impossible de récupérer le score mobile depuis Google PageSpeed.",
                "Vérifier que la page est accessible publiquement."))
        elif mob_s >= 90:
            checks.append(_c("score_mobile", "Score performance mobile", "pass", f"{mob_s}/100", 8, 8, "critical",
                "Excellent score mobile  -  tes visiteurs voient ta page rapidement.",
                "Maintenir les bonnes pratiques de performance."))
        elif mob_s >= 70:
            checks.append(_c("score_mobile", "Score performance mobile", "warning", f"{mob_s}/100", 5, 8, "critical",
                f"Score mobile de {mob_s}/100  -  des améliorations sont possibles pour retenir plus de visiteurs.",
                "Optimiser les images et réduire les ressources bloquantes."))
        elif mob_s >= 50:
            checks.append(_c("score_mobile", "Score performance mobile", "warning", f"{mob_s}/100", 3, 8, "critical",
                f"Score mobile de {mob_s}/100  -  insuffisant pour offrir une bonne expérience utilisateur.",
                "Compresser les images, activer le lazy loading, réduire le CSS critique."))
        else:
            checks.append(_c("score_mobile", "Score performance mobile", "fail", f"{mob_s}/100", 0, 8, "critical",
                f"Score mobile de {mob_s}/100. Au-delà de 3s de chargement, 53 % des visiteurs quittent la page.",
                "Audit Lighthouse complet nécessaire : images, JavaScript et CSS bloquants."))

        # score_desktop  -  4 pts
        raw = desk_lhr.get("categories", {}).get("performance", {}).get("score") if not desk_err else None
        desk_s = int(raw * 100) if raw is not None else None
        if desk_s is None:
            checks.append(_c("score_desktop", "Score performance desktop", "unavailable", " - ", 0, 4, "high",
                "Impossible de récupérer le score desktop depuis Google PageSpeed.",
                "Vérifier que la page est accessible publiquement."))
        elif desk_s >= 90:
            checks.append(_c("score_desktop", "Score performance desktop", "pass", f"{desk_s}/100", 4, 4, "high",
                "Excellent score desktop  -  ta page se charge rapidement sur ordinateur.",
                "Maintenir les bonnes pratiques."))
        elif desk_s >= 70:
            checks.append(_c("score_desktop", "Score performance desktop", "warning", f"{desk_s}/100", 2, 4, "high",
                f"Score desktop de {desk_s}/100  -  les utilisateurs desktop peuvent ressentir des lenteurs.",
                "Optimiser les scripts et les ressources tierces."))
        else:
            checks.append(_c("score_desktop", "Score performance desktop", "fail", f"{desk_s}/100", 0, 4, "high",
                f"Score desktop de {desk_s}/100  -  performance insuffisante même sur ordinateur.",
                "Réduire la taille des pages et optimiser les requêtes HTTP."))

        # LCP  -  5 pts
        lcp_ms = mob_audits.get("largest-contentful-paint", {}).get("numericValue") if not mob_err else None
        if lcp_ms is None:
            checks.append(_c("lcp", "Largest Contentful Paint (LCP)", "unavailable", " - ", 0, 5, "critical",
                "LCP non disponible  -  donnée PageSpeed manquante.",
                "Vérifier l'accessibilité de la page."))
        elif lcp_ms < 2500:
            checks.append(_c("lcp", "Largest Contentful Paint (LCP)", "pass", f"{lcp_ms/1000:.2f}s", 5, 5, "critical",
                "Excellent  -  le contenu principal s'affiche en moins de 2.5s.",
                "Continuer à surveiller lors de chaque mise à jour."))
        elif lcp_ms < 4000:
            checks.append(_c("lcp", "Largest Contentful Paint (LCP)", "warning", f"{lcp_ms/1000:.2f}s", 3, 5, "critical",
                f"LCP de {lcp_ms/1000:.1f}s  -  Google recommande < 2.5s pour un bon classement.",
                "Optimiser l'image ou l'élément principal de la page."))
        else:
            checks.append(_c("lcp", "Largest Contentful Paint (LCP)", "fail", f"{lcp_ms/1000:.2f}s", 0, 5, "critical",
                f"LCP de {lcp_ms/1000:.1f}s  -  trop lent. Google pénalise les sites lents dans ses résultats de recherche.",
                "Compression d'images, CDN et optimisation du chemin de rendu critique."))

        # CLS  -  4 pts
        cls_v = mob_audits.get("cumulative-layout-shift", {}).get("numericValue") if not mob_err else None
        if cls_v is None:
            checks.append(_c("cls", "Cumulative Layout Shift (CLS)", "unavailable", " - ", 0, 4, "high",
                "CLS non disponible.", "Vérifier l'accessibilité de la page."))
        elif cls_v < 0.1:
            checks.append(_c("cls", "Cumulative Layout Shift (CLS)", "pass", str(round(cls_v, 3)), 4, 4, "high",
                "Excellent  -  tes pages ne bougent pas pendant le chargement, bonne expérience utilisateur.",
                "Continuer à attribuer des dimensions explicites aux images."))
        elif cls_v < 0.25:
            checks.append(_c("cls", "Cumulative Layout Shift (CLS)", "warning", str(round(cls_v, 3)), 2, 4, "high",
                f"CLS de {cls_v:.3f}  -  le contenu bouge pendant le chargement, ce qui frustre les visiteurs.",
                "Définir des dimensions explicites (width/height) sur les images et publicités."))
        else:
            checks.append(_c("cls", "Cumulative Layout Shift (CLS)", "fail", str(round(cls_v, 3)), 0, 4, "high",
                f"CLS de {cls_v:.3f}  -  mouvement excessif du contenu. Les visiteurs cliquent au mauvais endroit.",
                "Réserver l'espace pour les images, polices et bannières publicitaires."))

        # INP  -  4 pts
        inp_ms = mob_audits.get("interaction-to-next-paint", {}).get("numericValue") if not mob_err else None
        if inp_ms is None:
            checks.append(_c("inp", "Interaction to Next Paint (INP)", "unavailable", " - ", 0, 4, "high",
                "INP non disponible.", "Vérifier l'accessibilité de la page."))
        elif inp_ms < 200:
            checks.append(_c("inp", "Interaction to Next Paint (INP)", "pass", f"{inp_ms:.0f}ms", 4, 4, "high",
                "Excellent  -  le site réagit rapidement aux actions des utilisateurs.",
                "Maintenir les bonnes pratiques."))
        elif inp_ms < 500:
            checks.append(_c("inp", "Interaction to Next Paint (INP)", "warning", f"{inp_ms:.0f}ms", 2, 4, "high",
                f"INP de {inp_ms:.0f}ms  -  le site répond lentement aux clics et interactions.",
                "Réduire le JavaScript bloquant et optimiser les gestionnaires d'événements."))
        else:
            checks.append(_c("inp", "Interaction to Next Paint (INP)", "fail", f"{inp_ms:.0f}ms", 0, 4, "high",
                f"INP de {inp_ms:.0f}ms  -  interactions très lentes, l'utilisateur a l'impression que le site est bloqué.",
                "Fractionner les tâches JavaScript longues, utiliser des web workers."))

        # Informative checks (0 pts each)
        for audit_key, cid, label, pass_imp, fail_imp, fix in [
            ("prioritize-lcp-image", "fetchpriority_hero", "fetchpriority sur l'image principale (LCP)",
             "L'image principale est priorisée correctement lors du chargement.",
             "L'image principale n'est pas priorisée  -  elle est chargée avec un retard inutile.",
             "Ajouter fetchpriority=\"high\" sur la balise <img> du hero."),
            ("font-display", "font_display", "Attribut font-display sur les polices",
             "Les polices sont configurées pour s'afficher sans blocage.",
             "Les polices bloquent le rendu  -  les visiteurs voient du texte invisible pendant le chargement.",
             "Ajouter font-display: swap dans vos déclarations @font-face."),
            ("uses-rel-preconnect", "preconnect", "Préconnexion aux domaines tiers",
             "Les connexions aux ressources tierces sont anticipées.",
             "Absence de préconnexion aux ressources tierces  -  ralentit le chargement.",
             'Ajouter <link rel="preconnect" href="..."> pour les domaines tiers critiques.'),
        ]:
            audit = mob_audits.get(audit_key, {}) if not mob_err else {}
            passed = audit.get("score") == 1
            checks.append(_c(cid, label,
                "pass" if passed else "warning",
                audit.get("displayValue") or ("OK" if passed else "Non optimisé"),
                0, 0, "low",
                pass_imp if passed else fail_imp,
                "Maintenir la configuration actuelle." if passed else fix))

        score = min(sum(c["points"] for c in checks), 25)
        return {"score": score, "maxScore": 25, "checks": checks}

    # ------------------------------------------------------------------
    # BLOCK 2  -  SEO TECHNIQUE (25 pts)
    # ------------------------------------------------------------------

    def _block_seo(self, soup, url, domain, sitemap_ok, robots_ok,
                   redirect_ok, redirect_code, internal_links, broken_count):
        checks = []

        # title
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        if title:
            checks.append(_c("title_present", "Balise <title> présente", "pass",
                title[:80], 2, 2, "critical",
                "La balise title est présente  -  bon signal pour les moteurs de recherche.",
                "Maintenir une balise title pertinente et unique sur chaque page."))
        else:
            checks.append(_c("title_present", "Balise <title> présente", "fail",
                " - ", 0, 2, "critical",
                "Aucune balise title  -  les moteurs de recherche ne savent pas comment intituler ta page.",
                "Ajouter une balise <title> unique et descriptive dans le <head>."))

        if title:
            tl = len(title)
            if 50 <= tl <= 60:
                checks.append(_c("title_length", "Longueur balise title (50-60 car.)", "pass", f"{tl} caractères", 2, 2, "medium",
                    "Longueur idéale  -  le title s'affichera complet dans Google.",
                    "Maintenir cette longueur pour les futures pages."))
            else:
                msg = f"Title trop {'court' if tl < 50 else 'long'} ({tl} car.)  -  " + (
                    "tu n'exploites pas tout l'espace disponible dans Google." if tl < 50
                    else "il sera tronqué dans les résultats Google.")
                fix = ("Enrichir le title pour atteindre 50-60 caractères." if tl < 50
                       else "Raccourcir le title à 50-60 caractères.")
                checks.append(_c("title_length", "Longueur balise title (50-60 car.)", "warning", f"{tl} caractères", 0, 2, "medium", msg, fix))
        else:
            checks.append(_c("title_length", "Longueur balise title (50-60 car.)", "fail", " - ", 0, 2, "medium",
                "Aucun title à évaluer.", "Ajouter d'abord une balise title."))

        # meta description
        md_tag = soup.find("meta", attrs={"name": "description"})
        md = md_tag.get("content", "").strip() if md_tag else ""

        if md:
            snippet = md[:100] + ("…" if len(md) > 100 else "")
            checks.append(_c("meta_desc_present", "Meta description présente", "pass", snippet, 2, 2, "high",
                "La meta description est présente  -  elle s'affiche dans les résultats Google.",
                "Maintenir une meta description pertinente sur chaque page."))
        else:
            checks.append(_c("meta_desc_present", "Meta description présente", "fail", " - ", 0, 2, "high",
                "Aucune meta description  -  Google en génère une automatiquement, souvent mal choisie.",
                "Ajouter une meta description de 150-160 caractères sur chaque page."))

        if md:
            ml = len(md)
            if 150 <= ml <= 160:
                checks.append(_c("meta_desc_length", "Longueur meta description (150-160 car.)", "pass", f"{ml} caractères", 2, 2, "medium",
                    "Longueur idéale  -  la meta description s'affichera complètement dans Google.",
                    "Maintenir cette longueur."))
            else:
                msg = (f"Meta description trop {'courte' if ml < 150 else 'longue'} ({ml} car.)  -  " +
                    ("tu n'utilises pas tout l'espace disponible." if ml < 150
                     else "elle sera tronquée dans Google."))
                fix = ("Enrichir la meta description pour atteindre 150-160 caractères." if ml < 150
                       else "Raccourcir à 150-160 caractères.")
                checks.append(_c("meta_desc_length", "Longueur meta description (150-160 car.)", "warning", f"{ml} caractères", 0, 2, "medium", msg, fix))
        else:
            checks.append(_c("meta_desc_length", "Longueur meta description (150-160 car.)", "fail", " - ", 0, 2, "medium",
                "Aucune meta description à évaluer.", "Ajouter d'abord une meta description."))

        # H1
        h1_tags = soup.find_all("h1")
        h1_count = len(h1_tags)
        if h1_count == 1:
            checks.append(_c("h1_unique", "H1 unique présent", "pass",
                h1_tags[0].get_text(strip=True)[:80], 3, 3, "high",
                "Un seul H1  -  structure correcte pour les moteurs de recherche.",
                "Continuer à utiliser un seul H1 par page."))
        elif h1_count == 0:
            checks.append(_c("h1_unique", "H1 unique présent", "fail", "Aucun H1", 0, 3, "high",
                "Aucun H1  -  les moteurs de recherche ne peuvent pas identifier le sujet principal de ta page.",
                "Ajouter un H1 unique et descriptif sur chaque page."))
        else:
            checks.append(_c("h1_unique", "H1 unique présent", "warning", f"{h1_count} H1 trouvés", 0, 3, "high",
                f"{h1_count} balises H1 détectées  -  cela dilue la pertinence de ta page pour Google.",
                "Conserver un seul H1 par page et convertir les autres en H2."))

        # Heading hierarchy
        headings = [int(t.name[1]) for t in soup.find_all(["h1","h2","h3","h4","h5","h6"])]
        seen_lvls: set[int] = set()
        hierarchy_ok = True
        for lvl in headings:
            if lvl > 1 and any(p not in seen_lvls for p in range(1, lvl)):
                hierarchy_ok = False
                break
            seen_lvls.add(lvl)

        checks.append(_c("hn_hierarchy", "Hiérarchie des titres (H1 > H2 > H3)",
            "pass" if hierarchy_ok else "warning",
            f"{len(headings)} titres détectés" if hierarchy_ok else "Hiérarchie incorrecte",
            2 if hierarchy_ok else 0, 2, "medium",
            "Structure des titres correcte  -  facilite la compréhension par les crawlers." if hierarchy_ok
                else "Hiérarchie des titres incorrecte  -  un H2 apparaît avant H1, ou un H3 sans H2 parent.",
            "Maintenir cette hiérarchie." if hierarchy_ok
                else "Respecter l'ordre H1 > H2 > H3 sans sauter de niveau."))

        # Sitemap
        checks.append(_c("sitemap_accessible", "Sitemap XML accessible",
            "pass" if sitemap_ok else "fail",
            "/sitemap.xml trouvé" if sitemap_ok else "/sitemap.xml introuvable",
            4 if sitemap_ok else 0, 4, "high",
            "Le sitemap est accessible  -  Google peut découvrir toutes tes pages facilement." if sitemap_ok
                else "Aucun sitemap XML  -  Google peut rater des pages importantes de ton site.",
            "Maintenir et soumettre le sitemap dans Google Search Console." if sitemap_ok
                else "Générer un sitemap.xml et le soumettre dans Google Search Console."))

        # Robots.txt
        checks.append(_c("robots_accessible", "robots.txt accessible",
            "pass" if robots_ok else "fail",
            "/robots.txt trouvé" if robots_ok else "/robots.txt introuvable",
            2 if robots_ok else 0, 2, "medium",
            "robots.txt présent  -  les crawlers savent comment indexer ton site." if robots_ok
                else "Aucun robots.txt  -  les moteurs de recherche pourraient indexer des pages indésirables.",
            "Maintenir le robots.txt à jour." if robots_ok
                else "Créer un fichier robots.txt à la racine du site."))

        # HTTPS
        https_ok = url.startswith("https://")
        checks.append(_c("https_active", "HTTPS activé",
            "pass" if https_ok else "fail",
            "HTTPS actif" if https_ok else "HTTP non sécurisé",
            4 if https_ok else 0, 4, "critical",
            "HTTPS actif  -  sécurité assurée et signal positif pour Google." if https_ok
                else "Ton site n'est pas sécurisé. Google pénalise les sites HTTP et les navigateurs affichent une alerte.",
            "Maintenir le certificat SSL à jour." if https_ok
                else "Activer HTTPS avec un certificat SSL (gratuit via Let's Encrypt)."))

        # Redirect 301
        if redirect_ok and redirect_code == 301:
            checks.append(_c("redirect_301", "Redirection HTTP → HTTPS (301)", "pass",
                "HTTP → HTTPS (301)", 2, 2, "high",
                "Redirection permanente HTTP → HTTPS  -  pas de contenu dupliqué.",
                "Maintenir la redirection 301."))
        elif redirect_code == 307:
            checks.append(_c("redirect_301", "Redirection HTTP → HTTPS (301)", "warning",
                "Redirection temporaire (307)", 0, 2, "high",
                "Redirection 307 temporaire  -  Google ne consolide pas le PageRank correctement.",
                "Remplacer la redirection 307 par une redirection 301 permanente."))
        elif redirect_ok and redirect_code in (302, 308):
            checks.append(_c("redirect_301", "Redirection HTTP → HTTPS (301)", "warning",
                f"Redirection {redirect_code}", 0, 2, "high",
                f"Redirection {redirect_code} vers HTTPS mais pas 301 permanente.",
                "Configurer une redirection 301 pour consolider le PageRank."))
        else:
            checks.append(_c("redirect_301", "Redirection HTTP → HTTPS (301)", "fail",
                f"Pas de redirection (code {redirect_code})", 0, 2, "high",
                "Pas de redirection HTTP → HTTPS  -  ton site peut être accessible en HTTP, créant du contenu dupliqué.",
                "Configurer une redirection 301 de http:// vers https://."))

        # Canonical
        canon_tag = soup.find("link", rel="canonical")
        canon_href = (canon_tag.get("href", "").rstrip("/") if canon_tag else "").strip()
        url_norm = url.rstrip("/")
        canon_ok = canon_href == url_norm
        if canon_ok:
            checks.append(_c("canonical_correct", "Balise canonical correcte", "pass",
                canon_href, 1, 1, "medium",
                "La balise canonical pointe vers l'URL analysée  -  pas de risque de contenu dupliqué.",
                "Maintenir la balise canonical sur toutes les pages."))
        elif canon_tag:
            checks.append(_c("canonical_correct", "Balise canonical correcte", "warning",
                canon_href, 0, 1, "medium",
                "La balise canonical ne pointe pas vers l'URL analysée  -  risque de contenu dupliqué.",
                "Vérifier et corriger l'URL dans la balise canonical."))
        else:
            checks.append(_c("canonical_correct", "Balise canonical correcte", "fail",
                " - ", 0, 1, "medium",
                "Aucune balise canonical  -  Google peut indexer plusieurs versions de ta page.",
                'Ajouter <link rel="canonical" href="URL"> dans le <head>.'))

        # Schema / JSON-LD
        schemas = soup.find_all("script", type="application/ld+json")
        schema_ok = len(schemas) > 0
        checks.append(_c("schema_present", "Données structurées (JSON-LD) présentes",
            "pass" if schema_ok else "fail",
            f"{len(schemas)} bloc(s) JSON-LD" if schema_ok else "Aucun JSON-LD",
            2 if schema_ok else 0, 2, "high",
            "Données structurées présentes  -  Google peut afficher des rich snippets dans ses résultats." if schema_ok
                else "Aucune donnée structurée  -  tu rates les rich snippets Google (avis, prix, FAQ, etc.).",
            "Maintenir et enrichir les schémas JSON-LD." if schema_ok
                else "Ajouter des données structurées JSON-LD (Organization, LocalBusiness, etc.)."))

        # lang
        html_tag = soup.find("html")
        lang = (html_tag.get("lang", "").strip() if html_tag else "")
        checks.append(_c("lang_attr", "Attribut lang sur <html>",
            "pass" if lang else "fail",
            lang if lang else " - ",
            1 if lang else 0, 1, "medium",
            f"Langue déclarée ({lang})  -  Google sert ta page aux bons utilisateurs." if lang
                else "Langue non déclarée  -  Google peut servir ta page aux mauvaises audiences.",
            'Maintenir lang="fr".' if lang else 'Ajouter lang="fr" sur la balise <html>.'))

        # images alt
        imgs = soup.find_all("img")
        if not imgs:
            checks.append(_c("images_alt", "Attributs alt sur les images", "pass",
                "Aucune image", 1, 1, "low",
                "Aucune image détectée sur la page.", "Ajouter des images optimisées avec attribut alt."))
        else:
            with_alt = sum(1 for img in imgs if img.get("alt", "").strip())
            pct = round(with_alt / len(imgs) * 100)
            if pct == 100:
                checks.append(_c("images_alt", "Attributs alt sur les images", "pass",
                    f"{with_alt}/{len(imgs)} ({pct}%)", 1, 1, "low",
                    "Toutes les images ont un attribut alt  -  accessibilité et SEO image optimisés.",
                    "Maintenir cette bonne pratique."))
            elif pct >= 80:
                checks.append(_c("images_alt", "Attributs alt sur les images", "warning",
                    f"{with_alt}/{len(imgs)} ({pct}%)", 0, 1, "low",
                    f"{len(imgs)-with_alt} image(s) sans alt  -  Google ne peut pas indexer leur contenu.",
                    "Ajouter des attributs alt descriptifs sur toutes les images."))
            else:
                checks.append(_c("images_alt", "Attributs alt sur les images", "fail",
                    f"{with_alt}/{len(imgs)} ({pct}%)", 0, 1, "low",
                    f"La majorité des images ({len(imgs)-with_alt}/{len(imgs)}) n'ont pas d'alt  -  mauvais signal SEO.",
                    "Ajouter des attributs alt descriptifs sur toutes les images."))

        # broken links
        if not internal_links:
            checks.append(_c("no_broken_links", "Pas de liens cassés (10 premiers)", "pass",
                "Aucun lien interne", 1, 1, "medium",
                "Aucun lien interne à vérifier.",
                "Ajouter des liens internes pour améliorer le maillage."))
        elif broken_count == 0:
            checks.append(_c("no_broken_links", "Pas de liens cassés (10 premiers)", "pass",
                f"{len(internal_links)} lien(s) vérifié(s), 0 cassé", 1, 1, "medium",
                "Aucun lien cassé  -  bonne expérience utilisateur et pas de perte de PageRank.",
                "Continuer à surveiller les liens après chaque mise à jour."))
        else:
            checks.append(_c("no_broken_links", "Pas de liens cassés (10 premiers)", "fail",
                f"{broken_count} lien(s) cassé(s) sur {len(internal_links)}", 0, 1, "medium",
                f"{broken_count} lien(s) cassé(s)  -  mauvaise expérience et perte de PageRank.",
                "Identifier et corriger les liens qui retournent une erreur 404."))

        # Open Graph image (2 pts)
        og_image = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        og_img_ok = og_image is not None and og_image.get("content", "").strip().startswith("http")
        checks.append(_c("og_image", "Open Graph image (og:image)",
            "pass" if og_img_ok else "fail",
            og_image.get("content", "-")[:80] if og_image else "-",
            2 if og_img_ok else 0, 2, "medium",
            "og:image presente - partage visuel sur LinkedIn, Facebook, WhatsApp." if og_img_ok
                else "og:image absente - les partages sociaux affichent un apercu vide.",
            "Maintenir og:image." if og_img_ok
                else "Ajouter <meta property=\"og:image\" content=\"https://...\"> dans le <head>."))

        # Twitter Card (1 pt)
        tc = soup.find("meta", attrs={"name": "twitter:card"})
        tc_ok = tc is not None
        checks.append(_c("twitter_card", "Twitter / X Card",
            "pass" if tc_ok else "warning",
            tc.get("content", "-") if tc_ok else "-",
            1 if tc_ok else 0, 1, "low",
            "Twitter Card presente - affichage enrichi lors des partages sur X/Twitter." if tc_ok
                else "Twitter Card absente - partages sur X sans apercu visuel.",
            "Maintenir twitter:card." if tc_ok
                else "Ajouter <meta name=\"twitter:card\" content=\"summary_large_image\">."))

        # Word count (informatif, 0 pts)
        body = soup.find("body")
        body_text = body.get_text(" ", strip=True) if body else soup.get_text(" ", strip=True)
        word_count = len(body_text.split())
        wc_status = "pass" if word_count >= 300 else ("warning" if word_count >= 150 else "fail")
        checks.append(_c("word_count", "Nombre de mots (contenu texte)",
            wc_status, f"{word_count} mots",
            0, 0, "medium",
            f"Contenu suffisant ({word_count} mots)." if word_count >= 300
                else f"Contenu mince ({word_count} mots) - risque de penalite thin content Google.",
            "Maintenir un contenu riche." if word_count >= 300
                else "Enrichir le contenu pour atteindre au moins 300 mots."))

        score = min(sum(c["points"] for c in checks), 25)
        return {"score": score, "maxScore": 25, "checks": checks}

    # ------------------------------------------------------------------
    # BLOCK 3  -  LÉGAL & RGPD (20 pts)
    # ------------------------------------------------------------------

    def _block_legal(self, soup: BeautifulSoup) -> dict:
        checks = []
        html_lower = str(soup).lower()

        # Cookie banner
        cookie_ids = ["cookie", "cookies", "gdpr", "rgpd", "consent",
                      "cookiebanner", "cc-", "tarteaucitron", "axeptio",
                      "didomi", "onetrust", "cookieyes", "cookiebot"]
        cookie_ok = any(kw in html_lower for kw in cookie_ids)
        checks.append(_c("cookie_banner", "Bandeau cookies / consentement RGPD",
            "pass" if cookie_ok else "fail",
            "Détecté" if cookie_ok else "Non détecté",
            6 if cookie_ok else 0, 6, "critical",
            "Bandeau de consentement détecté  -  conformité RGPD assurée." if cookie_ok
                else "Aucun bandeau cookies  -  tu risques une amende CNIL pouvant atteindre 4 % du CA annuel.",
            "Maintenir la solution de consentement." if cookie_ok
                else "Installer une solution de consentement (Axeptio, Tarteaucitron, Cookiebot…)."))

        links_norm = [_norm_fr(a.get_text(strip=True)) for a in soup.find_all("a")]
        mentions_ok = any("mentions legales" in t or "mentions-legales" in t for t in links_norm)
        checks.append(_c("mentions_legales", "Lien mentions légales",
            "pass" if mentions_ok else "fail",
            "Trouvé" if mentions_ok else "Non trouvé",
            5 if mentions_ok else 0, 5, "critical",
            "Lien vers les mentions légales présent  -  obligation légale respectée." if mentions_ok
                else "Aucun lien vers les mentions légales  -  obligation légale non respectée (amende jusqu'à 75 000 €).",
            "Maintenir le lien visible." if mentions_ok
                else "Ajouter un lien 'Mentions légales' visible, de préférence dans le footer."))

        conf_ok = any(
            "confidentialit" in _norm_fr(a.get_text(strip=True))
            or "privacy" in (a.get("href") or "").lower()
            or "confidentialit" in _norm_fr(a.get("href") or "")
            for a in soup.find_all("a")
        )
        checks.append(_c("politique_conf", "Politique de confidentialité",
            "pass" if conf_ok else "fail",
            "Trouvée" if conf_ok else "Non trouvée",
            5 if conf_ok else 0, 5, "high",
            "Politique de confidentialité présente  -  confiance et conformité RGPD." if conf_ok
                else "Aucune politique de confidentialité  -  obligatoire si tu collectes des données personnelles.",
            "Maintenir la politique à jour." if conf_ok
                else "Ajouter une page 'Politique de confidentialité' et la lier depuis le footer."))

        consent_ok = False
        for form in soup.find_all("form"):
            for cb in form.find_all("input", attrs={"type": "checkbox"}):
                cb_id = cb.get("id", "")
                label = form.find("label", attrs={"for": cb_id}) if cb_id else None
                label_text = _norm_fr(label.get_text(strip=True) if label else "")
                if any(kw in label_text for kw in ["accepte", "consent", "rgpd", "donnees"]):
                    consent_ok = True
                    break
            if consent_ok:
                break
        checks.append(_c("consent_checkbox", "Case de consentement dans les formulaires",
            "pass" if consent_ok else "warning",
            "Présente" if consent_ok else "Non détectée",
            4 if consent_ok else 0, 4, "medium",
            "Case de consentement présente dans les formulaires  -  bonne pratique RGPD." if consent_ok
                else "Aucune case de consentement  -  obligatoire avant d'envoyer des emails marketing.",
            "Maintenir la case de consentement." if consent_ok
                else "Ajouter une case à cocher de consentement dans chaque formulaire de contact."))

        return {"score": sum(c["points"] for c in checks), "maxScore": 20, "checks": checks}

    # ------------------------------------------------------------------
    # BLOCK 4  -  CONVERSION (20 pts)
    # ------------------------------------------------------------------

    def _block_conv(self, soup: BeautifulSoup, html: str) -> dict:
        checks = []

        body = soup.find("body")
        body_text = (body.get_text(" ") if body else soup.get_text(" "))[:1000]
        body_norm = _norm_fr(body_text)
        cta_kw = ["contact", "devis", "rdv", "appel", "essai", "demarrer", "commencer", "reserver", "decouvrir"]
        cta_ok = any(kw in body_norm for kw in cta_kw)
        checks.append(_c("cta_above_fold", "Appel à l'action visible dès l'arrivée",
            "pass" if cta_ok else "fail",
            "CTA détecté" if cta_ok else "Aucun CTA détecté",
            6 if cta_ok else 0, 6, "critical",
            "Un appel à l'action est visible dès l'arrivée  -  les visiteurs savent quoi faire." if cta_ok
                else "Aucun appel à l'action visible  -  tes visiteurs ne savent pas par où commencer et partent.",
            "Maintenir le CTA bien visible et au-dessus de la ligne de flottaison." if cta_ok
                else "Ajouter un bouton CTA visible dès la première vue (ex : 'Demander un devis', 'Prendre RDV')."))

        forms = soup.find_all("form")
        form_ok = len(forms) > 0
        checks.append(_c("form_present", "Formulaire de contact présent",
            "pass" if form_ok else "fail",
            f"{len(forms)} formulaire(s)" if form_ok else "Aucun formulaire",
            5 if form_ok else 0, 5, "high",
            f"{len(forms)} formulaire(s) présent(s)  -  les visiteurs peuvent te contacter facilement." if form_ok
                else "Aucun formulaire  -  tu perds des contacts de visiteurs intéressés.",
            "Maintenir au moins un formulaire de contact." if form_ok
                else "Ajouter un formulaire de contact simple (prénom, email, message) sur la page."))

        phone_re = re.compile(r'(\+33|0)[1-9][\s.\-]?(\d{2}[\s.\-]?){4}')
        phone_ok = bool(phone_re.search(soup.get_text(" ")))
        checks.append(_c("phone_detectable", "Numéro de téléphone détectable",
            "pass" if phone_ok else "fail",
            "Trouvé" if phone_ok else "Non trouvé",
            4 if phone_ok else 0, 4, "high",
            "Numéro de téléphone visible  -  facilite le contact direct avec tes prospects." if phone_ok
                else "Aucun numéro de téléphone  -  tu perds des clients qui préfèrent appeler directement.",
            "Maintenir le numéro visible et cliquable (lien tel:)." if phone_ok
                else "Afficher ton numéro de téléphone de façon bien visible, de préférence dans le header."))

        text_norm = _norm_fr(soup.get_text(" "))
        proof_kw = ["avis", "temoignage", "client", "etoile", "note", "review", "trustpilot", "google"]
        hits = sum(1 for kw in proof_kw if kw in text_norm)
        jsonld = _parse_jsonld(soup)
        if any(s.get("@type") in ("Review", "AggregateRating") or s.get("aggregateRating") for s in jsonld):
            hits += 1
        proof_ok = hits >= 2
        checks.append(_c("social_proof", "Preuve sociale (avis, témoignages, notes)",
            "pass" if proof_ok else ("warning" if hits == 1 else "fail"),
            f"{hits} indicateur(s)",
            5 if proof_ok else 0, 5, "high",
            "Preuve sociale détectée  -  les visiteurs sont rassurés par les avis clients." if proof_ok
                else "Aucune preuve sociale visible  -  les visiteurs n'ont pas de raison de te faire confiance.",
            "Maintenir les avis et témoignages bien visibles." if proof_ok
                else "Ajouter des avis clients, témoignages ou notes Google sur ta page d'accueil."))

        html_lower = str(soup).lower()
        maps_ok = any(kw in html_lower for kw in ["maps.google", "goo.gl/maps", "maps.app.goo"]) or bool(soup.find("address"))
        checks.append(_c("maps_address", "Carte ou adresse intégrée (informatif)",
            "pass" if maps_ok else "warning",
            "Trouvée" if maps_ok else "Non détectée",
            0, 0, "low",
            "Carte ou adresse intégrée  -  tes visiteurs trouvent facilement ton emplacement." if maps_ok
                else "Aucune carte ni adresse physique  -  les visiteurs ne savent pas où tu te trouves.",
            "Maintenir la carte intégrée." if maps_ok
                else "Intégrer une carte Google Maps ou afficher ton adresse avec la balise <address>."))

        chat_ok = any(kw in html_lower for kw in ["intercom", "tidio", "crisp", "tawk", "freshchat", "hubspot", "drift", "zendesk"])
        checks.append(_c("chat_widget", "Widget de chat en ligne (informatif)",
            "pass" if chat_ok else "warning",
            "Détecté" if chat_ok else "Non détecté",
            0, 0, "low",
            "Widget de chat détecté  -  tu peux engager les visiteurs en temps réel." if chat_ok
                else "Aucun widget de chat  -  tu rates des opportunités de conversion en temps réel.",
            "Maintenir le chat en ligne." if chat_ok
                else "Envisager l'ajout d'un chat (Tidio, Crisp…) pour engager les visiteurs."))

        return {"score": sum(c["points"] for c in checks), "maxScore": 20, "checks": checks}

    # ------------------------------------------------------------------
    # BLOCK 5  -  MOBILE (10 pts)
    # ------------------------------------------------------------------

    def _block_mobile(self, soup: BeautifulSoup, mob: dict) -> dict:
        checks = []
        mob_lhr = mob.get("lighthouseResult", {})
        mob_audits = mob_lhr.get("audits", {})
        mob_err = not mob_lhr

        vp = soup.find("meta", attrs={"name": "viewport"})
        vp_ok = vp is not None
        checks.append(_c("viewport_set", "Balise meta viewport présente",
            "pass" if vp_ok else "fail",
            vp.get("content", "") if vp else " - ",
            4 if vp_ok else 0, 4, "critical",
            "Meta viewport présente  -  le site s'adapte correctement aux écrans mobiles." if vp_ok
                else "Aucune meta viewport  -  ton site ne s'adapte pas aux mobiles, rédhibitoire pour 60 % des visiteurs.",
            'Maintenir <meta name="viewport" content="width=device-width, initial-scale=1">.' if vp_ok
                else 'Ajouter <meta name="viewport" content="width=device-width, initial-scale=1"> dans le <head>.'))

        raw = mob_lhr.get("categories", {}).get("performance", {}).get("score") if not mob_err else None
        mob_s = int(raw * 100) if raw is not None else None
        if mob_s is None:
            checks.append(_c("mobile_score_50", "Score mobile > 50", "unavailable", " - ", 0, 3, "high",
                "Score mobile non disponible.", "Vérifier la configuration PageSpeed API."))
        elif mob_s > 50:
            checks.append(_c("mobile_score_50", "Score mobile > 50", "pass", f"{mob_s}/100", 3, 3, "high",
                f"Score mobile de {mob_s}/100  -  acceptable pour les utilisateurs mobiles.",
                "Continuer à améliorer les performances mobiles."))
        else:
            checks.append(_c("mobile_score_50", "Score mobile > 50", "fail", f"{mob_s}/100", 0, 3, "high",
                f"Score mobile de {mob_s}/100  -  en dessous du seuil minimum. La majorité des visiteurs sont sur mobile.",
                "Optimiser en priorité les performances mobiles."))

        font_score = mob_audits.get("font-size", {}).get("score") if not mob_err else None
        text_ok = font_score == 1
        checks.append(_c("text_readable", "Texte lisible sur mobile (taille minimale)",
            "pass" if text_ok else ("fail" if font_score is not None else "unavailable"),
            "Lisible" if text_ok else ("Trop petit" if font_score is not None else " - "),
            3 if text_ok else 0, 3, "high",
            "La taille des textes est adaptée aux écrans mobiles." if text_ok
                else "Textes trop petits sur mobile  -  les visiteurs doivent zoomer pour lire.",
            "Maintenir une taille de police minimale de 16px." if text_ok
                else "Augmenter la taille de police à 16px minimum pour les textes de corps."))

        rb = mob_audits.get("render-blocking-resources", {}) if not mob_err else {}
        rb_ok = rb.get("score") == 1
        checks.append(_c("no_blocking_res", "Ressources bloquant le rendu (informatif)",
            "pass" if rb_ok else "warning",
            rb.get("displayValue") or ("OK" if rb_ok else "Ressources bloquantes détectées"),
            0, 0, "medium",
            "Aucune ressource bloquante  -  la page se charge sans obstacle." if rb_ok
                else "Des ressources bloquent le rendu  -  elles retardent l'affichage de ta page.",
            "Continuer à éviter les ressources bloquantes." if rb_ok
                else "Différer ou déplacer les scripts et CSS non critiques."))

        return {"score": sum(c["points"] for c in checks), "maxScore": 10, "checks": checks}

    # ------------------------------------------------------------------
    # BLOCK 6  -  LOCAL SEO (informatif, localScore /100)
    # ------------------------------------------------------------------

    def _block_local_seo(self, soup: BeautifulSoup) -> dict:
        schemas = _parse_jsonld(soup)
        checks = []

        _LOCAL_TYPES = (
            "LocalBusiness", "ProfessionalService", "Restaurant", "Dentist", "Doctor",
            "Lawyer", "Plumber", "Electrician", "GeneralContractor", "HomeAndConstructionBusiness",
            "LegalService", "MedicalBusiness", "Store", "FoodEstablishment", "AutoDealer",
        )
        org = next((s for s in schemas if s.get("@type") in _LOCAL_TYPES), None)
        is_local = org is not None

        # LocalBusiness schema present
        checks.append(_c("local_schema", "Schema LocalBusiness (ou type derive)",
            "pass" if is_local else "fail",
            org.get("@type", "-") if is_local else "Absent",
            8 if is_local else 0, 8, "critical",
            f"Schema {org.get('@type')} present - Google comprend qu'il s'agit d'un etablissement local." if is_local
                else "Aucun schema LocalBusiness - Google ne peut pas afficher ta fiche locale dans les resultats.",
            "Maintenir et enrichir le schema." if is_local
                else "Ajouter un schema LocalBusiness ou ProfessionalService avec nom, adresse, telephone."))

        # Address in schema
        addr = org.get("address") if is_local else None
        addr_ok = bool(addr)
        checks.append(_c("local_address", "Adresse dans le schema",
            "pass" if addr_ok else ("fail" if is_local else "warning"),
            str(addr)[:80] if addr_ok else "-",
            4 if addr_ok else 0, 4, "high",
            "Adresse declaree dans le schema - utilisable par Google Maps et les assistants IA." if addr_ok
                else "Adresse absente du schema - Google ne peut pas verifier ta localisation.",
            "Maintenir l'adresse complete." if addr_ok
                else "Ajouter PostalAddress dans le schema avec streetAddress, addressLocality, postalCode."))

        # Telephone in schema
        tel_schema = org.get("telephone") if is_local else None
        tel_ok = bool(tel_schema)
        checks.append(_c("local_telephone", "Telephone dans le schema",
            "pass" if tel_ok else ("fail" if is_local else "warning"),
            str(tel_schema) if tel_ok else "-",
            4 if tel_ok else 0, 4, "high",
            f"Telephone declare ({tel_schema}) - cliquable dans Google et les assistants." if tel_ok
                else "Telephone absent du schema - Google ne peut pas l'afficher dans la fiche.",
            "Maintenir le telephone." if tel_ok
                else "Ajouter telephone dans le schema LocalBusiness."))

        # Opening hours
        oh = (org.get("openingHours") or org.get("openingHoursSpecification")) if is_local else None
        oh_ok = bool(oh)
        checks.append(_c("opening_hours", "Horaires d'ouverture dans le schema",
            "pass" if oh_ok else ("warning" if is_local else "warning"),
            str(oh)[:60] if oh_ok else "-",
            3 if oh_ok else 0, 3, "medium",
            "Horaires declares - affiches dans Google My Business et les resultats locaux." if oh_ok
                else "Horaires absents - informations manquantes pour les recherches locales.",
            "Maintenir les horaires." if oh_ok
                else "Ajouter openingHours dans le schema (ex: 'Mo-Fr 09:00-18:00')."))

        # Area served / geo
        area = (org.get("areaServed") or org.get("geo") or org.get("serviceArea")) if is_local else None
        area_ok = bool(area)
        checks.append(_c("area_served", "Zone de service (areaServed)",
            "pass" if area_ok else "warning",
            str(area)[:60] if area_ok else "-",
            2 if area_ok else 0, 2, "low",
            "Zone de service declaree - Google cible les bons internautes locaux." if area_ok
                else "Zone de service absente - Google ne sait pas ou tu interviens.",
            "Maintenir areaServed." if area_ok
                else "Ajouter areaServed dans le schema avec les villes ou regions couvertes."))

        # GBP link in sameAs
        same_as = org.get("sameAs", []) if is_local else []
        if isinstance(same_as, str):
            same_as = [same_as]
        gbp_patterns = ["google.com/maps", "g.page/", "goo.gl/maps", "business.google.com", "maps.google"]
        gbp_url = next((u for u in same_as if any(p in str(u) for p in gbp_patterns)), None)
        gbp_ok = gbp_url is not None
        checks.append(_c("gbp_link", "Google Business Profile lie (sameAs)",
            "pass" if gbp_ok else ("fail" if is_local else "warning"),
            gbp_url[:80] if gbp_ok else "-",
            6 if gbp_ok else 0, 6, "high",
            "GBP lie dans sameAs - Google verifie et consolide ta presence locale." if gbp_ok
                else "GBP absent de sameAs - opportunite de consolidation locale manquee.",
            "Maintenir le lien GBP." if gbp_ok
                else "Ajouter l'URL de ta fiche Google Business Profile dans sameAs."))

        # AggregateRating on local schema
        ar = (org.get("aggregateRating") if is_local else None)
        ar_ok = bool(ar and isinstance(ar, dict) and ar.get("ratingValue"))
        rating_val = str(ar.get("ratingValue", "")) + "/5" if ar_ok else "-"
        review_count = str(ar.get("reviewCount", "")) if ar_ok else ""
        ar_display = f"{rating_val} ({review_count} avis)" if (ar_ok and review_count) else rating_val
        checks.append(_c("local_rating", "Note agregee (AggregateRating) dans le schema local",
            "pass" if ar_ok else ("fail" if is_local else "warning"),
            ar_display,
            6 if ar_ok else 0, 6, "high",
            f"Note agregee presente ({ar_display}) - Google peut afficher les etoiles dans les resultats locaux." if ar_ok
                else "Aucune note dans le schema local - tu rates les rich snippets etoiles.",
            "Maintenir l'AggregateRating." if ar_ok
                else "Ajouter AggregateRating avec ratingValue et reviewCount depuis tes avis Google."))

        # NAP visible in page text (informative)
        page_text = soup.get_text(" ")
        phone_re = re.compile(r'(\+33|0)[1-9][\s.\-]?(\d{2}[\s.\-]?){4}')
        nap_phone_ok = bool(phone_re.search(page_text))
        checks.append(_c("nap_visible", "Telephone visible dans le texte de la page",
            "pass" if nap_phone_ok else ("warning" if is_local else "warning"),
            "Trouve" if nap_phone_ok else "Non trouve",
            2 if nap_phone_ok else 0, 2, "medium",
            "Telephone visible dans le texte - coherence NAP (Nom/Adresse/Phone) assures." if nap_phone_ok
                else "Telephone non detecte dans le texte - risque d'incoherence NAP.",
            "Maintenir le numero visible." if nap_phone_ok
                else "Afficher le telephone de facon claire dans le contenu textuel de la page."))

        local_max = sum(c["maxPoints"] for c in checks)
        local_earned = sum(c["points"] for c in checks)
        local_score = round(local_earned / local_max * 100) if local_max else 0
        return {"score": 0, "maxScore": 0, "localScore": local_score, "isLocal": is_local, "checks": checks}

    # ------------------------------------------------------------------
    # BLOCK 7  -  E-COMMERCE (informatif, ecommerceScore /100)
    # ------------------------------------------------------------------

    def _block_ecommerce(self, soup: BeautifulSoup, tech_stack: dict) -> dict:
        schemas = _parse_jsonld(soup)
        checks = []

        ecom_cms = {"Shopify", "WooCommerce", "PrestaShop"}
        cms_tools = [t for ts in tech_stack.values() for t in ts if t in ecom_cms]
        product_schemas = [s for s in schemas if s.get("@type") in ("Product", "ProductGroup")]
        is_ecommerce = bool(cms_tools or product_schemas)

        # E-commerce CMS or Product schema detected
        checks.append(_c("ecom_detected", "Site e-commerce detecte (CMS ou schema Product)",
            "pass" if is_ecommerce else "warning",
            ", ".join(cms_tools) if cms_tools else ("schema Product" if product_schemas else "Non detecte"),
            4 if is_ecommerce else 0, 4, "high",
            f"E-commerce detecte ({', '.join(cms_tools) or 'schema Product'})." if is_ecommerce
                else "Aucun e-commerce detecte sur cette page - checks non applicables.",
            "Maintenir le CMS e-commerce." if is_ecommerce
                else "Non applicable pour ce type de site."))

        # Product schema
        prod = product_schemas[0] if product_schemas else None
        prod_ok = prod is not None
        checks.append(_c("product_schema", "Schema Product en JSON-LD",
            "pass" if prod_ok else ("fail" if is_ecommerce else "warning"),
            f"{len(product_schemas)} produit(s)" if prod_ok else "-",
            8 if prod_ok else 0, 8, "critical",
            f"{len(product_schemas)} schema(s) Product present(s) - Google peut afficher les produits dans Shopping." if prod_ok
                else "Aucun schema Product - tes produits sont invisibles dans Google Shopping.",
            "Maintenir et enrichir les schemas Product." if prod_ok
                else "Ajouter un schema Product sur chaque page produit avec name, image, description."))

        offers = (prod.get("offers") if prod else None)
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        price_ok = bool(offers and (offers.get("price") or offers.get("lowPrice")))

        # offers.availability
        avail = offers.get("availability", "") if offers else ""
        avail_ok = bool(avail)
        checks.append(_c("product_availability", "Disponibilite (offers.availability)",
            "pass" if avail_ok else ("fail" if prod else "warning"),
            avail.replace("https://schema.org/", "") if avail_ok else "-",
            4 if avail_ok else 0, 4, "medium",
            "Disponibilite declaree - Google peut filtrer par produits en stock." if avail_ok
                else "Disponibilite absente - Google ne peut pas indiquer si le produit est disponible.",
            "Maintenir availability." if avail_ok
                else "Ajouter availability: 'https://schema.org/InStock' dans offers."))

        # AggregateRating on Product
        prod_rating = (prod.get("aggregateRating") if prod else None)
        pr_ok = bool(prod_rating and isinstance(prod_rating, dict) and prod_rating.get("ratingValue"))
        pr_display = f"{prod_rating.get('ratingValue', '')}/5 ({prod_rating.get('reviewCount', '?')} avis)" if pr_ok else "-"
        checks.append(_c("product_rating", "AggregateRating sur le produit",
            "pass" if pr_ok else ("warning" if prod else "warning"),
            pr_display,
            6 if pr_ok else 0, 6, "high",
            f"Note produit presente ({pr_display}) - etoiles dans les resultats Google Shopping." if pr_ok
                else "Aucune note produit - tu rates les rich snippets etoiles sur tes produits.",
            "Maintenir l'AggregateRating produit." if pr_ok
                else "Ajouter aggregateRating avec ratingValue et reviewCount sur chaque schema Product."))

        # Brand in Product
        brand = prod.get("brand") if prod else None
        brand_ok = bool(brand)
        brand_name = brand.get("name", str(brand)) if isinstance(brand, dict) else str(brand) if brand else "-"
        checks.append(_c("product_brand", "Marque (brand) dans le schema Product",
            "pass" if brand_ok else "warning",
            brand_name[:40],
            2 if brand_ok else 0, 2, "low",
            f"Marque declaree ({brand_name}) - signal de confiance pour Google Shopping." if brand_ok
                else "Marque absente du schema Product - signal manquant pour Google Shopping.",
            "Maintenir brand." if brand_ok
                else "Ajouter brand: {name: 'NomDeLaMarque'} dans le schema Product."))

        # Currency in offers
        currency = offers.get("priceCurrency", "") if offers else ""
        curr_ok = bool(currency)
        checks.append(_c("product_currency", "Devise (priceCurrency) dans les offres",
            "pass" if curr_ok else ("fail" if price_ok else "warning"),
            currency if curr_ok else "-",
            2 if curr_ok else 0, 2, "medium",
            f"Devise declaree ({currency}) - requis pour Google Shopping." if curr_ok
                else "Devise absente - Google Shopping requiert priceCurrency avec le prix.",
            "Maintenir priceCurrency." if curr_ok
                else "Ajouter priceCurrency: 'EUR' dans l'objet offers."))

        # Images produit avec alt
        prod_imgs = soup.find_all("img") if is_ecommerce else []
        if prod_imgs:
            with_alt = sum(1 for img in prod_imgs if img.get("alt", "").strip())
            img_pct = round(with_alt / len(prod_imgs) * 100)
            img_ok = img_pct == 100
            checks.append(_c("product_img_alt", "Images avec attribut alt (e-commerce)",
                "pass" if img_ok else ("warning" if img_pct >= 70 else "fail"),
                f"{with_alt}/{len(prod_imgs)} ({img_pct}%)",
                3 if img_ok else 0, 3, "medium",
                "Toutes les images ont un alt - SEO image et accessibilite optimises." if img_ok
                    else f"{len(prod_imgs) - with_alt} image(s) sans alt - Google ne peut pas indexer ces produits en image.",
                "Maintenir les attributs alt." if img_ok
                    else "Ajouter un attribut alt descriptif sur toutes les images produit."))

        ecom_max = sum(c["maxPoints"] for c in checks)
        ecom_earned = sum(c["points"] for c in checks)
        ecom_score = round(ecom_earned / ecom_max * 100) if ecom_max else 0
        return {"score": 0, "maxScore": 0, "ecommerceScore": ecom_score, "isEcommerce": is_ecommerce, "checks": checks}

    # ------------------------------------------------------------------
    # BLOCK 6  -  GEO (informatif, score /100 bonus)
    # ------------------------------------------------------------------

    def _block_geo(self, soup: BeautifulSoup, robots_text: str, llms_ok: bool) -> dict:
        checks = []

        # Crawler access
        crawler_status = _parse_robots_for_ai(robots_text) if robots_text else {c: "allowed" for c in _AI_CRAWLERS}
        allowed = [c for c, s in crawler_status.items() if s == "allowed"]
        blocked = [c for c, s in crawler_status.items() if s == "blocked"]
        crawlers_ok = not blocked
        checks.append(_c("ai_crawlers_allowed", "Accès des robots IA (GPTBot, ClaudeBot…)",
            "pass" if crawlers_ok else ("warning" if len(blocked) < 3 else "fail"),
            f"{len(allowed)}/6 autorisés" + (f"  -  bloqués : {', '.join(blocked)}" if blocked else ""),
            20 if crawlers_ok else (10 if len(blocked) < 3 else 0), 20, "high",
            "Tous les crawlers IA sont autorisés  -  ton contenu peut être cité par ChatGPT, Claude, Perplexity…" if crawlers_ok
                else f"Certains crawlers IA bloqués ({', '.join(blocked)})  -  ton site est invisible pour ces plateformes.",
            "Maintenir l'accès aux crawlers IA." if crawlers_ok
                else "Retirer les Disallow pour les crawlers IA dans robots.txt."))

        sitemap_rb = any(line.strip().lower().startswith("sitemap:") for line in robots_text.splitlines()) if robots_text else False
        checks.append(_c("sitemap_in_robots", "Sitemap déclaré dans robots.txt",
            "pass" if sitemap_rb else "warning",
            "Déclaré" if sitemap_rb else "Non déclaré",
            10 if sitemap_rb else 0, 10, "medium",
            "Le sitemap est déclaré dans robots.txt  -  les crawlers le trouvent automatiquement." if sitemap_rb
                else "Le sitemap n'est pas déclaré dans robots.txt  -  certains crawlers pourraient ne pas le trouver.",
            "Maintenir la ligne Sitemap: dans robots.txt." if sitemap_rb
                else "Ajouter 'Sitemap: https://monsite.fr/sitemap.xml' à la fin de robots.txt."))

        checks.append(_c("llms_txt_present", "Fichier llms.txt présent",
            "pass" if llms_ok else "fail",
            "Trouvé" if llms_ok else "Non trouvé",
            20 if llms_ok else 0, 20, "high",
            "llms.txt présent  -  les IA comme Claude et ChatGPT comprennent le contexte de ton site." if llms_ok
                else "Aucun fichier llms.txt  -  ton site n'est pas optimisé pour la visibilité dans les outils IA.",
            "Maintenir et enrichir le fichier llms.txt." if llms_ok
                else "Créer /llms.txt décrivant ton entreprise, tes services et ton domaine d'expertise."))

        # JSON-LD checks
        schemas = _parse_jsonld(soup)

        def _find(type_name):
            return next((s for s in schemas if s.get("@type") == type_name), None)

        def _any(*type_names):
            return next((s for s in schemas if s.get("@type") in type_names), None)

        # org_type
        org = _any("LocalBusiness", "ProfessionalService", "Organization", "Corporation")
        org_type = org.get("@type") if org else None
        generic = org_type == "Organization"
        checks.append(_c("org_type", "Type d'organisation précis (JSON-LD)",
            "pass" if (org_type and not generic) else ("warning" if generic else "fail"),
            org_type or " - ",
            8 if (org_type and not generic) else (4 if generic else 0), 8, "medium",
            f"Type précis ({org_type})  -  meilleure compréhension par les IA." if (org_type and not generic)
                else ("Type générique 'Organization'  -  préférer LocalBusiness ou ProfessionalService." if generic
                      else "Aucun schéma d'organisation  -  les IA ne comprennent pas la nature de ton activité."),
            "Maintenir le type précis." if (org_type and not generic)
                else "Remplacer @type: Organization par LocalBusiness ou ProfessionalService."))

        # sameAs
        same_as = org.get("sameAs", []) if org else []
        if isinstance(same_as, str):
            same_as = [same_as]
        sa_ok = bool(same_as)
        checks.append(_c("same_as", "Propriété sameAs (profils réseaux, annuaires)",
            "pass" if sa_ok else "warning",
            f"{len(same_as)} profil(s) : {', '.join(str(u)[:40] for u in same_as[:3])}" if sa_ok else " - ",
            10 if sa_ok else 0, 10, "high",
            f"{len(same_as)} profil(s) lié(s)  -  les IA peuvent vérifier ton identité sur d'autres plateformes." if sa_ok
                else "Aucun sameAs  -  les IA ne peuvent pas vérifier ton identité sur d'autres plateformes.",
            "Ajouter d'autres profils (LinkedIn, GBP…) si non présents." if sa_ok
                else "Ajouter sameAs avec tes profils LinkedIn, Google Business Profile, Facebook."))

        # speakable
        speakable_ok = any("speakable" in s for s in schemas)
        checks.append(_c("speakable", "Propriété speakable",
            "pass" if speakable_ok else "warning",
            "Présent" if speakable_ok else "Absent",
            5 if speakable_ok else 0, 5, "low",
            "speakable présent  -  tes informations clés peuvent être lues par les assistants IA." if speakable_ok
                else "speakable absent  -  tes contenus ne sont pas optimisés pour les assistants vocaux.",
            "Maintenir la propriété speakable." if speakable_ok
                else "Ajouter speakable dans tes schémas pour cibler les assistants vocaux IA."))

        # price_spec_valid
        price_schemas = [s for s in schemas if "priceSpecification" in s]
        if not price_schemas:
            checks.append(_c("price_spec_valid", "priceSpecification valide", "pass",
                "Non applicable", 3, 3, "low",
                "Aucune priceSpecification  -  non applicable pour ce type de page.",
                "Ajouter des priceSpecification si tu vends des produits ou services."))
        else:
            invalid = any(isinstance(s["priceSpecification"], str) for s in price_schemas)
            checks.append(_c("price_spec_valid", "priceSpecification valide (objet)",
                "fail" if invalid else "pass",
                "Invalide (string)" if invalid else "Valide (objet)",
                0 if invalid else 3, 3, "medium",
                "priceSpecification valide en objet  -  correct pour Google." if not invalid
                    else "priceSpecification définie comme string  -  invalide, Google ignorera ce schéma.",
                "Maintenir la structure objet." if not invalid
                    else "Remplacer la string par un objet {priceCurrency, price}."))

        # foundingDate
        fd = org.get("foundingDate") if org else None
        checks.append(_c("founding_date", "foundingDate dans Organization",
            "pass" if fd else "warning", str(fd) if fd else " - ",
            3 if fd else 0, 3, "low",
            f"Date de création déclarée ({fd})  -  renforce la crédibilité auprès des IA." if fd
                else "foundingDate absent  -  les IA ne connaissent pas l'ancienneté de ton entreprise.",
            "Maintenir foundingDate." if fd else "Ajouter foundingDate dans le schéma Organization."))

        # numberOfEmployees
        emp = org.get("numberOfEmployees") if org else None
        checks.append(_c("employee_count", "numberOfEmployees dans Organization",
            "pass" if emp else "warning", str(emp) if emp else " - ",
            3 if emp else 0, 3, "low",
            "Taille d'équipe déclarée  -  signal de confiance pour les IA." if emp
                else "numberOfEmployees absent  -  information manquante pour les IA.",
            "Maintenir numberOfEmployees." if emp else "Ajouter numberOfEmployees dans le schéma Organization."))

        # Person + knowsAbout
        person = _find("Person")
        knows = person.get("knowsAbout") if person else None
        person_ok = bool(person and knows)
        checks.append(_c("person_schema", "Schema Person avec knowsAbout",
            "pass" if person_ok else "warning",
            str(knows)[:80] if knows else ("Person sans knowsAbout" if person else " - "),
            3 if person_ok else 0, 3, "low",
            "Schema Person avec knowsAbout  -  les IA comprennent l'expertise de l'auteur." if person_ok
                else "Aucun schema Person  -  les IA ne connaissent pas l'expertise de l'équipe.",
            "Maintenir Person avec knowsAbout." if person_ok
                else "Ajouter un schéma Person avec knowsAbout pour les auteurs ou fondateurs."))

        # dateModified
        blog = _find("BlogPosting") or _find("Article")
        if not blog:
            checks.append(_c("date_modified", "dateModified dans BlogPosting/Article",
                "pass", "Non applicable", 2, 2, "low",
                "Aucun article détecté sur cette page.", "Ajouter dateModified sur chaque article de blog."))
        else:
            dm = blog.get("dateModified")
            checks.append(_c("date_modified", "dateModified dans BlogPosting/Article",
                "pass" if dm else "warning", str(dm) if dm else " - ",
                2 if dm else 0, 2, "low",
                "dateModified présent  -  Google sait que ton contenu est à jour." if dm
                    else "dateModified absent  -  Google ne sait pas si ton article est récent.",
                "Maintenir dateModified." if dm else "Ajouter dateModified dans le schéma BlogPosting."))

        # BreadcrumbList
        bc = _find("BreadcrumbList")
        checks.append(_c("breadcrumb", "BreadcrumbList dans JSON-LD",
            "pass" if bc else "warning",
            "Présent" if bc else "Absent",
            6 if bc else 0, 6, "medium",
            "BreadcrumbList présent  -  Google peut afficher le fil d'ariane dans ses résultats." if bc
                else "BreadcrumbList absent  -  tu rates l'affichage du fil d'ariane dans Google.",
            "Maintenir le BreadcrumbList." if bc
                else "Ajouter un schéma BreadcrumbList sur chaque page."))

        # AggregateRating / Review
        rating = _any("AggregateRating", "Review") or next((s for s in schemas if s.get("aggregateRating")), None)
        ar_ok = rating is not None
        checks.append(_c("aggregate_rating", "AggregateRating ou Review en JSON-LD",
            "pass" if ar_ok else "warning",
            "Présent" if ar_ok else "Absent",
            8 if ar_ok else 0, 8, "high",
            "Notes et avis en JSON-LD  -  Google peut afficher les étoiles dans les résultats." if ar_ok
                else "Aucune note en JSON-LD  -  tu rates les rich snippets avec étoiles dans Google.",
            "Maintenir les avis en JSON-LD." if ar_ok
                else "Ajouter un schéma AggregateRating avec tes avis Google/Trustpilot."))

        # FAQPage
        faq = _find("FAQPage")
        checks.append(_c("faq_schema", "Schema FAQPage",
            "pass" if faq else "warning",
            "Present" if faq else "Absent",
            6 if faq else 0, 6, "medium",
            "FAQPage present - Google peut afficher la FAQ directement dans les resultats (AI Overviews)." if faq
                else "FAQPage absent - opportunite manquee pour les rich snippets et AI Overviews.",
            "Maintenir le schema FAQPage." if faq
                else "Ajouter un schema FAQPage si tu as des questions/reponses sur la page."))

        # HowTo
        howto = _find("HowTo")
        checks.append(_c("how_to", "Schema HowTo",
            "pass" if howto else "warning",
            "Présent" if howto else "Absent",
            2 if howto else 0, 2, "low",
            "Schema HowTo présent  -  Google peut afficher un guide étape par étape dans les résultats." if howto
                else "Aucun schema HowTo  -  opportunité manquée pour les contenus de type 'guide'.",
            "Maintenir le schema HowTo." if howto
                else "Envisager un schema HowTo pour tes pages de type 'comment faire'."))

        # images_absolute in schemas
        img_urls = []
        for s in schemas:
            for key in ("image", "logo", "photo"):
                val = s.get(key)
                if isinstance(val, str):
                    img_urls.append(val)
                elif isinstance(val, dict):
                    u = val.get("url", "")
                    if u:
                        img_urls.append(u)
        relative = [u for u in img_urls if u and not u.startswith("http")]
        if not img_urls:
            checks.append(_c("images_absolute", "URLs d'images absolues (JSON-LD)",
                "warning", "Aucune image dans les schémas",
                0, 3, "medium",
                "Aucune image déclarée dans les schémas JSON-LD.",
                "Ajouter des images absolues (https://…) dans les schémas Organization ou Article."))
        elif not relative:
            checks.append(_c("images_absolute", "URLs d'images absolues (JSON-LD)",
                "pass", f"{len(img_urls)} image(s)  -  toutes absolues",
                3, 3, "medium",
                "Toutes les URLs d'images dans les schémas sont absolues  -  correct.",
                "Maintenir les URLs absolues."))
        else:
            checks.append(_c("images_absolute", "URLs d'images absolues (JSON-LD)",
                "fail", f"{len(relative)} URL(s) relative(s)",
                0, 3, "medium",
                f"{len(relative)} URL(s) d'image relative(s) dans les schémas  -  Google peut ne pas les indexer.",
                "Utiliser des URLs absolues (https://…) pour toutes les images dans les schémas JSON-LD."))

        # wordCount
        if not blog:
            checks.append(_c("word_count", "wordCount dans BlogPosting/Article",
                "pass", "Non applicable", 2, 2, "low",
                "Aucun article détecté sur cette page.", "Ajouter wordCount sur chaque article de blog."))
        else:
            wc = blog.get("wordCount")
            checks.append(_c("word_count", "wordCount dans BlogPosting/Article",
                "pass" if wc else "warning", str(wc) if wc else " - ",
                2 if wc else 0, 2, "low",
                "wordCount présent  -  signal de richesse de contenu pour les IA." if wc
                    else "wordCount absent  -  signal manquant pour les moteurs IA.",
                "Maintenir wordCount." if wc else "Ajouter wordCount dans le schéma BlogPosting."))

        geo_max = sum(c["maxPoints"] for c in checks if c["maxPoints"] > 0)
        geo_earned = sum(c["points"] for c in checks)
        geo_score = round(geo_earned / geo_max * 100) if geo_max > 0 else 0

        geo_contrib = round(geo_earned / geo_max * 20) if geo_max else 0
        return {"score": geo_contrib, "maxScore": 20, "geoScore": geo_score, "checks": checks}
