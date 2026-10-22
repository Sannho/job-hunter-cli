"""Fetchers for the career sites of large companies: Jibe and Phenom.

Big groups rarely use the startup ATS. Their career sites are built on
platforms that expose the search behind the page as a JSON endpoint:

- jibe   iCIMS Career Sites (AXA and others): GET  <site>/api/jobs
- phenom Phenom People (Allianz and others):  POST <site>/widgets

Both are the same calls the career page makes in your browser. Use "query" in
the company entry to let the site filter server-side (for example "engineer").
Without it every posting of the group is downloaded, which can be thousands.
"query" can also be a list ("ingénieur", "développeur"): each search is run and merged.
"""
import json
import re
import urllib.parse
import urllib.request

from more_ats import strip_html

MAX_POSTINGS = 3000
HYBRID_RE = re.compile(r"hybrid|hybride|t[ée]l[ée]travail", re.I)
REMOTE_RE = re.compile(r"\bremote\b|t[ée]l[ée]travail", re.I)


def _call(url, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.load(resp)


def _host(slug):
    """Accept 'jobs.axa.com' or a full URL; return the bare host."""
    return urllib.parse.urlparse(slug if "//" in slug else f"https://{slug}").netloc


def fetch_jibe(company):
    host = _host(company["slug"])
    query = company.get("query", "")
    seen, jobs, page = set(), [], 1
    while len(seen) < MAX_POSTINGS:
        params = {"limit": 100, "page": page}
        if query:
            params["keywords"] = query
        data = _call(f"https://{host}/api/jobs?{urllib.parse.urlencode(params)}")
        batch = [item["data"] for item in data.get("jobs", [])]
        if not batch:
            break
        for j in batch:
            slug = j.get("slug") or j.get("req_id")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            categories = j.get("categories") or []
            title = (j.get("title") or "").strip()
            location = j.get("full_location") or j.get("short_location") or j.get("city") or ""
            jobs.append({
                "company": company["name"],
                "title": title,
                "department": (categories[0].get("name", "") if categories else ""),
                "team": "",
                "location": location,
                "remote": bool(REMOTE_RE.search(f"{title} {location}")),
                "workplace": "hybrid" if HYBRID_RE.search(strip_html(j.get("description", ""))) else "",
                "contract": " ".join(j.get("tags2") or []),
                "published": (j.get("posted_date") or j.get("create_date") or "")[:10],
                "compensation": None,
                "text": f"{title} {strip_html(j.get('description', ''))}",
                "url": f"https://{host}/careers-home/jobs/{slug}?lang={j.get('language', 'en-us')}",
            })
        page += 1
        if len(batch) < 100:
            break
    return jobs


def fetch_phenom(company):
    host = _host(company["slug"])
    path = company.get("path", "global/en")  # <country>/<language> segment of the career site
    lang = company.get("lang", "en_global")
    country = company.get("country", "global")
    query = company.get("query", "")
    jobs, start, total = [], 0, None
    while (total is None or start < total) and start < MAX_POSTINGS:
        payload = {
            "lang": lang, "deviceType": "desktop", "country": country,
            "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "",
            "subsearch": "", "from": start, "jobs": True, "counts": True,
            "all_fields": ["category", "country", "city"], "size": 50, "clearAll": False,
            "jdsource": "facets", "isSliderEnable": False,
            "pageId": company.get("page_id", "page11"), "siteType": "external",
            "keywords": query, "global": True, "selected_fields": {},
            "sort": {"order": "desc", "field": "postedDate"}, "locationData": {},
        }
        result = _call(f"https://{host}/widgets", payload)["refineSearch"]
        total = result.get("totalHits", 0)
        batch = result.get("data", {}).get("jobs", [])
        if not batch:
            break
        for j in batch:
            title = (j.get("title") or "").strip()
            places = j.get("multi_location") or [j.get("location") or ""]
            location = "; ".join(dict.fromkeys(p for p in places if p))
            remote_label = (j.get("remote") or "").lower()
            skills = " ".join(j.get("ml_skills") or [])
            jobs.append({
                "company": company["name"],
                "title": title,
                "department": j.get("category") or "",
                "team": j.get("unit") or "",
                "location": location,
                "remote": "remote" in remote_label and "hybrid" not in remote_label,
                "contract": j.get("employmentType") or j.get("type") or "",
                "published": (j.get("postedDate") or "")[:10],
                "compensation": None,
                "text": f"{title} {j.get('descriptionTeaser', '')} {skills} {j.get('category', '')}",
                "url": f"https://{host}/{path}/job/{j.get('jobId') or j.get('reqId')}",
            })
        start += len(batch)
    return jobs


def _for_each_query(fetch):
    """Let "query" be a list: run each search and merge the results by URL."""
    def wrapper(company):
        queries = company.get("query", "")
        queries = [queries] if isinstance(queries, str) else list(queries)
        merged = {}
        for q in queries or [""]:
            for job in fetch(dict(company, query=q)):
                merged.setdefault(job["url"], job)
        return list(merged.values())
    return wrapper


FETCHERS = {
    "jibe": _for_each_query(fetch_jibe),
    "phenom": _for_each_query(fetch_phenom),
}
