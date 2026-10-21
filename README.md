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

- **Multi-board fetching**: Ashby, Greenhouse, Lever, Workable,
  SmartRecruiters, Recruitee, Personio and Teamtailor. Public APIs only, fetched in
  parallel. A board that fails never stops the others.
- **Smart filtering**: dev-only departments and titles, location filter
  (Europe, EMEA, France and "remote anywhere" by default; configurable),
  remote-first, with optional on-site/hybrid section for companies that
  need it.
- **Scoring against your profile**: keywords in `profile.json` decide the
  ranking. Title matches weigh more, and you can boost or avoid title words.
- **Report built to be skimmed**: summary table per company, one ranking with
  a `NEW` tag on postings first seen in the last 3 days and a remote / on-site
  tag on each.
- **Optional LLM scoring**: Claude rates how well each of the best postings
  fits your profile and tells you why. Off by default.
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
python track.py --all         # same, also listing non-remote postings (r toggles)
```

Python 3 only, standard library, no `pip install` needed.

The terminal output shows a live progress bar, a table per board (open
postings, dev roles open to you, new ones, time, failures) and one ranking
of the top matches (new ones tagged `NEW`) with clickable titles. Colors and
links are used only in a real terminal.

```bash
python fetch_jobs.py --top 30      # list more postings per section
python fetch_jobs.py --no-color    # plain text (also automatic when piped, or with NO_COLOR)
```

```bash
python track.py applied 7624e3 "sent via referral"   # mark a posting as applied
python track.py seen 5fb0e6                          # hide a posting from the report
python track.py unmark 5fb0e6                        # put it back in "To review"
python track.py list                                 # everything marked so far
```

The picker shows one colored row per posting (score, company, title, place,
`NEW`, remote / hybrid / on-site) and a detail pane with the full location, pay,
matched keywords or LLM verdict, and your own note. Filters: `h` hide marked,
`r` non-remote, `n` new only, `c` cycle companies, `/` search, `x` reset.
Titles are clickable links in terminals that support them.

Each posting has a short id (e.g. `7624e3`) derived from its URL. Marking
rebuilds the report immediately, without a new fetch.

## Tuning the score

In `profile.json`, besides `keywords`:

```json
{
  "boost_titles": ["senior", "staff", "backend"],
  "avoid_titles": ["intern", "sales", "support"]
}
```

A keyword found in the posting counts 1, found in the title 3. Each
`boost_titles` word in the title adds 3, each `avoid_titles` word takes away 5.

## Locations

By default a posting is kept when one of its locations is in Europe (EU, UK,
Switzerland, Norway), EMEA or France, or when it is plainly open ("Remote",
"Anywhere", "Worldwide"). Postings restricted to the US, Asia and so on are
dropped. "Remote, US" and "Remote-Friendly | San Francisco" do not pass.

Many companies hire remote per country ("Germany (Remote)" means living in
Germany). Set `"residence"` in `profile.json` and only postings you could
actually take are kept: those open to your country, listing it among several,
or open to a region that covers it (Europe, EU, EMEA), plus plain "Remote":

```json
{ "residence": "france" }
```

With `residence` set, "Germany (Remote)" or "London, UK" no longer pass, while
"Remote, EMEA" and "Remote, France; Remote, Germany" do. For other countries
the country name itself is used as the match term.

To control the list yourself instead, change `locations` in `profile.json`
(it takes precedence over `residence`):

```json
{
  "locations": ["france", "paris", "europe", "emea"],
  "open_remote": false
}
```

- `locations`: place names matched as whole words, case-insensitive. This
  replaces the default list (see `DEFAULT_LOCATIONS` in `fetch_jobs.py`).
- `open_remote`: set to `false` to drop postings that only say "Remote" or
  "Anywhere" with no region. Default `true`.

## LLM scoring (optional)

Keyword counting is a rough first pass. With `--llm`, the best postings are
also rated by Claude against a short description of you:

```bash
export ANTHROPIC_API_KEY=...        # your own key
python fetch_jobs.py --llm          # rates the top 50 by keyword score
python fetch_jobs.py --llm --llm-top 20
```

- Add a `summary` field to `profile.json` (a few lines: your background, what
  you want, what you refuse). See `profile.example.json`.
- Each posting gets a fit from 0 to 10, a one-sentence reason and up to three
  red flags. The report sorts by fit first.
- Ratings are cached in `llm_cache.json`, keyed by posting and profile, so a
  posting is rated once. Without `--llm`, cached ratings still show up.
- Postings are treated as untrusted text: the model answers through a
  structured tool call, the output is validated, and any failure falls back to
  the keyword score. The report is always produced.
- Model: `claude-haiku-5-5` by default, override with `JOB_HUNTER_MODEL`.
- Postings and your summary are sent to the Anthropic API. Nothing is sent
  without `--llm`.

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

To find the slug, open the careers page and look at the job board link:

| `ats` | Link looks like | Slug |
|---|---|---|
| `ashby` | `jobs.ashbyhq.com/<slug>` | `<slug>` |
| `greenhouse` | `boards.greenhouse.io/<slug>` or `job-boards.greenhouse.io/<slug>` | `<slug>` |
| `lever` | `jobs.lever.co/<slug>` | `<slug>` |
| `workable` | `apply.workable.com/<slug>` | `<slug>` |
| `smartrecruiters` | `jobs.smartrecruiters.com/<slug>` | `<slug>` (case matters) |
| `recruitee` | `<slug>.recruitee.com` | `<slug>` |
| `personio` | `<slug>.jobs.personio.de` | `<slug>` |
| `teamtailor` | `<slug>.teamtailor.com` or a custom domain such as `careers.acme.com` | `<slug>` or the full domain |
| `jibe` | iCIMS career site with `/api/jobs` (for example `jobs.axa.com`) | the site host |
| `phenom` | Phenom People career site (for example `careers.allianz.com/global/en`) | the site host |

Large groups (`jibe`, `phenom`) host thousands of postings, so give a `query`
(or a list of queries, merged and de-duplicated) to let the site filter for you.
Their job titles are often in French, so add French words to `title_include`:

```json
{
  "name": "AXA", "slug": "jobs.axa.com", "ats": "jibe",
  "query": ["engineer", "ingénieur", "développeur", "architecte"],
  "skip_department_filter": true, "include_onsite": true,
  "title_include": ["engineer", "ingénieur", "développeur", "architecte"],
  "title_exclude": ["business developer", "intern", "stage"]
}
```

For `phenom`, `path` (default `global/en`), `lang` (default `en_global`) and
`country` (default `global`) match the locale of the career site. The Phenom
search returns a short teaser, not the full description, so keyword scores are
lower there.

Workable and SmartRecruiters list titles only, so the tool fetches the
description of postings whose title passes `title_include` and
`title_exclude`. Set those two for large boards to keep the run fast.

Optional keys: `skip_department_filter`, `location_include`,
`include_onsite`. Defaults are in `fetch_jobs.py`.

To support another job board, write a `fetch_<ats>(company)` function that
returns the same shape as the others (see `more_ats.py`) and register it in
`FETCHERS`.

## Limitations

- Only public postings are read; nothing is submitted on your behalf.

## License

[MIT](LICENSE)
