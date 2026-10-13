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
import urllib.request
from datetime import datetime, timezone

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
            "remote": j.get("isRemote", False),
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
}


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

    if "intern" in title:
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


def score_job(job, keywords):
    text = job["text"].lower()
    hits = [kw for kw in keywords if kw in text]
    job["match_score"] = len(hits)
    job["matched_keywords"] = hits
    job["id"] = job_id(job["url"])
    # Kept in memory for the optional LLM pass, never written to jobs.json.
    job["_text"] = job["text"][:6000]
    del job["text"]
    return job


def write_report(jobs, tracking, path="jobs_report.md", limit=50):
    to_review = [j for j in jobs if j.get("remote") and j["url"] not in tracking][:limit]
    # On-site/hybrid postings from include_onsite companies get their own
    # section: their French descriptions score low against English keywords,
    # so they would never make the remote top 50.
    onsite = [j for j in jobs if not j.get("remote") and is_listed(j) and j["url"] not in tracking]
    listed_urls = {j["url"] for j in jobs}
    applied = [
        (url, t) for url, t in tracking.items() if t["status"] == "applied"
    ]
    applied.sort(key=lambda item: item[1]["date"], reverse=True)
    seen_count = sum(1 for t in tracking.values() if t["status"] == "seen")

    lines = [
        "# Job matches",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat()}. Remote postings "
        f"sorted by LLM fit when available, then keyword score, best first (top {limit}), then on-site/hybrid "
        f"postings from companies flagged include_onsite. Postings marked "
        f"with `track.py` are left out of this list ({seen_count} seen, "
        f"{len(applied)} applied).",
        "",
        "## To review",
        "",
    ]
    def add_entries(entries):
        for rank, j in enumerate(entries, start=1):
            lines.append(f"### {rank}. [{j['company']} - {j['title']}]({j['url']}) `{j['id']}`")
            if j.get("llm_fit") is not None:
                lines.append(f"- **Fit:** {j['llm_fit']}/10 - {j['llm_reason']}")
                if j.get("llm_flags"):
                    lines.append(f"- **Flags:** {'; '.join(j['llm_flags'])}")
            lines.append(f"- **Score:** {j['match_score']}")
            lines.append(f"- **Location:** {j['location']}")
            lines.append(f"- **Added:** {j['published']}")
            if j.get("compensation"):
                lines.append(f"- **Compensation:** {j['compensation']}")
            if j.get("matched_keywords"):
                lines.append(f"- **Matched:** {', '.join(j['matched_keywords'])}")
            lines.append("")

    add_entries(to_review)
    lines += ["## On-site / hybrid", ""]
    add_entries(onsite)

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
    args = parser.parse_args()

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
        open_remote = profile.get("open_remote", True)
        summary = (profile.get("summary") or "").strip()
    except FileNotFoundError:
        raise SystemExit(
            "profile.json not found. Copy profile.example.json to profile.json "
            "and edit the keywords to match your own skills."
        )

    previous = load_previous_jobs()
    tracking = load_tracking()

    all_jobs = []
    for company in companies:
        fetcher = FETCHERS.get(company["ats"])
        if not fetcher:
            print(f"Skipping {company['name']}: unsupported ATS '{company['ats']}'")
            continue
        jobs = fetcher(company)
        dev_jobs = [j for j in jobs if is_dev_job(j, company, allowed_locations, open_remote)]
        if company.get("include_onsite"):
            for j in dev_jobs:
                j["include_onsite"] = True
        print(f"{company['name']}: {len(jobs)} openings, {len(dev_jobs)} dev-related")
        all_jobs.extend(score_job(j, keywords) for j in dev_jobs)

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
    new_jobs = [j for j in all_jobs if j["url"] not in previous]
    removed_jobs = [j for url, j in previous.items() if url not in current_urls]

    with open("jobs.json", "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in j.items() if k != "_text"} for j in all_jobs],
                  f, ensure_ascii=False, indent=2)

    write_report(all_jobs, tracking)

    untracked = [j for j in all_jobs if is_listed(j) and j["url"] not in tracking]
    print(f"\nTotal dev-related openings across all companies: {len(all_jobs)} "
          f"({sum(1 for j in all_jobs if is_listed(j))} listed, "
          f"{sum(1 for j in all_jobs if j['url'] in tracking)} already seen/applied)")
    print("Top listed matches:\n")
    for j in untracked[:15]:
        print(f"[{j['match_score']:>2}] {j['id']} {j['company']} - {j['title']} ({j['location']})  {j['url']}")

    if previous:
        print(f"\nNew since last run ({len(new_jobs)}):")
        for j in new_jobs:
            print(f"  + {j['id']} {j['company']} - {j['title']} ({j['location']})  {j['url']}")

        print(f"\nNo longer listed since last run ({len(removed_jobs)}):")
        for j in removed_jobs:
            print(f"  - {j['company']} - {j['title']} ({j['location']})  {j['url']}")
    else:
        print("\nNo previous jobs.json found - nothing to compare against yet.")

    print(f"\nSaved: jobs.json, jobs_report.md")
    print(f"Fetched at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
