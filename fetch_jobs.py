"""Generic multi-company job search: fetch openings, filter dev roles, score against profile.

Add a company by appending an entry to companies.json (copy companies.example.json first). Currently supports
Ashby-backed job boards (ats: "ashby") - the same public API Docker uses
(https://api.ashbyhq.com/posting-api/job-board/<slug>). To find a company's
slug: open their careers page, look for a script/iframe pointing at
jobs.ashbyhq.com/<slug>.

Other ATS providers (Greenhouse, Lever, ...) can be added later by writing
a fetch_<ats>(company) function with the same return shape and registering
it in FETCHERS below.
"""
import hashlib
import json
import re
import argparse
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import ui
from corp_ats import FETCHERS as CORP_FETCHERS
from more_ats import FETCHERS as MORE_FETCHERS

TRACKING_PATH = "applications.json"


def job_id(url):
    """Short, stable id derived from the posting URL, used by track.py."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]


def load_tracking(path=TRACKING_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

DEFAULT_DEV_DEPARTMENTS = {"engineering", "security"}
DEFAULT_TITLE_INCLUDE = ["engineer", "developer", "architect"]
DEFAULT_TITLE_EXCLUDE = [
    "solutions engineer", "solutions architect", "sales engineer",
    "implementation engineer", "customer experience engineer", "field cto",
]

TAG_RE = re.compile(r"<[^>]+>")
# Whole word only: "International" and "Internal" are not internships.
INTERN_RE = re.compile(r"\bintern(ship)?s?\b")


def strip_html(html):
    return TAG_RE.sub(" ", html or "")


def fetch_ashby(company):
    slug = company["slug"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)

    jobs = []
    for j in data["jobs"]:
        compensation = j.get("compensation") or {}
        jobs.append({
            "company": company["name"],
            "title": j["title"].strip(),
            "department": j.get("department", ""),
            "team": j.get("team", ""),
            "location": j.get("location", ""),
            # Ashby sets isRemote on hybrid postings too; workplaceType tells them apart.
            "remote": (j.get("workplaceType") or "").lower() == "remote"
                      if j.get("workplaceType") else bool(j.get("isRemote")),
            "workplace": (j.get("workplaceType") or "").lower(),
            "published": j["publishedAt"][:10],
            "compensation": (
                compensation.get("scrapeableCompensationSalarySummary")
                or compensation.get("compensationTierSummary")
            ),
            "text": f"{j['title']} {j.get('team', '')} {strip_html(j.get('descriptionHtml', ''))}",
            "url": j["jobUrl"],
        })
    return jobs


def fetch_greenhouse(company):
    slug = company["slug"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)

    jobs = []
    for j in data["jobs"]:
        departments = j.get("departments") or []
        location = (j.get("location") or {}).get("name", "")
        jobs.append({
            "company": company["name"],
            "title": j["title"].strip(),
            "department": departments[0]["name"] if departments else "",
            "team": "",
            "location": location,
            # Canonical and a few others say "Home based" instead of "Remote".
            "remote": any(term in location.lower() for term in ("remote", "home based")),
            "published": (j.get("first_published") or j.get("updated_at", ""))[:10],
            "compensation": None,
            "text": f"{j['title']} {strip_html(j.get('content', ''))}",
            "url": j["absolute_url"],
        })
    return jobs


def fetch_lever(company):
    slug = company["slug"]
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)

    jobs = []
    for j in data:
        categories = j.get("categories") or {}
        jobs.append({
            "company": company["name"],
            "title": j["text"].strip(),
            "department": categories.get("team", ""),
            "team": categories.get("team", ""),
            "location": categories.get("location", ""),
            "remote": j.get("workplaceType") == "remote"
                      or "remote" in (categories.get("location") or "").lower(),
            "published": datetime.fromtimestamp(j["createdAt"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
            "compensation": None,
            "text": f"{j['text']} {strip_html(j.get('descriptionPlain', j.get('description', '')))}",
            "url": j["hostedUrl"],
        })
    return jobs


FETCHERS = {
    "ashby": fetch_ashby,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    **MORE_FETCHERS,
    **CORP_FETCHERS,
}


def workplace(job):
    """'remote', 'hybrid' or 'on-site', from the board's own field when it has one."""
    declared = (job.get("workplace") or "").lower()
    if declared in ("remote", "hybrid"):
        return declared
    if job.get("remote"):
        return "remote"
    return "hybrid" if "(hybrid)" in (job.get("location") or "").lower() else "on-site"


def is_listed(job):
    """Whether a posting belongs in the report and the picker.

    Remote postings always do. Companies flagged include_onsite in
    companies.json are listed too.
    """
    return bool(job.get("remote") or job.get("include_onsite"))


# Countries/terms excluded outright, wherever they appear in a location
# string (even alongside other, allowed countries).
LOCATION_EXCLUDE_TERMS = ["india", "canada", "in-office", "seattle", "americas", "hybrid"]


# Places a posting may be open to. Matched as whole words, case-insensitive,
# against each location listed on a posting. Override with "locations" in
# profile.json (e.g. ["france", "paris"] for France only).
DEFAULT_LOCATIONS = [
    "europe", "european union", "eu", "emea",
    "france", "paris", "lyon", "toulouse", "nantes", "bordeaux", "lille",
    "united kingdom", "uk", "england", "london", "ireland", "ie", "dublin",
    "germany", "berlin", "munich", "netherlands", "amsterdam", "belgium", "brussels",
    "spain", "madrid", "barcelona", "portugal", "lisbon", "italy", "milan",
    "switzerland", "ch", "zurich", "luxembourg", "austria", "vienna",
    "poland", "sweden", "stockholm", "denmark", "copenhagen", "norway",
    "finland", "helsinki", "czech", "romania", "estonia", "lithuania",
]
# A location that says nothing but this is open to anyone.
OPEN_LOCATIONS = {"remote", "anywhere", "worldwide", "global", "distributed", "remote (anywhere)"}

# With "residence" set in profile.json, a posting must be open to that country:
# either it names a region that covers it, or it lists the country itself.
# "Germany (Remote)" is for people living in Germany, so it no longer passes.
REGION_LOCATIONS = ["europe", "european union", "eu", "emea"]
RESIDENCE_PLACES = {
    "france": ["france", "paris", "lyon", "toulouse", "nantes", "bordeaux", "lille",
               "marseille", "poitiers"],
}


def residence_terms(residence):
    """Location terms accepted for someone living in `residence` (a name or a list)."""
    names = [residence] if isinstance(residence, str) else list(residence)
    terms = list(REGION_LOCATIONS)
    for name in names:
        name = name.lower()
        terms += RESIDENCE_PLACES.get(name, []) or [name]
    return terms


def is_location_allowed(location, allowed=None, open_remote=True):
    """Whether a posting is open to a candidate in one of the allowed places.

    A posting often lists several locations ("Remote, France; Remote, US" or
    "Paris | New York City, NY"). It passes if at least one of them is
    allowed. Postings with no location at all are kept: nothing says no.
    """
    loc = (location or "").lower()
    if any(term in loc for term in LOCATION_EXCLUDE_TERMS):
        return False
    parts = [p.strip() for p in re.split(r"[;|]", loc) if p.strip()]
    if not parts:
        return True

    terms = [t.lower() for t in (allowed if allowed is not None else DEFAULT_LOCATIONS)]
    pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(t) for t in terms) + r")(?!\w)") if terms else None
    for part in parts:
        if open_remote and part in OPEN_LOCATIONS:
            return True
        if pattern and pattern.search(part):
            return True
    return False


def is_dev_job(job, company, allowed_locations=None, open_remote=True):
    title = job["title"].lower()
    title_include = company.get("title_include", DEFAULT_TITLE_INCLUDE)
    title_exclude = company.get("title_exclude", DEFAULT_TITLE_EXCLUDE)

    if INTERN_RE.search(title):
        return False
    if any(term in (job.get("contract") or "").lower() for term in ("stage", "apprentissage", "alternance")):
        return False
    if not is_location_allowed(job.get("location"), allowed_locations, open_remote):
        return False
    # Some companies post one copy of each role per country ("Spain (Remote)",
    # "Australia (Remote)"); location_include keeps only the copies open to us.
    location_include = company.get("location_include")
    if location_include and not any(term in (job.get("location") or "").lower() for term in location_include):
        return False
    if any(term in title for term in LOCATION_EXCLUDE_TERMS):
        return False
    if not company.get("skip_department_filter"):
        dept = (job.get("department") or "").lower()
        dev_departments = {d.lower() for d in company.get("dev_departments", DEFAULT_DEV_DEPARTMENTS)}
        if dept not in dev_departments:
            return False
    if any(x in title for x in title_exclude):
        return False
    return any(kw in title for kw in title_include)


def score_job(job, keywords, boost_titles=(), avoid_titles=()):
    """Keyword score: each hit in the posting counts 1, each hit in the title
    counts 3 in total, boost_titles add 3 and avoid_titles take 5 away."""
    text = job["text"].lower()
    title = job["title"].lower()
    hits = [kw for kw in keywords if kw in text]
    title_hits = [kw for kw in keywords if kw in title]
    boosts = [t for t in boost_titles if t in title]
    avoids = [t for t in avoid_titles if t in title]
    job["match_score"] = len(hits) + 2 * len(title_hits) + 3 * len(boosts) - 5 * len(avoids)
    job["matched_keywords"] = hits
    job["id"] = job_id(job["url"])
    # Kept in memory for the optional LLM pass, never written to jobs.json.
    job["_text"] = job["text"][:6000]
    del job["text"]
    return job


NEW_DAYS = 3


def is_new(job):
    """Seen for the first time in the last NEW_DAYS days."""
    try:
        first = date.fromisoformat((job.get("first_seen") or "")[:10])
    except ValueError:
        return False
    return date.today() - first <= timedelta(days=NEW_DAYS)


def write_report(jobs, tracking, path="jobs_report.md", limit=60):
    # One ranking: remote postings and the on-site/hybrid ones of include_onsite
    # companies, best first. `jobs` is already sorted.
    shown = [j for j in jobs if is_listed(j) and j["url"] not in tracking][:limit]
    listed_urls = {j["url"] for j in jobs}
    applied = [
        (url, t) for url, t in tracking.items() if t["status"] == "applied"
    ]
    applied.sort(key=lambda item: item[1]["date"], reverse=True)
    seen_count = sum(1 for t in tracking.values() if t["status"] == "seen")

    by_company = {}
    for j in shown:
        row = by_company.setdefault(j["company"], [0, 0])
        row[0] += 1
        row[1] += is_new(j)

    lines = [
        "# Job matches",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
        f"{len(shown)} postings to look at, {sum(r[1] for r in by_company.values())} new "
        f"in the last {NEW_DAYS} days. Sorted by LLM fit when available, then keyword "
        f"score. Postings marked with `track.py` are left out ({seen_count} seen, "
        f"{len(applied)} applied).",
        "",
    ]
    if by_company:
        lines += ["| Company | Postings | New |", "|---|---:|---:|"]
        for name, (total, new) in sorted(by_company.items(), key=lambda kv: (-kv[1][1], -kv[1][0])):
            lines.append(f"| {name} | {total} | {new or ''} |")
        lines.append("")

    def add_entries(entries):
        for rank, j in enumerate(entries, start=1):
            badge = " **NEW**" if is_new(j) else ""
            lines.append(f"### {rank}. [{j['company']} - {j['title']}]({j['url']}) `{j['id']}`{badge}")
            if j.get("llm_fit") is not None:
                lines.append(f"- **Fit:** {j['llm_fit']}/10 - {j['llm_reason']}")
                if j.get("llm_flags"):
                    lines.append(f"- **Flags:** {'; '.join(j['llm_flags'])}")
            lines.append(f"- **Score:** {j['match_score']}")
            mode = workplace(j)
            lines.append(f"- **Location:** {j['location']} ({mode})")
            lines.append(f"- **Added:** {j['published']}")
            if j.get("compensation"):
                lines.append(f"- **Compensation:** {j['compensation']}")
            if j.get("matched_keywords"):
                lines.append(f"- **Matched:** {', '.join(j['matched_keywords'])}")
            lines.append("")

    lines += ["## To review", ""]
    add_entries(shown)

    lines += ["## Applied", ""]
    if not applied:
        lines += ["None yet.", ""]
    for url, t in applied:
        still_listed = "" if url in listed_urls else " - *no longer listed*"
        lines.append(f"- {t['date']} - [{t['company']} - {t['title']}]({url}) `{job_id(url)}`{still_listed}")
        if t.get("note"):
            lines.append(f"  - {t['note']}")
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def load_previous_jobs(path="jobs.json"):
    try:
        with open(path, encoding="utf-8") as f:
            return {j["url"]: j for j in json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    parser = argparse.ArgumentParser(description="Fetch, filter and score job postings.")
    parser.add_argument("--llm", action="store_true",
                        help="rate the best postings with Claude (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--llm-top", type=int, default=50, metavar="N",
                        help="how many postings to rate with --llm (default: 50)")
    parser.add_argument("--top", type=int, default=15, metavar="N",
                        help="how many postings to list per section in the terminal (default: 15)")
    parser.add_argument("--no-color", action="store_true",
                        help="plain output without colors or links")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    t = ui.Term(color=False if args.no_color else None)

    try:
        with open("companies.json", encoding="utf-8") as f:
            companies = json.load(f)
    except FileNotFoundError:
        raise SystemExit(
            "companies.json not found. Copy companies.example.json to companies.json "
            "and list the companies you want to follow."
        )
    try:
        with open("profile.json", encoding="utf-8") as f:
            profile = json.load(f)
        keywords = [k.lower() for k in profile["keywords"]]
        allowed_locations = profile.get("locations")
        if allowed_locations is None and profile.get("residence"):
            allowed_locations = residence_terms(profile["residence"])
        open_remote = profile.get("open_remote", True)
        summary = (profile.get("summary") or "").strip()
        boost_titles = [t.lower() for t in profile.get("boost_titles", [])]
        avoid_titles = [t.lower() for t in profile.get("avoid_titles", [])]
    except FileNotFoundError:
        raise SystemExit(
            "profile.json not found. Copy profile.example.json to profile.json "
            "and edit the keywords to match your own skills."
        )

    previous = load_previous_jobs()
    tracking = load_tracking()

    def fetch_company(company):
        fetcher = FETCHERS.get(company["ats"])
        if not fetcher:
            return company, None, f"unsupported ATS '{company['ats']}'", 0.0
        start = time.monotonic()
        try:
            return company, fetcher(company), None, time.monotonic() - start
        except Exception as e:  # one broken board must not sink the whole run
            return company, None, f"{type(e).__name__}: {e}", time.monotonic() - start

    started = time.monotonic()
    ui.banner(t, f"{len(companies)} boards - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()
    progress = ui.Progress(t, len(companies))
    results = [None] * len(companies)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_company, c): i for i, c in enumerate(companies)}
        for future in as_completed(futures):
            result = future.result()
            results[futures[future]] = result
            progress.update(result[0]["name"], ok=result[2] is None)
    progress.finish()

    all_jobs, failed, board_rows = [], [], []
    for company, jobs, error, secs in results:
        row = {"name": company["name"], "ats": company["ats"], "total": 0, "dev": 0,
               "shown": 0, "new": 0, "secs": secs, "error": error}
        board_rows.append(row)
        if error:
            failed.append(company["name"])
            continue
        dev_jobs = [j for j in jobs if is_dev_job(j, company, allowed_locations, open_remote)]
        if company.get("include_onsite"):
            for j in dev_jobs:
                j["include_onsite"] = True
        row["total"], row["dev"] = len(jobs), len(dev_jobs)
        all_jobs.extend(score_job(j, keywords, boost_titles, avoid_titles) for j in dev_jobs)

    # A board that failed this run keeps its previous postings, so they are
    # neither reported as removed nor lost from jobs.json.
    for url, j in previous.items():
        if j["company"] in failed:
            all_jobs.append(j)

    today = date.today().isoformat()
    for j in all_jobs:
        prior = previous.get(j["url"])
        if prior:
            j["first_seen"] = prior.get("first_seen") or prior.get("published") or today
        else:
            # First ever run: nothing is "new", fall back to the posting date.
            j["first_seen"] = today if previous else (j.get("published") or today)

    all_jobs.sort(key=lambda j: (j["match_score"], j["published"]), reverse=True)

    if args.llm and not summary:
        raise SystemExit('--llm needs a "summary" field in profile.json (a few lines about you).')
    if summary:
        tracked = load_tracking()
        candidates = [j for j in all_jobs if is_listed(j) and j["url"] not in tracked]
        from llm_score import apply_llm_scores
        apply_llm_scores(candidates, all_jobs, summary, top=args.llm_top, call_api=args.llm)
        all_jobs.sort(key=lambda j: (j.get("llm_fit", -1), j["match_score"], j["published"]), reverse=True)

    current_urls = {j["url"] for j in all_jobs}
    removed_jobs = [j for url, j in previous.items() if url not in current_urls]

    with open("jobs.json", "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in j.items() if k != "_text"} for j in all_jobs],
                  f, ensure_ascii=False, indent=2)

    write_report(all_jobs, tracking)

    untracked = [j for j in all_jobs if is_listed(j) and j["url"] not in tracking]
    for row in board_rows:
        mine = [j for j in untracked if j["company"] == row["name"]]
        row["shown"] = len(mine)
        row["new"] = sum(1 for j in mine if is_new(j))
    fresh = [j for j in untracked if is_new(j)]
    handled = sum(1 for j in all_jobs if j["url"] in tracking)

    ui.boards_table(t, board_rows)
    ui.summary(t, len(all_jobs), len(untracked), len(fresh), handled, time.monotonic() - started)
    ui.job_list(t, "Top matches", untracked, tracking, args.top,
                empty="No postings left to review. Run track.py --all to see the rest.")
    if previous:
        ui.removed_list(t, removed_jobs)
    ui.footer(t, failed, "jobs_report.md")


if __name__ == "__main__":
    main()
