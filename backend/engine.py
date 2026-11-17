"""SEO checks for Rank Wise. Standard library only, same rules as the project spec."""

from __future__ import annotations

import html
import ipaddress
import re
import socket
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener

UA = "Mozilla/5.0 (compatible; RankWise/1.0; educational SEO auditor)"
MAX_BYTES = 1_500_000
TIMEOUT = 14
MAX_REDIRECTS = 5
SAMPLE_URL = "https://example.com/"
SAMPLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Harbor & Pine Coffee — Fresh beans, quiet tables, weekday breakfast</title>
  <meta name="description" content="Harbor & Pine is a neighbourhood coffee shop with house-roasted beans, pastry, and a calm place to work. Open daily in the old mill quarter.">
</head>
<body>
  <header>
    <a href="/">Home</a>
    <a href="/menu">Menu</a>
    <a href="/visit">Visit</a>
    <a href="https://www.instagram.com/">Instagram</a>
  </header>
  <h1>Coffee roasted in small batches</h1>
  <h2>This week's pour-overs</h2>
  <p>Single-origin cups from Kenya, Peru, and Ethiopia.</p>
  <h2>The room</h2>
  <h3>Morning light</h3>
  <img src="/images/shop-front.jpg" alt="Shop front with pine signage">
  <img src="/images/espresso.jpg">
  <img src="/images/pastry.jpg" alt="">
  <a href="/about">About the roastery</a>
  <a href="https://developer.mozilla.org/">Web notes</a>
</body>
</html>
"""


class AuditError(Exception):
    pass


def _private_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _private_host(hostname: str) -> bool:
    host = hostname.strip("[]").lower()
    if host in {"localhost", "0.0.0.0", "::1", "metadata.google.internal"}:
        return True
    if host.endswith(".localhost") or host.endswith(".local") or host.endswith(".onion"):
        return True
    return _private_ip(host)


def _assert_public(hostname: str) -> None:
    if not hostname or _private_host(hostname):
        raise AuditError("That address cannot be audited.")
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return
    for info in infos:
        ip = info[4][0]
        if _private_ip(ip):
            raise AuditError("That address cannot be audited.")


def normalize_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise AuditError("Enter a website URL to start the audit.")
    if re.search(r"\s", text) and "://" not in text:
        raise AuditError("The URL cannot contain spaces.")
    candidate = text if re.match(r"^https?://", text, re.I) else f"https://{text}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise AuditError("Only http and https websites can be audited.")
    if not parsed.hostname:
        raise AuditError("That does not look like a valid URL. Try something like example.com")
    if parsed.username or parsed.password:
        raise AuditError("URLs with usernames or passwords cannot be audited.")
    if _private_host(parsed.hostname):
        raise AuditError("That address cannot be audited.")
    return parsed.geturl()


class _Redirect(Exception):
    def __init__(self, location: str):
        self.location = location


class _RedirectHandler:
    """urllib opener hook: do not follow redirects automatically."""

    def __init__(self):
        from urllib.request import HTTPRedirectHandler

        class Handler(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise _Redirect(urljoin(req.full_url, newurl))

        self.handler = Handler

    def opener(self):
        return build_opener(self.handler())


def _fetch(url: str, accept: str) -> dict:
    started = time.perf_counter()
    current = url
    status = 0
    body = ""
    content_type = ""
    final = url
    opener = _RedirectHandler().opener()
    for hop in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        _assert_public(parsed.hostname or "")
        req = Request(
            current,
            headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en"},
        )
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                status = getattr(resp, "status", 200)
                content_type = resp.headers.get("Content-Type", "")
                length = int(resp.headers.get("Content-Length") or "0")
                if length > MAX_BYTES:
                    raise AuditError("The page is too large to analyse in this project.")
                raw = resp.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise AuditError("The page is too large to analyse in this project.")
                charset = "utf-8"
                match = re.search(r"charset=([^;]+)", content_type, re.I)
                if match:
                    charset = match.group(1).strip("\"' ")
                body = raw.decode(charset, errors="replace")
                final = resp.geturl()
                break
        except _Redirect as redir:
            if hop >= MAX_REDIRECTS:
                raise AuditError("The website redirected too many times.")
            current = redir.location
            final = current
            continue
        except HTTPError as err:
            status = err.code
            content_type = err.headers.get("Content-Type", "") if err.headers else ""
            raw = err.read(MAX_BYTES + 1) if err.fp else b""
            body = raw[:MAX_BYTES].decode("utf-8", errors="replace")
            final = current
            break
        except AuditError:
            raise
        except (URLError, TimeoutError, socket.timeout):
            raise AuditError("The website could not be reached. Check the URL and try again.")
        except Exception:
            raise AuditError("The website could not be reached. Check the URL and try again.")
    return {
        "url": final,
        "status": status,
        "ok": 200 <= status < 300,
        "body": body,
        "content_type": content_type,
        "ms": int((time.perf_counter() - started) * 1000),
    }


def fetch_page(raw_url: str) -> dict:
    target = normalize_url(raw_url)
    page = _fetch(target, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
    if page["status"] == 404:
        raise AuditError("The page was not found (404).")
    if page["status"] in {401, 403}:
        raise AuditError(
            "The website blocked the audit request. Some sites do not allow automated checks."
        )
    if page["status"] >= 400:
        raise AuditError(f"The website returned HTTP {page['status']} and could not be analysed.")
    head = page["body"][:4000]
    if not re.search(r"html|xml|text/plain", page["content_type"], re.I) and not re.search(
        r"<html|<head|<title|<body", head, re.I
    ):
        raise AuditError("That URL did not return a webpage we can read.")
    return page


def _strip(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.meta = None
        self.lang = None
        self.base = None
        self.headings = {"h1": [], "h2": [], "h3": []}
        self.images = []
        self.links = []
        self._capture = None
        self._buf = []
        self._skip = 0
        self._skip_tag = ""

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        data = {k.lower(): v for k, v in attrs}
        if t in {"script", "style", "noscript"}:
            self._skip += 1
            self._skip_tag = t
            return
        if self._skip:
            return
        if t == "html" and self.lang is None:
            self.lang = data.get("lang")
        if t == "base" and data.get("href"):
            self.base = data.get("href")
        if t == "meta" and self.meta is None:
            name = (data.get("name") or data.get("property") or "").lower()
            if name in {"description", "og:description"} and data.get("content"):
                self.meta = data["content"].strip()
        if t == "img" and len(self.images) < 200:
            has_alt = "alt" in data
            self.images.append(
                {
                    "src": data.get("src") or data.get("data-src") or "",
                    "alt": data.get("alt") if has_alt else None,
                    "hasAltAttr": has_alt,
                }
            )
        if t == "a" and len(self.links) < 400:
            self.links.append({"href": data.get("href") or "", "text": ""})
            self._capture = "a"
            self._buf = []
            return
        if t in {"h1", "h2", "h3"} and len(self.headings[t]) < 40:
            self._capture = t
            self._buf = []
        if t == "title" and self.title is None:
            self._capture = "title"
            self._buf = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in {"script", "style", "noscript"} and self._skip and t == self._skip_tag:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip or self._capture is None:
            return
        if t == self._capture or (self._capture == "a" and t == "a") or (
            self._capture == "title" and t == "title"
        ):
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if self._capture == "title":
                self.title = text or None
            elif self._capture == "a" and self.links:
                self.links[-1]["text"] = text[:120]
            elif self._capture in self.headings and text:
                self.headings[self._capture].append(text[:240])
            self._capture = None
            self._buf = []

    def handle_data(self, data):
        if self._skip or self._capture is None:
            return
        self._buf.append(data)


def parse_html(source: str) -> dict:
    parser = PageParser()
    try:
        parser.feed(source)
        parser.close()
    except Exception:
        pass
    return {
        "title": parser.title,
        "metaDescription": parser.meta,
        "lang": parser.lang,
        "headings": parser.headings,
        "images": parser.images,
        "links": parser.links,
        "baseHref": parser.base,
    }


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.").lower()
    except Exception:
        return ""


def _resolve(href: str, page_url: str, base: str | None):
    trimmed = href.strip()
    lower = trimmed.lower()
    if not trimmed or lower.startswith(("javascript:", "mailto:", "tel:", "data:")) or trimmed.startswith("#"):
        return None
    try:
        base_url = urljoin(page_url, base) if base else page_url
        return urljoin(base_url, trimmed)
    except Exception:
        return None


def check_title(page):
    why = "The title is the clickable headline in search results. It should name the page clearly."
    title = page["title"]
    if not title:
        return _check("title", "Page title", "on-page", "error", 0, 15, "No title tag was found.", why,
                      "Add a unique <title> between 30 and 60 characters that describes the page topic.")
    length = len(title)
    items = [{"label": "Title", "value": title}, {"label": "Length", "value": f"{length} characters"}]
    if length < 30:
        return _check("title", "Page title", "on-page", "warning", 9, 15, f"Title is short ({length} characters).", why,
                      "Lengthen the title so it clearly describes the page. Aim for 30–60 characters.", items)
    if length > 60:
        score = 8 if length > 70 else 11
        return _check("title", "Page title", "on-page", "warning", score, 15,
                      f"Title is long ({length} characters). Search results may cut it off.", why,
                      "Shorten the title to about 50–60 characters so it fits in search results.", items)
    return _check("title", "Page title", "on-page", "pass", 15, 15, f"Title looks well sized ({length} characters).", why, items=items)


def check_meta(page):
    why = "The meta description is the short summary under the title in search results. It helps people decide whether to click."
    desc = page["metaDescription"]
    if not desc:
        return _check("meta", "Meta description", "on-page", "error", 0, 15, "No meta description was found.", why,
                      "Add a meta description of about 70–160 characters that explains what the page offers.")
    length = len(desc)
    items = [{"label": "Description", "value": desc}, {"label": "Length", "value": f"{length} characters"}]
    if length < 70:
        return _check("meta", "Meta description", "on-page", "warning", 9, 15, f"Description is short ({length} characters).", why,
                      "Write a fuller description so search users understand the page at a glance.", items)
    if length > 160:
        score = 8 if length > 180 else 11
        return _check("meta", "Meta description", "on-page", "warning", score, 15,
                      f"Description is long ({length} characters) and may be trimmed.", why,
                      "Keep the meta description around 150–160 characters.", items)
    return _check("meta", "Meta description", "on-page", "pass", 15, 15, f"Description looks well sized ({length} characters).", why, items=items)


def check_headings(page):
    why = "Headings organise the page. One H1 should name the main topic. H2 and H3 should follow in a sensible order."
    h1, h2, h3 = page["headings"]["h1"], page["headings"]["h2"], page["headings"]["h3"]
    items = [
        {"label": "H1 headings", "value": str(len(h1)), "ok": len(h1) == 1},
        {"label": "H2 headings", "value": str(len(h2)), "ok": len(h2) > 0},
        {"label": "H3 headings", "value": str(len(h3))},
    ]
    if h1:
        items.append({"label": "Main H1", "value": h1[0]})
    if not h1:
        return _check("headings", "Heading structure", "on-page", "error", 0, 15, "No H1 heading was found.", why,
                      "Add one H1 that states the main topic of the page.", items)
    score = 8
    status = "pass"
    notes = []
    if len(h1) == 1:
        notes.append("One H1 is present.")
    else:
        score -= 3
        status = "warning"
        notes.append(f"{len(h1)} H1 headings were found. One is usually enough.")
    if h2:
        score += 4
        notes.append("H2 headings are present.")
    else:
        status = "warning"
        notes.append("No H2 headings were found.")
    if h3 and not h2:
        status = "warning"
        notes.append("H3 headings appear without H2 headings.")
    else:
        score += 3
    score = max(4, min(15, score))
    if len(h1) != 1 or not h2:
        status = "warning"
    return _check(
        "headings", "Heading structure", "on-page", status, score, 15, notes[0], why,
        None if status == "pass" else "Use a single H1, then H2 sections, then H3 subsections. Avoid skipping levels.",
        items,
    )


def check_images(page):
    why = "Alternative text describes images for screen readers and helps search engines understand pictures."
    images = page["images"]
    if not images:
        return _check("images", "Image alt text", "on-page", "pass", 15, 15, "No images were found on this page.", why)
    missing = [img for img in images if not img["hasAltAttr"]]
    empty = [img for img in images if img["hasAltAttr"] and not (img["alt"] or "").strip()]
    ok_count = len(images) - len(missing) - len(empty)
    items = [{"label": "Missing alt", "value": img["src"] or "(no src)", "ok": False} for img in missing[:8]]
    if not missing and not empty:
        return _check("images", "Image alt text", "on-page", "pass", 15, 15, f"All {len(images)} images include alternative text.", why)
    if len(missing) == len(images):
        return _check("images", "Image alt text", "on-page", "error", 0, 15, f"None of the {len(images)} images have alt text.", why,
                      "Add a short, useful alt attribute to every meaningful image.", items)
    ratio = ok_count / len(images)
    return _check(
        "images", "Image alt text", "on-page", "warning", max(4, round(15 * ratio)), 15,
        f"{len(missing) + len(empty)} of {len(images)} images need better alt text.", why,
        'Add descriptive alt text to images that convey information. Decorative images can use empty alt="".',
        items,
    )


def check_links(page, page_url):
    why = "Links connect pages. Internal links help people and search engines move around the site. External links can support related sources."
    page_host = _host(page_url)
    internal = external = empty = 0
    samples = []
    for link in page["links"]:
        href = link["href"].strip()
        if not href or href == "#":
            empty += 1
            continue
        resolved = _resolve(link["href"], page_url, page["baseHref"])
        if not resolved:
            continue
        kind = "internal" if _host(resolved) == page_host else "external"
        if kind == "internal":
            internal += 1
        else:
            external += 1
        if len(samples) < 6:
            samples.append({"label": "Internal" if kind == "internal" else "External", "value": resolved[:180]})
    items = [
        {"label": "Internal links", "value": str(internal)},
        {"label": "External links", "value": str(external)},
        {"label": "Empty or # links", "value": str(empty), "ok": empty == 0},
        *samples,
    ]
    if internal + external == 0:
        return _check("links", "Internal and external links", "structure", "warning", 4, 10,
                      "No crawlable links were found on the page.", why,
                      "Add useful internal links so visitors can reach other pages on the site.", items)
    score = (5 if internal else 0) + (3 if external else 0) + (2 if empty == 0 else 1)
    status = "warning" if internal == 0 or empty > 0 else "pass"
    return _check(
        "links", "Internal and external links", "structure", status, score, 10,
        f"{internal} internal and {external} external links were found.", why,
        None if status == "pass" else "Add a few clear internal links, and avoid empty or # only href values.",
        items,
    )


def check_url(page_url):
    why = "A clean HTTPS URL is easier to share, trust, and understand. Search engines also prefer secure pages."
    parsed = urlparse(page_url)
    items = [
        {"label": "Protocol", "value": parsed.scheme, "ok": parsed.scheme == "https"},
        {"label": "Host", "value": parsed.hostname or ""},
        {"label": "Path", "value": parsed.path or "/"},
    ]
    score = 0
    notes = []
    status = "pass"
    if parsed.scheme == "https":
        score += 5
        notes.append("The page is served over HTTPS.")
    else:
        status = "error"
        notes.append("The page is not using HTTPS.")
    path = parsed.path or ""
    host = parsed.hostname or ""
    is_ip = bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host))
    messy = is_ip or "_" in path or len(path) > 80 or re.search(r"[A-Z]", path) or len([p for p in path.split("/") if p]) > 5
    if not messy:
        score += 5
        notes.append("The URL path looks tidy.")
    else:
        score += 2
        status = "error" if status == "error" else "warning"
        if is_ip:
            notes.append("The host looks like an IP address rather than a domain name.")
        if "_" in path:
            notes.append("The path uses underscores. Hyphens are usually easier to read.")
        if len(path) > 80:
            notes.append("The path is quite long.")
        if re.search(r"[A-Z]", path):
            notes.append("The path includes capital letters.")
        if len([p for p in path.split("/") if p]) > 5:
            notes.append("The path is nested quite deeply.")
    return _check(
        "url", "URL structure", "structure", status, score, 10, notes[0], why,
        None if status == "pass" else "Prefer HTTPS, a real domain name, and a short lowercase path with hyphens.",
        items,
    )


def check_robots(tech):
    why = "robots.txt tells search engines which parts of the site they may crawl. It is a basic technical file at the site root."
    robots = tech["robots"]
    if robots["found"]:
        items = [
            {"label": "Location", "value": "/robots.txt", "ok": True},
            {"label": "Sitemaps listed", "value": str(len(robots["sitemapUrls"]))},
        ]
        if robots["snippet"]:
            items.append({"label": "Preview", "value": robots["snippet"][:220]})
        return _check("robots", "robots.txt", "technical", "pass", 10, 10, "robots.txt is available at the site root.", why, items=items)
    return _check("robots", "robots.txt", "technical", "error", 0, 10, "robots.txt was not found at the expected location.", why,
                  "Add a robots.txt file at https://your-domain/robots.txt.")


def check_sitemap(tech):
    why = "A sitemap lists important pages so search engines can discover them more easily."
    sitemap = tech["sitemap"]
    robots = tech["robots"]
    if sitemap["found"]:
        return _check("sitemap", "sitemap.xml", "technical", "pass", 10, 10, "A sitemap file was found.", why,
                      items=[{"label": "Location", "value": sitemap["url"] or "/sitemap.xml", "ok": True}])
    if robots["sitemapUrls"]:
        return _check("sitemap", "sitemap.xml", "technical", "warning", 6, 10,
                      "robots.txt lists a sitemap, but the usual sitemap.xml URL did not respond.", why,
                      "Confirm the sitemap URL in robots.txt is reachable.",
                      [{"label": "Listed sitemap", "value": u} for u in robots["sitemapUrls"][:3]])
    return _check("sitemap", "sitemap.xml", "technical", "error", 0, 10, "sitemap.xml was not found at the expected location.", why,
                  "Publish a sitemap.xml file at the site root, and mention it in robots.txt.")


def _check(cid, title, category, status, score, max_score, summary, why, recommendation=None, items=None):
    row = {
        "id": cid,
        "title": title,
        "category": category,
        "status": status,
        "score": score,
        "maxScore": max_score,
        "summary": summary,
        "why": why,
    }
    if recommendation:
        row["recommendation"] = recommendation
    if items:
        row["items"] = items
    return row


def _looks_robots(body: str) -> bool:
    return bool(re.search(r"^\s*(user-agent|sitemap|disallow|allow|crawl-delay)\s*:", body, re.I | re.M))


def _looks_sitemap(body: str) -> bool:
    return bool(re.search(r"<urlset|<sitemapindex|<\?xml", body, re.I))


def technical_files(page_url: str) -> dict:
    parsed = urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    robots = {"found": False, "status": None, "sitemapUrls": [], "snippet": None}
    sitemap = {"found": False, "status": None, "url": None}
    candidates = [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]
    try:
        res = _fetch(f"{origin}/robots.txt", "text/plain,*/*;q=0.8")
        robots["status"] = res["status"]
        if res["ok"] and _looks_robots(res["body"]):
            urls = []
            for line in res["body"].splitlines():
                match = re.match(r"^\s*sitemap\s*:\s*(\S+)", line, re.I)
                if match:
                    urls.append(match.group(1).strip())
            robots = {"found": True, "status": res["status"], "sitemapUrls": urls, "snippet": res["body"][:400]}
            for extra in urls:
                if extra not in candidates:
                    candidates.append(extra)
    except AuditError:
        pass
    for candidate in candidates[:4]:
        try:
            res = _fetch(candidate, "application/xml,text/xml,*/*;q=0.8")
            if res["ok"] and _looks_sitemap(res["body"]):
                sitemap = {"found": True, "status": res["status"], "url": res["url"]}
                break
            sitemap["status"] = res["status"]
        except AuditError:
            continue
    return {"robots": robots, "sitemap": sitemap}


def _grade(score: int) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Needs work"
    return "Poor"


def _status(score: int, checks: list) -> str:
    errors = sum(1 for c in checks if c["status"] == "error")
    if score < 50 or errors >= 3:
        return "error"
    if score < 85 or errors > 0 or any(c["status"] == "warning" for c in checks):
        return "warning"
    return "pass"


def _recommendations(checks):
    out = []
    for status in ("error", "warning"):
        for check in checks:
            if check["status"] == status and check.get("recommendation"):
                out.append({
                    "title": check["title"],
                    "body": check["recommendation"],
                    "checkId": check["id"],
                    "priority": status,
                })
    return out


def _count_links(page, page_url):
    internal = external = 0
    page_host = _host(page_url)
    for link in page["links"]:
        href = link["href"].strip()
        if not href or href.startswith("#") or re.match(r"^(javascript|mailto|tel):", href, re.I):
            continue
        resolved = _resolve(href, page_url, page["baseHref"])
        if not resolved:
            continue
        if _host(resolved) == page_host:
            internal += 1
        else:
            external += 1
    return internal, external


def assemble(requested, fetched_url, status_code, fetch_ms, sample, page, tech):
    checks = [
        check_title(page),
        check_meta(page),
        check_headings(page),
        check_images(page),
        check_links(page, fetched_url),
        check_url(fetched_url),
        check_robots(tech),
        check_sitemap(tech),
    ]
    score = round(sum(c["score"] for c in checks))
    internal, external = _count_links(page, fetched_url)
    recs = _recommendations(checks)
    return {
        "id": str(uuid.uuid4()),
        "url": requested,
        "normalizedUrl": fetched_url,
        "fetchedUrl": fetched_url,
        "statusCode": status_code,
        "fetchMs": fetch_ms,
        "analyzedAt": datetime.now(timezone.utc).isoformat(),
        "sample": sample,
        "score": score,
        "grade": _grade(score),
        "status": _status(score, checks),
        "passed": sum(1 for c in checks if c["status"] == "pass"),
        "warnings": sum(1 for c in checks if c["status"] == "warning"),
        "errors": sum(1 for c in checks if c["status"] == "error"),
        "checks": checks,
        "recommendations": recs,
        "page": {
            "title": page["title"],
            "metaDescription": page["metaDescription"],
            "h1Count": len(page["headings"]["h1"]),
            "h2Count": len(page["headings"]["h2"]),
            "h3Count": len(page["headings"]["h3"]),
            "imageCount": len(page["images"]),
            "missingAltCount": sum(1 for img in page["images"] if not img["hasAltAttr"]),
            "internalLinks": internal,
            "externalLinks": external,
            "robotsFound": tech["robots"]["found"],
            "sitemapFound": tech["sitemap"]["found"],
        },
    }


def run_live(raw_url: str) -> dict:
    page_res = fetch_page(raw_url)
    parsed = parse_html(page_res["body"])
    tech = technical_files(page_res["url"])
    return assemble(raw_url.strip(), page_res["url"], page_res["status"], page_res["ms"], False, parsed, tech)


def run_sample() -> dict:
    parsed = parse_html(SAMPLE_HTML)
    tech = {
        "robots": {
            "found": True,
            "status": 200,
            "sitemapUrls": [f"{SAMPLE_URL}sitemap.xml"],
            "snippet": "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml",
        },
        "sitemap": {"found": True, "status": 200, "url": f"{SAMPLE_URL}sitemap.xml"},
    }
    return assemble(SAMPLE_URL, SAMPLE_URL, 200, 42, True, parsed, tech)
