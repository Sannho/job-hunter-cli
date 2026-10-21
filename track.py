"""Mark job postings as applied or seen, so the report stops resurfacing them.

Usage:
    python track.py                              # interactive picker (see below)
    python track.py --all                        # same, also showing non-remote postings
    python track.py applied <id|url> [note...]   # moves it to the "Applied" section
    python track.py seen <id|url>                # hides it from the report
    python track.py unmark <id|url>              # back into the "To review" list
    python track.py list                         # show everything tracked

<id> is the 6-character code shown next to each posting in jobs_report.md
and in the detail pane of the picker. Marks are stored in applications.json,
keyed by posting URL, so they survive re-runs and ranking changes. Marking
rebuilds jobs_report.md from the last jobs.json, without fetching again.

Interactive picker keys:
    up/down (k/j)   move            PgUp/PgDn   jump a page       home/end   first/last
    space           cycle none -> seen -> applied -> none
    s / a / u       set seen / applied / unmarked directly
    o               open the posting in the browser
    h               hide or show already-marked postings
    r               show or hide non-remote postings (--all starts with them shown)
    n               only postings first seen in the last 3 days
    c               cycle through companies
    /               search company, title or location (Enter to apply, Esc to cancel)
    x               clear all filters
    q               quit
"""
import json
import os
import shutil
import sys
import webbrowser
from datetime import date

import ui
from fetch_jobs import (NEW_DAYS, TRACKING_PATH, is_listed, is_new, job_id,
                        load_tracking, workplace, write_report)


def save_tracking(tracking):
    with open(TRACKING_PATH, "w", encoding="utf-8") as f:
        json.dump(tracking, f, ensure_ascii=False, indent=2)


def load_jobs():
    try:
        with open("jobs.json", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit("jobs.json not found. Run python fetch_jobs.py first.")


def find_job(ref, tracking):
    """Resolve an id or URL against jobs.json first, then already-tracked postings."""
    for j in load_jobs():
        if ref in (j["url"], job_id(j["url"])):
            return j["url"], j["company"], j["title"]
    for url, t in tracking.items():
        if ref in (url, job_id(url)):
            return url, t["company"], t["title"]
    sys.exit(f"No posting matches '{ref}' in jobs.json or {TRACKING_PATH}.")


def rebuild_report(tracking):
    # Rebuild the report from the last fetch, no network call needed.
    write_report(load_jobs(), tracking)


# --- interactive picker ------------------------------------------------------

CYCLE = {None: "seen", "seen": "applied", "applied": None}
HINTS_WIDE = ("↑↓ move  space cycle  s seen  a applied  u unmark  o open  h hide marked  "
              "r non-remote  n new  c company  / search  x reset  q quit")
HINTS_NARROW = "↑↓ move  space cycle  s/a/u mark  o open  h r n c / filters  x reset  q quit"
MODE_COLOR = {"remote": "green", "hybrid": "yellow"}


def read_key():
    """Return one of: up, down, pgup, pgdn, home, end, enter, or the typed character."""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down", "I": "pgup", "Q": "pgdn",
                    "G": "home", "O": "end"}.get(msvcrt.getwch(), "")
        return "enter" if ch in ("\r", "\n") else ch

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
        return "enter" if ch in ("\r", "\n") else ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


class Filters:
    def __init__(self, show_all):
        self.show_all = show_all
        self.hide_marked = False
        self.only_new = False
        self.company = None
        self.query = ""

    def reset(self, show_all=False):
        self.__init__(show_all)

    def apply(self, jobs, tracking):
        needle = self.query.lower()
        out = []
        for j in jobs:
            if not (self.show_all or is_listed(j)):
                continue
            if self.hide_marked and j["url"] in tracking:
                continue
            if self.only_new and not is_new(j):
                continue
            if self.company and j["company"] != self.company:
                continue
            if needle and needle not in f"{j['company']} {j['title']} {j['location']}".lower():
                continue
            out.append(j)
        return out


def render_row(t, j, status, selected, score_w, top, company_w, loc_w, title_w):
    chip = {"seen": "seen   ", "applied": "APPLIED"}.get(status, "       ")
    ratio = j["match_score"] / top
    tier = "green" if ratio >= 0.8 else "yellow" if ratio >= 0.5 else "gray"
    if j.get("llm_fit") is not None:
        score = t.s(t.pad(f"{j['llm_fit']}/10", score_w, True), "cyan", "bold")
    else:
        score = t.s(t.pad(str(j["match_score"]), score_w, True), tier, "bold")
    kind = workplace(j)
    new = t.s("NEW", "yellow", "bold") if is_new(j) else "   "
    title = t.cut(j["title"], title_w)
    parts = [
        t.s("▌", "cyan") if selected else " ",
        t.s(chip, "green", "bold") if status == "applied" else t.s(chip, "gray"),
        score,
        t.s(t.pad(t.cut(j["company"], company_w), company_w), "bold"),
        t.link(title, j["url"]) + " " * max(title_w - len(title), 0),
        t.s(t.pad(t.cut(_place(j), loc_w), loc_w), "gray"),
        new,
        t.s(t.pad(kind, 7), MODE_COLOR.get(kind, "magenta")),
    ]
    row = " ".join(parts)
    if selected:
        return t.s(ui.ANSI_RE.sub("", row), "invert", "bold")
    if status == "seen":
        return t.s(ui.ANSI_RE.sub("", row), "gray")
    return row


def _place(j):
    return (j.get("location") or "").split(";")[0].split("|")[0].strip()


def detail_lines(t, j, tracking):
    mark = tracking.get(j["url"]) or {}
    kind = workplace(j)
    meta = [j.get("location") or "no location", t.s(kind, MODE_COLOR.get(kind, "magenta")),
            f"added {j['published']}" if j.get("published") else "",
            j.get("compensation") or t.s("no compensation listed", "gray")]
    if is_new(j):
        meta.append(t.s("NEW", "yellow", "bold"))
    lines = [
        t.s(f"{j['company']} - {j['title']}", "bold"),
        t.s("  ·  ", "gray").join(m for m in meta if m),
    ]
    if j.get("llm_fit") is not None:
        flags = f"   {t.s('! ' + '; '.join(j['llm_flags']), 'yellow')}" if j.get("llm_flags") else ""
        lines.append(f"{t.s('Fit ' + str(j['llm_fit']) + '/10', 'cyan', 'bold')} {j.get('llm_reason', '')}{flags}")
    else:
        keywords = ", ".join(j.get("matched_keywords", [])) or "none"
        lines.append(f"{t.s('Score ' + str(j['match_score']), 'bold')}  {t.s('matched:', 'gray')} {keywords}")
    if mark:
        note = f" - {mark['note']}" if mark.get("note") else ""
        label = "Applied" if mark["status"] == "applied" else "Marked seen"
        lines.append(t.s(f"{label} on {mark['date']}{note}", "green" if mark["status"] == "applied" else "gray"))
    else:
        lines.append(t.s("Not marked yet", "gray"))
    lines.append(f"{t.s(j['id'], 'gray')}  {t.link(t.cut(j['url'], t.width - 12), j['url'])}")
    return [t.clip(l, t.width - 1) for l in lines]


def chips(t, f, total, marked, all_marked):
    out = []
    if f.show_all:
        out.append("non-remote shown")
    if f.hide_marked:
        out.append("marked hidden")
    if f.only_new:
        out.append(f"new only ({NEW_DAYS}d)")
    if f.company:
        out.append(f"company: {f.company}")
    if f.query:
        out.append(f"search: {f.query}")
    shown = "  ".join(t.s(f"[{c}]", "yellow") for c in out) or t.s("no filter", "gray")
    return f"{t.s(str(total), 'bold')} postings  {t.s(f'{marked} marked', 'gray')}   {shown}"


def render(t, jobs, tracking, cursor, top_row, height, f, message, typing, every_job):
    width = t.width
    marked = sum(1 for j in jobs if j["url"] in tracking)
    position = f"{cursor + 1}/{len(jobs)}" if jobs else "0/0"
    title = t.s("job-hunter", "bold", "cyan") + t.s("  picker", "gray")
    gap = max(width - ui.Term.vlen(title) - len(position) - 1, 1)
    lines = [
        title + " " * gap + t.s(position, "gray"),
        t.clip(chips(t, f, len(jobs), marked, len(every_job)), width - 1),
        t.clip(t.s(HINTS_WIDE if width >= 135 else HINTS_NARROW, "gray"), width - 1),
        t.rule(),
    ]
    list_height = max(height - 11, 3)
    if jobs:
        top = max(j["match_score"] for j in jobs) or 1
        score_w = 5 if any(j.get("llm_fit") is not None for j in jobs) else 3
        company_w = min(max(len(j["company"]) for j in jobs), 14)
        loc_w = min(max(len(_place(j)) for j in jobs), 26 if width >= 100 else 14)
        fixed = 1 + 1 + 7 + 1 + score_w + 1 + company_w + 1 + 1 + loc_w + 1 + 3 + 1 + 7 + 1
        title_w = max(width - fixed, 16)
        for i in range(top_row, min(top_row + list_height, len(jobs))):
            j = jobs[i]
            status = (tracking.get(j["url"]) or {}).get("status")
            lines.append(render_row(t, j, status, i == cursor, score_w, top, company_w, loc_w, title_w))
    else:
        lines.append(t.s("  Nothing matches these filters. Press x to reset them.", "gray"))
    while len(lines) < 4 + list_height:
        lines.append("")
    lines.append(t.rule())
    detail = detail_lines(t, jobs[cursor], tracking) if jobs else []
    detail += [""] * (5 - len(detail))
    lines += detail
    if typing is not None:
        lines.append(t.s("/", "cyan", "bold") + typing + t.s("█", "cyan"))
    else:
        lines.append(t.s(message, "green") if message else "")
    return "\n".join(lines)


def interactive(show_all=False):
    t = ui.Term()
    if not t.tty:
        sys.exit("The picker needs a terminal. Use: python track.py list | applied | seen | unmark")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    every_job = load_jobs()
    tracking = load_tracking()
    f = Filters(show_all)
    companies = sorted({j["company"] for j in every_job})
    cursor = top_row = 0
    message, typing = "", None
    counts = {"seen": 0, "applied": 0, "unmarked": 0}

    sys.stdout.write("\x1b[?1049h\x1b[?25l")  # alternate screen, hide cursor
    try:
        while True:
            jobs = f.apply(every_job, tracking)
            width, height = shutil.get_terminal_size()
            t.width = max(70, min(width, 150))
            list_height = max(height - 11, 3)
            cursor = max(0, min(cursor, len(jobs) - 1))
            top_row = min(max(top_row, cursor - list_height + 1), cursor)
            top_row = max(top_row, 0)

            sys.stdout.write("\x1b[H\x1b[2J" + render(t, jobs, tracking, cursor, top_row, height,
                                                       f, message, typing, every_job))
            sys.stdout.flush()
            message = ""

            key = read_key()
            if typing is not None:  # search box
                if key == "enter":
                    f.query, typing, cursor, top_row = typing.strip(), None, 0, 0
                elif key == "\x1b":
                    typing = None
                elif key in ("\x7f", "\x08"):
                    typing = typing[:-1]
                elif len(key) == 1 and key.isprintable():
                    typing += key
                continue

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
                f.hide_marked = not f.hide_marked
            elif key == "r":
                f.show_all = not f.show_all
            elif key == "n":
                f.only_new = not f.only_new
                cursor = top_row = 0
            elif key == "c":
                options = [None] + companies
                f.company = options[(options.index(f.company) + 1) % len(options)]
                cursor = top_row = 0
            elif key == "/":
                typing = f.query
            elif key == "x":
                f.reset()
                cursor = top_row = 0
            elif key == "o" and jobs:
                webbrowser.open(jobs[cursor]["url"])
                message = "Opened in your browser."
            elif key in (" ", "s", "a", "u") and jobs:
                j = jobs[cursor]
                current = (tracking.get(j["url"]) or {}).get("status")
                new = CYCLE[current] if key == " " else {"s": "seen", "a": "applied", "u": None}[key]
                if new is None:
                    tracking.pop(j["url"], None)
                    counts["unmarked"] += 1
                    message = f"Unmarked: {j['company']} - {j['title']}"
                else:
                    tracking[j["url"]] = {
                        "status": new,
                        "date": date.today().isoformat(),
                        "company": j["company"],
                        "title": j["title"],
                        "note": (tracking.get(j["url"]) or {}).get("note", ""),
                    }
                    counts[new] += 1
                    message = f"Marked {new}: {j['company']} - {j['title']}"
                save_tracking(tracking)
                if f.hide_marked and new is not None:
                    cursor = min(cursor, max(len(f.apply(every_job, tracking)) - 1, 0))
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")  # show cursor, leave alternate screen
        sys.stdout.flush()

    if any(counts.values()):
        rebuild_report(tracking)
        done = ", ".join(f"{n} {name}" for name, n in counts.items() if n)
        print(f"{t.s('Saved', 'green', 'bold')} applications.json ({done}) and updated jobs_report.md.")


# --- commands ----------------------------------------------------------------

def show_list(t, tracking):
    if not tracking:
        print(t.s("Nothing tracked yet.", "gray"))
        return
    rows = sorted(tracking.items(), key=lambda item: item[1]["date"])
    company_w = min(max(len(x["company"]) for _, x in rows), 16)
    for url, x in rows:
        applied = x["status"] == "applied"
        chip = t.s("APPLIED", "green", "bold") if applied else t.s("seen   ", "gray")
        title = f"{t.s(t.pad(t.cut(x['company'], company_w), company_w), 'bold')} {t.link(x['title'], url)}"
        note = f"  {t.s('(' + x['note'] + ')', 'gray')}" if x.get("note") else ""
        print(f"{t.s(x['date'], 'gray')} {chip} {t.s(job_id(url), 'gray')}  {title}{note}")
    counts = {s: sum(1 for x in tracking.values() if x["status"] == s) for s in ("applied", "seen")}
    print(t.s(f"\n{counts['applied']} applied, {counts['seen']} seen", "gray"))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    args = sys.argv[1:]
    if args in ([], ["--all"]):
        return interactive(show_all=bool(args))
    if args[0] not in ("applied", "seen", "unmark", "list"):
        sys.exit(__doc__)

    t = ui.Term()
    command = args[0]
    tracking = load_tracking()

    if command == "list":
        return show_list(t, tracking)

    if len(args) < 2:
        sys.exit(__doc__)

    url, company, title = find_job(args[1], tracking)

    if command == "unmark":
        tracking.pop(url, None)
        print(f"{t.s('Unmarked', 'yellow', 'bold')}: {company} - {title}")
    else:
        tracking[url] = {
            "status": command,
            "date": date.today().isoformat(),
            "company": company,
            "title": title,
            "note": " ".join(args[2:]),
        }
        color = ("green", "bold") if command == "applied" else ("gray",)
        print(f"{t.s('Marked ' + command, *color)}: {company} - {title}")

    save_tracking(tracking)
    rebuild_report(tracking)
    print(t.s("Updated jobs_report.md.", "gray"))


if __name__ == "__main__":
    main()
