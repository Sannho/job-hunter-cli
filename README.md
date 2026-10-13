# job-hunter

> Find the right openings and track what you applied to. All from the
> command line, with no accounts and no paid services.

Job boards are noisy. This tool pulls the public postings of the companies
you care about, keeps only the developer roles that fit, scores each one
against your own skills, and gives you a short ranked report.

```
companies.json ──► fetch_jobs.py ──► jobs.json + jobs_report.md
                                          │
                  track.py  ◄─────────────┘   mark applied / seen
```

## Features

- **Multi-board fetching**: Ashby, Greenhouse and Lever. Public APIs only.
- **Smart filtering**: dev-only departments and titles, location filters,
  remote-first, with optional on-site/hybrid section for companies that
  need it.
- **Scoring against your profile**: keywords in `profile.json` decide the
  ranking, so the best matches come first.
- **Application tracking**: mark postings `applied` or `seen` from an
  interactive picker. Seen postings leave the report; applied ones move to
  their own section and get flagged when the posting disappears.

## Example report

`jobs_report.md` looks like this (postings are public, scores depend on your
`profile.json`):

```markdown
## To review

### 1. [GitLab - Professional Services Technical Architect - West](https://job-boards.greenhouse.io/gitlab/jobs/8857335002) `77c79c`
- **Score:** 9
- **Location:** Remote
- **Added:** 2026-09-30
- **Matched:** go, git, ci/cd, mentor, code review, leadership, remote, async

### 2. [Ashby - Engineering Manager - EU](https://jobs.ashbyhq.com/ashby/7458d4e9-da2e-47bd-98cb-adfda43d42b2) `2e7ba1`
- **Score:** 11
- **Location:** Remote - European Union
- **Compensation:** EUR 110K - 185K
- **Matched:** go, typescript, redis, ci/cd, code review, leadership, remote
```

## Quick start

```bash
cp profile.example.json profile.json     # edit keywords to match your skills
cp companies.example.json companies.json # edit the list of companies to follow
python fetch_jobs.py          # fetch, filter, score -> jobs.json + jobs_report.md
python track.py               # interactive picker (arrows, space, o to open, q to quit)
```

Python 3 only, standard library, no `pip install` needed.

```bash
python track.py applied 7624e3 "sent via referral"   # mark a posting as applied
python track.py seen 5fb0e6                          # hide a posting from the report
python track.py unmark 5fb0e6                        # put it back in "To review"
python track.py list                                 # everything marked so far
```

Each posting has a short id (e.g. `7624e3`) derived from its URL. Marking
rebuilds the report immediately, without a new fetch.

## Adding a company

Add an entry to your `companies.json` (start from [`companies.example.json`](companies.example.json)):

```json
{
  "name": "Company Name",
  "slug": "company-slug",
  "ats": "ashby",
  "dev_departments": ["engineering", "security"],
  "title_include": ["engineer", "developer", "architect"],
  "title_exclude": ["solutions engineer", "sales engineer"]
}
```

To find the slug, open the careers page and look for a link to
`jobs.ashbyhq.com/<slug>`, `boards.greenhouse.io/<slug>` or
`jobs.lever.co/<slug>`.

Optional keys: `skip_department_filter`, `location_include`,
`include_onsite`. Defaults are in `fetch_jobs.py`.

To support another job board, write a `fetch_<ats>(company)` function that
returns the same shape as the others and register it in `FETCHERS`.

## Limitations

- Only public postings are read; nothing is submitted on your behalf.

## License

[MIT](LICENSE)
