"""Terminal output for fetch_jobs.py: colors, tables and a live progress bar.

Standard library only. Colors, hyperlinks and the live progress line are used
only when stdout is a real terminal; piped output (or NO_COLOR, or
--no-color) stays plain text with the same layout.
"""
import os
import re
import shutil
import sys

STYLES = {
    "bold": "1", "dim": "2", "invert": "7",
    "red": "31", "green": "32", "yellow": "33", "blue": "34",
    "magenta": "35", "cyan": "36", "gray": "90",
}
ANSI_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")


class Term:
    def __init__(self, color=None):
        self.tty = sys.stdout.isatty()
        auto = self.tty and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        self.color = auto if color is None else (auto and color)
        if self.color and os.name == "nt":
            os.system("")  # turns on ANSI escape handling in the Windows console
        columns = shutil.get_terminal_size((110, 24)).columns if self.tty else 110
        self.width = max(70, min(columns, 150))

    # --- styling ------------------------------------------------------------

    def s(self, text, *styles):
        if not self.color or not styles:
            return str(text)
        codes = ";".join(STYLES[x] for x in styles)
        return f"\x1b[{codes}m{text}\x1b[0m"

    def link(self, text, url):
        """OSC 8 hyperlink: clickable in Windows Terminal, iTerm2, VS Code, ..."""
        if not self.color or not url:
            return str(text)
        return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"

    # --- measuring ----------------------------------------------------------

    @staticmethod
    def vlen(text):
        return len(ANSI_RE.sub("", text))

    def pad(self, text, width, right=False):
        gap = max(width - self.vlen(text), 0)
        return " " * gap + text if right else text + " " * gap

    @staticmethod
    def cut(text, width):
        text = str(text)
        if width <= 0:
            return ""
        return text if len(text) <= width else text[: max(width - 1, 0)] + "…"

    def rule(self, label="", fill="─"):
        if not label:
            return self.s(fill * self.width, "gray")
        left = f"{fill}{fill} "
        right = " " + fill * max(self.width - len(left) - len(label) - 1, 2)
        return self.s(left, "gray") + self.s(label, "bold") + self.s(right, "gray")


# --- pieces -------------------------------------------------------------------

def banner(t, subtitle):
    print()
    print(t.s("job-hunter", "bold", "cyan") + "  " + t.s(subtitle, "gray"))


class Progress:
    """One live line while the boards are being fetched."""

    def __init__(self, t, total):
        self.t, self.total, self.done = t, total, 0

    def update(self, label, ok=True):
        self.done += 1
        if not self.t.tty:
            return
        bar_width = 24
        filled = int(bar_width * self.done / max(self.total, 1))
        bar = self.t.s("█" * filled, "cyan") + self.t.s("░" * (bar_width - filled), "gray")
        mark = self.t.s("✓", "green") if ok else self.t.s("✗", "red")
        line = f"  {bar} {self.done}/{self.total}  {mark} {self.t.cut(label, 30)}"
        sys.stdout.write("\r\x1b[2K" + line)
        sys.stdout.flush()

    def finish(self):
        if self.t.tty:
            sys.stdout.write("\r\x1b[2K")
            sys.stdout.flush()


def boards_table(t, rows):
    """rows: dicts with name, ats, total, dev, shown, new, secs, error."""
    print(t.rule("Boards"))
    name_w = max([len(r["name"]) for r in rows] + [5])
    ats_w = max([len(r["ats"]) for r in rows] + [3])
    head = (f"  {t.pad('Board', name_w)}  {t.pad('ATS', ats_w)}  "
            f"{t.pad('Open', 5, True)}  {t.pad('Dev', 4, True)}  "
            f"{t.pad('Shown', 5, True)}  {t.pad('New', 4, True)}  {t.pad('Time', 5, True)}")
    print(t.s(head, "gray"))
    for r in rows:
        if r["error"]:
            print(f"  {t.s(t.pad(r['name'], name_w), 'red', 'bold')}  {t.s(t.pad(r['ats'], ats_w), 'gray')}  "
                  f"{t.s('FAILED', 'red', 'bold')}  {t.s(t.cut(r['error'], t.width - name_w - ats_w - 20), 'red')}")
            continue
        shown = t.s(t.pad(str(r["shown"]), 5, True), "green", "bold") if r["shown"] else t.s(t.pad("0", 5, True), "gray")
        new = t.s(t.pad(str(r["new"]), 4, True), "yellow", "bold") if r["new"] else t.s(t.pad("·", 4, True), "gray")
        print(f"  {t.s(t.pad(r['name'], name_w), 'bold')}  {t.s(t.pad(r['ats'], ats_w), 'gray')}  "
              f"{t.pad(str(r['total']), 5, True)}  {t.pad(str(r['dev']), 4, True)}  {shown}  {new}  "
              f"{t.s(t.pad(format(r['secs'], '.1f') + 's', 5, True), 'gray')}")
    print(t.s("  Open = postings on the board, Dev = dev roles open to you, "
              "Shown = not yet seen/applied and listed", "gray"))


def summary(t, dev_total, shown, new, handled, elapsed):
    print()
    parts = [
        f"{t.s(dev_total, 'bold')} dev postings",
        f"{t.s(shown, 'green', 'bold')} to review",
        f"{t.s(new, 'yellow', 'bold')} new" if new else t.s("0 new", "gray"),
        t.s(f"{handled} already handled", "gray"),
        t.s(f"{elapsed:.1f}s", "gray"),
    ]
    print("  " + t.s("  ·  ", "gray").join(parts))


def job_list(t, title, jobs, tracking, limit, first_rank=1, empty="Nothing here."):
    print()
    print(t.rule(f"{title} ({len(jobs)})"))
    if not jobs:
        print(t.s(f"  {empty}", "gray"))
        return
    shown = jobs[:limit]
    top = max([j["match_score"] for j in shown] + [1])
    company_w = min(max(len(j["company"]) for j in shown), 14)
    loc_w = min(max(len(_loc(j)) for j in shown), 26 if t.width >= 100 else 14)
    score_w = 5 if any(j.get("llm_fit") is not None for j in shown) else 3
    fixed = 2 + 2 + 1 + score_w + 1 + company_w + 1 + 1 + loc_w + 1 + 11 + 1 + 6  # all but the title
    title_w = max(t.width - fixed, 18)
    for rank, j in enumerate(shown, start=first_rank):
        ratio = j["match_score"] / top
        tier = "green" if ratio >= 0.8 else "yellow" if ratio >= 0.5 else "gray"
        score = t.s(t.pad(str(j["match_score"]), score_w, True), tier, "bold")
        if j.get("llm_fit") is not None:
            score = t.s(t.pad(f"{j['llm_fit']}/10", score_w, True), "cyan", "bold")
        shown_title = t.cut(j["title"], title_w)
        title_cell = t.link(shown_title, j["url"])
        title_cell += " " * max(title_w - len(shown_title), 0)
        mode = t.s("remote", "green") if j.get("remote") else t.s("on-site", "magenta")
        tags = t.pad(t.s("NEW", "yellow", "bold") if _is_new(j) else "", 3) + " " + t.pad(mode, 7)
        print(f"  {t.s(t.pad(str(rank), 2, True), 'gray')} {score} "
              f"{t.s(t.pad(t.cut(j['company'], company_w), company_w), 'bold')} {title_cell} "
              f"{t.s(t.pad(t.cut(_loc(j), loc_w), loc_w), 'gray')} {tags} {t.s(j['id'], 'gray')}")
    hidden = len(jobs) - len(shown)
    if hidden > 0:
        print(t.s(f"  … {hidden} more in jobs_report.md", "gray"))


def removed_list(t, jobs, limit=8):
    if not jobs:
        return
    print()
    print(t.rule(f"No longer listed ({len(jobs)})"))
    for j in jobs[:limit]:
        text = t.cut(f"{j['company']} - {j['title']} ({j['location']})", t.width - 6)
        print("  " + t.s("- " + text, "gray"))
    if len(jobs) > limit:
        print(t.s(f"  … and {len(jobs) - limit} more", "gray"))


def footer(t, failed, report_path):
    print()
    if failed:
        print("  " + t.s("!", "red", "bold") + f" Boards that failed (previous postings kept): "
              + t.s(", ".join(failed), "red"))
    cmd = lambda c: t.s(c, "cyan")
    print(f"  Report: {t.s(report_path, 'bold')}   Mark postings: {cmd('python track.py')}"
          f"   Include non-remote: {cmd('python track.py --all')}")
    print()


# --- helpers ------------------------------------------------------------------

def _loc(job):
    loc = job.get("location") or ""
    # Greenhouse sometimes prefixes the role's own country, keep the first place only.
    return loc.split(";")[0].split("|")[0].strip()


def _is_new(job):
    from fetch_jobs import is_new  # late import: fetch_jobs imports this module
    return is_new(job)
