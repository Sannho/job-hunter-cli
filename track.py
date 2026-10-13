"""Mark job postings as applied or seen, so the report stops resurfacing them.

Usage:
    python track.py                              # interactive picker (see below)
    python track.py applied <id|url> [note...]   # moves it to the "Applied" section
    python track.py seen <id|url>                # hides it from the report
    python track.py unmark <id|url>              # back into the "To review" list
    python track.py list                         # show everything tracked

<id> is the 6-character code shown next to each posting in jobs_report.md
and in fetch_jobs.py's console output. Marks are stored in applications.json,
keyed by posting URL, so they survive re-runs and ranking changes. Marking
rebuilds jobs_report.md from the last jobs.json, without fetching again.

Interactive picker keys: up/down (or k/j) move, PgUp/PgDn jump a page,
space cycles a posting through none -> seen -> applied -> none, s/a/u set
seen/applied/unmarked directly, o opens the posting in the browser, h hides
or shows already-marked postings, q quits.
"""
import json
import os
import shutil
import sys
import webbrowser
from datetime import date

from fetch_jobs import TRACKING_PATH, is_listed, job_id, load_tracking, write_report


def save_tracking(tracking):
    with open(TRACKING_PATH, "w", encoding="utf-8") as f:
        json.dump(tracking, f, ensure_ascii=False, indent=2)


def find_job(ref, tracking):
    """Resolve an id or URL against jobs.json first, then already-tracked postings."""
    with open("jobs.json", encoding="utf-8") as f:
        jobs = json.load(f)
    for j in jobs:
        if ref in (j["url"], job_id(j["url"])):
            return j["url"], j["company"], j["title"]
    for url, t in tracking.items():
        if ref in (url, job_id(url)):
            return url, t["company"], t["title"]
    sys.exit(f"No posting matches '{ref}' in jobs.json or {TRACKING_PATH}.")


def rebuild_report(tracking):
    # Rebuild the report from the last fetch, no network call needed.
    with open("jobs.json", encoding="utf-8") as f:
        write_report(json.load(f), tracking)


# --- interactive picker ------------------------------------------------------

RESET, REVERSE, DIM, GREEN, YELLOW, BOLD = (
    "\x1b[0m", "\x1b[7m", "\x1b[2m", "\x1b[32m", "\x1b[33m", "\x1b[1m",
)
CYCLE = {None: "seen", "seen": "applied", "applied": None}


def read_key():
    """Return one of: up, down, pgup, pgdn, home, end, or the typed character."""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down", "I": "pgup", "Q": "pgdn",
                    "G": "home", "O": "end"}.get(msvcrt.getwch(), "")
        return ch

    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq in ("[5", "[6"):
                sys.stdin.read(1)  # trailing "~"
            return {"[A": "up", "[B": "down", "[5": "pgup", "[6": "pgdn",
                    "[H": "home", "[F": "end"}.get(seq, "")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def fit(text, width):
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def render(jobs, tracking, cursor, top, width, height, hide_marked):
    """Build one full screen of the picker as a string."""
    marked = sum(1 for j in jobs if j["url"] in tracking)
    lines = [
        f"{BOLD}Job picker{RESET} - {len(jobs)} postings, {marked} marked"
        + (" (hidden)" if hide_marked else ""),
        f"{DIM}up/down move - space cycle seen/applied - s/a/u set - o open - h hide marked - q quit{RESET}",
        "",
    ]
    list_height = height - len(lines) - 5
    for i in range(top, min(top + list_height, len(jobs))):
        j = jobs[i]
        status = (tracking.get(j["url"]) or {}).get("status")
        tag = {"seen": "seen   ", "applied": "APPLIED"}.get(status, "       ")
        row = fit(f"{tag} {j['match_score']:>2}  {j['company']} - {j['title']}  ({j['location']})", width - 1)
        color = {"seen": DIM, "applied": GREEN}.get(status, "")
        if i == cursor:
            color += REVERSE
        lines.append(f"{color}{row.ljust(width - 1)}{RESET}")
    while len(lines) < height - 5:
        lines.append("")

    if jobs:
        j = jobs[cursor]
        lines += [
            "-" * (width - 1),
            YELLOW + fit(f"{j['id']}  Added {j['published']}  -  {j.get('compensation') or 'no compensation listed'}", width - 1) + RESET,
            fit(f"Matched: {', '.join(j.get('matched_keywords', []))}", width - 1),
            fit(j["url"], width - 1),
        ]
    else:
        lines.append("Nothing to show. Press h to show marked postings, q to quit.")
    return "\n".join(lines)


def interactive():
    if os.name == "nt":
        os.system("")  # turns on ANSI escape handling in the Windows console

    with open("jobs.json", encoding="utf-8") as f:
        all_jobs = [j for j in json.load(f) if is_listed(j)]
    tracking = load_tracking()
    hide_marked = False
    cursor = top = 0
    changed = False

    sys.stdout.write("\x1b[?1049h\x1b[?25l")  # alternate screen, hide cursor
    try:
        while True:
            jobs = [j for j in all_jobs if not (hide_marked and j["url"] in tracking)]
            width, height = shutil.get_terminal_size()
            list_height = max(height - 8, 1)
            cursor = max(0, min(cursor, len(jobs) - 1))
            top = min(max(top, cursor - list_height + 1), cursor)

            sys.stdout.write("\x1b[H\x1b[2J" + render(jobs, tracking, cursor, top, width, height, hide_marked))
            sys.stdout.flush()

            key = read_key()
            if key in ("q", "\x1b", "\x03"):
                break
            elif key in ("up", "k"):
                cursor -= 1
            elif key in ("down", "j"):
                cursor += 1
            elif key == "pgup":
                cursor -= list_height
            elif key == "pgdn":
                cursor += list_height
            elif key == "home":
                cursor = 0
            elif key == "end":
                cursor = len(jobs) - 1
            elif key == "h":
                hide_marked = not hide_marked
            elif key == "o" and jobs:
                webbrowser.open(jobs[cursor]["url"])
            elif key in (" ", "s", "a", "u") and jobs:
                j = jobs[cursor]
                current = (tracking.get(j["url"]) or {}).get("status")
                new = CYCLE[current] if key == " " else {"s": "seen", "a": "applied", "u": None}[key]
                if new is None:
                    tracking.pop(j["url"], None)
                else:
                    tracking[j["url"]] = {
                        "status": new,
                        "date": date.today().isoformat(),
                        "company": j["company"],
                        "title": j["title"],
                        "note": (tracking.get(j["url"]) or {}).get("note", ""),
                    }
                save_tracking(tracking)
                changed = True
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")  # show cursor, leave alternate screen
        sys.stdout.flush()

    if changed:
        rebuild_report(tracking)
        print("Saved applications.json and updated jobs_report.md.")


def main():
    if len(sys.argv) == 1:
        return interactive()
    if sys.argv[1] not in ("applied", "seen", "unmark", "list"):
        sys.exit(__doc__)

    command = sys.argv[1]
    tracking = load_tracking()

    if command == "list":
        for url, t in sorted(tracking.items(), key=lambda item: item[1]["date"]):
            note = f"  ({t['note']})" if t.get("note") else ""
            print(f"{t['date']} {t['status']:<7} {job_id(url)} {t['company']} - {t['title']}{note}")
        return

    if len(sys.argv) < 3:
        sys.exit(__doc__)

    url, company, title = find_job(sys.argv[2], tracking)

    if command == "unmark":
        tracking.pop(url, None)
        print(f"Unmarked: {company} - {title}")
    else:
        tracking[url] = {
            "status": command,
            "date": date.today().isoformat(),
            "company": company,
            "title": title,
            "note": " ".join(sys.argv[3:]),
        }
        print(f"Marked {command}: {company} - {title}")

    save_tracking(tracking)
    rebuild_report(tracking)
    print("Updated jobs_report.md.")


if __name__ == "__main__":
    main()
