"""Fetchers for Workable, SmartRecruiters, Recruitee, Personio and Teamtailor.

Same contract as the fetchers in fetch_jobs.py: take a company entry from
companies.json, return a list of job dicts. All four use public endpoints.

Workable and SmartRecruiters only list titles and locations; the description
needs one extra request per posting. To keep that cheap, postings whose title
is already rejected by the company's title_include / title_exclude are dropped
before any description is fetched.
"""
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor

TAG_RE = re.compile(r"<[^>]+>")
DETAIL_WORKERS = 8
DEFAULT_TITLE_INCLUDE = ["engineer", "developer", "architect"]


def _request(url, data=None, headers=None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": "Mozilla/5.0",
        **({"Content-Type": "application/json"} if data is not None else {}),
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def get_json(url, data=None):
    return json.loads(_request(url, data))


def strip_html(html):
    return TAG_RE.sub(" ", html or "")


def _title_plausible(title, company):
    """Cheap title check done before paying for a description request."""
    t = title.lower()
    include = company.get("title_include", DEFAULT_TITLE_INCLUDE)
    exclude = company.get("title_exclude", [])
    return any(x in t for x in include) and not any(x in t for x in exclude)


def _detail(url):
    """One posting's description, retried once. {} when it still fails (the job is flagged no_description)."""
    for _ in range(2):
        try:
            return get_json(url)
        except Exception:
            pass
    return {}


def _details(items, fetch_one):
    """Run fetch_one over items in parallel, keeping order."""
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        return list(pool.map(fetch_one, items))


def fetch_workable(company):
    slug = company["slug"]
    base = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    listing, token = [], None
    while True:
        payload = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            payload["token"] = token
        data = get_json(base, payload)
        listing += data.get("results", [])
        token = data.get("nextPage")
        if not token:
            break

    listing = [j for j in listing if _title_plausible(j["title"], company)]

    def one(j):
        detail = _detail(f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{j['shortcode']}")
        loc = j.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        return {
            "company": company["name"],
            "title": j["title"].strip(),
            "department": (j.get("department") or [""])[0],
            "team": "",
            "location": location,
            "remote": (j.get("workplace") == "remote") if j.get("workplace") else bool(j.get("remote")),
            "workplace": (j.get("workplace") or "").replace("_", "-"),
            "published": (j.get("published") or "")[:10],
            "compensation": None,
            "no_description": not detail,
            "text": f"{j['title']} " + strip_html(
                f"{detail.get('description', '')} {detail.get('requirements', '')}"),
            "url": f"https://apply.workable.com/{slug}/j/{j['shortcode']}/",
        }

    return _details(listing, one)


def fetch_smartrecruiters(company):
    slug = company["slug"]
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    listing, offset = [], 0
    while True:
        data = get_json(f"{base}?limit=100&offset={offset}")
        listing += data.get("content", [])
        offset += 100
        if offset >= data.get("totalFound", 0) or not data.get("content"):
            break

    listing = [j for j in listing if _title_plausible(j["name"], company)]

    def one(j):
        detail = _detail(f"{base}/{j['id']}")
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        text = " ".join(strip_html((s or {}).get("text", "")) for s in sections.values())
        loc = j.get("location") or {}
        return {
            "company": company["name"],
            "title": j["name"].strip(),
            "department": (j.get("department") or {}).get("label", ""),
            "team": (j.get("function") or {}).get("label", ""),
            "location": loc.get("fullLocation") or loc.get("city", ""),
            "remote": bool(loc.get("remote")) and not loc.get("hybrid"),
            "workplace": "hybrid" if loc.get("hybrid") else ("remote" if loc.get("remote") else ""),
            "published": (j.get("releasedDate") or "")[:10],
            "compensation": None,
            "no_description": not detail,
            "text": f"{j['name']} {text}",
            "url": detail.get("postingUrl") or f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
        }

    return _details(listing, one)


def fetch_recruitee(company):
    slug = company["slug"]
    data = get_json(f"https://{slug}.recruitee.com/api/offers/")
    jobs = []
    for j in data.get("offers", []):
        salary = j.get("salary") or {}
        compensation = None
        if salary.get("min") or salary.get("max"):
            compensation = f"{salary.get('min') or '?'} - {salary.get('max') or '?'} {salary.get('currency') or ''}".strip()
        jobs.append({
            "company": company["name"],
            "title": j["title"].strip(),
            "department": j.get("department") or "",
            "team": "",
            "location": j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x),
            "remote": bool(j.get("remote")),
            "published": (j.get("published_at") or j.get("created_at") or "")[:10],
            "compensation": compensation,
            "text": f"{j['title']} " + strip_html(f"{j.get('description', '')} {j.get('requirements', '')}"),
            "url": j.get("careers_url") or f"https://{slug}.recruitee.com/o/{j.get('slug', '')}",
        })
    return jobs


def fetch_personio(company):
    slug = company["slug"]
    root = ET.fromstring(_request(f"https://{slug}.jobs.personio.de/xml?language=en"))
    jobs = []
    for p in root.iter("position"):
        title = (p.findtext("name") or "").strip()
        offices = [p.findtext("office") or ""] + [o.text or "" for o in p.iter("office") if o is not p.find("office")]
        location = "; ".join(dict.fromkeys(o for o in offices if o))
        descriptions = " ".join(
            strip_html((d.findtext("value") or "")) for d in p.iter("jobDescription"))
        text = f"{title} {descriptions}"
        jobs.append({
            "company": company["name"],
            "title": title,
            "department": p.findtext("department") or "",
            "team": p.findtext("recruitingCategory") or "",
            "location": location,
            "remote": bool(re.search(r"\bremote\b", f"{location} {title}".lower())),
            "published": (p.findtext("createdAt") or "")[:10],
            "compensation": None,
            "text": text,
            "url": f"https://{slug}.jobs.personio.de/job/{p.findtext('id')}",
        })
    return jobs


def fetch_teamtailor(company):
    """Teamtailor career sites publish every opening in a public RSS feed.

    slug is either the Teamtailor subdomain ("acme" for acme.teamtailor.com)
    or the full domain of a custom career site ("careers.acme.com").
    """
    slug = company["slug"]
    host = slug if "." in slug else f"{slug}.teamtailor.com"
    ns = {"tt": "https://teamtailor.com/locations"}
    root = ET.fromstring(_request(f"https://{host}/jobs.rss"))
    jobs = []
    for item in root.iter("item"):
        places = []
        for loc in item.findall("tt:locations/tt:location", ns):
            name = ", ".join(x for x in (loc.findtext("tt:city", namespaces=ns),
                                         loc.findtext("tt:country", namespaces=ns)) if x)
            if name and name not in places:
                places.append(name)
        status = (item.findtext("remoteStatus") or "none").lower()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate")).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            published = ""
        title = (item.findtext("title") or "").strip()
        jobs.append({
            "company": company["name"],
            "title": title,
            "department": item.findtext("tt:department", namespaces=ns) or "",
            "team": item.findtext("tt:role", namespaces=ns) or "",
            "location": "; ".join(places) + (" (hybrid)" if status == "hybrid" else ""),
            "remote": status == "fully",
            "published": published,
            "compensation": None,
            "text": f"{title} " + strip_html(item.findtext("description") or ""),
            "url": item.findtext("link") or "",
        })
    return jobs


FETCHERS = {
    "workable": fetch_workable,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "personio": fetch_personio,
    "teamtailor": fetch_teamtailor,
}
