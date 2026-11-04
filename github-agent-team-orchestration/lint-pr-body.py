#!/usr/bin/env python3
"""Lint a PR title + body against the technical-writer shape. Usage:
   python3 lint-pr-body.py <title-file> <body-file>
Exit 0 = clean, 1 = findings (printed one per line)."""
import re, sys

SECTIONS = ["## Why", "## What changes", "## How it works", "## How to check it",
            "## Risks and choices", "## For the reviewer"]
NO_CODE = {"## Why", "## What changes", "## How to check it"}
ONE_CODE = {"## How it works", "## Risks and choices"}
KNOWN_ACRONYMS = {"PR", "CI", "API", "URL", "HTTP", "JSON", "SQL", "ID", "IOS", "UI", "OK", "WIP", "TDD",
                  "AI", "ISO", "UTC", "CSS", "HTML", "DNS", "TLS", "DB", "RC", "PAT", "SHA"}
AUDIENCE = re.compile(r"^- (A |An |The |Nothing visible|Members|Admins|Operators|Maintainers|Everyone|Nobody)", re.I)

def main(title_path, body_path):
    findings = []
    title = open(title_path).read().strip()
    body = open(body_path).read()
    if len(title) > 72:
        findings.append(f"title: {len(title)} chars (max 72)")
    if not re.match(r"^[a-z]+(\([a-z0-9-]+\))?!?: \S", title):
        findings.append("title: not `type(scope): …` conventional form")
    main_part, _, appendix = body.partition("<details>")
    words = len(main_part.split())
    if not 300 <= words <= 700:
        findings.append(f"body: {words} words before the appendix (budget 300-700)")
    # sections present and ordered
    pos = [main_part.find(h + "\n") if main_part.find(h + "\n") >= 0 else main_part.find(h) for h in SECTIONS]
    for h, p in zip(SECTIONS, pos):
        if p < 0:
            findings.append(f"missing section {h}")
    if all(p >= 0 for p in pos) and pos != sorted(pos):
        findings.append("sections out of order")
    if "Not verified:" not in main_part:
        findings.append("How to check it: no 'Not verified:' item")
    if not re.search(r"^Closes #\d+", main_part, re.M):
        findings.append("footer: no `Closes #N` line")
    if "<details>" not in body:
        findings.append("no <details> appendix for test evidence")
    # per-section checks
    chunks = re.split(r"^(## .+)$", main_part, flags=re.M)
    for i in range(1, len(chunks) - 1, 2):
        head, text = chunks[i].strip(), chunks[i + 1]
        ticks = len(re.findall(r"`[^`]+`", text))
        if head in NO_CODE and ticks:
            findings.append(f"{head}: {ticks} code span(s); this section allows none")
        if head in ONE_CODE and ticks > 1:
            findings.append(f"{head}: {ticks} code spans; at most one")
        if head == "## What changes":
            bullets = [l for l in text.splitlines() if l.startswith("- ")]
            if not 3 <= len(bullets) <= 6:
                findings.append(f"What changes: {len(bullets)} bullets (want 3-6)")
            for b in bullets:
                if not AUDIENCE.match(b):
                    findings.append(f"What changes bullet does not start with who notices: {b[:50]!r}")
        for sent in re.split(r"(?<=[.!?])\s+", re.sub(r"`[^`]+`", "X", text).replace("\n", " ")):
            n = len(sent.split())
            if n > 25:
                findings.append(f"{head}: {n}-word sentence: {sent[:60]!r}")
    # bare issue numbers as sentence subjects
    for m in re.finditer(r"(?:^|\. )#\d+ ", main_part, re.M):
        findings.append(f"number as sentence subject at {m.group(0).strip()!r}")
    # fragments: bullet or sentence with no verb-ish word is hard; flag 1-3 word sentences
    for sent in re.split(r"(?<=[.!?])\s+", main_part.replace("\n", " ")):
        w = sent.strip().split()
        if 0 < len(w) <= 2 and not sent.startswith("#") and not sent.startswith("Closes") \
                and re.search(r"[A-Za-z]{3,}", sent):
            findings.append(f"fragment: {sent.strip()!r}")
    # unexpanded acronyms
    seen = set()
    for m in re.finditer(r"\b([A-Z]{3,6})\b", re.sub(r"`[^`]+`", "", main_part)):  # 2-letter tokens are country codes etc.
        a = m.group(1)
        if a in KNOWN_ACRONYMS or a in seen:
            continue
        seen.add(a)
        window = main_part[max(0, m.start() - 120): m.end() + 120]
        if "(" not in window:
            findings.append(f"acronym {a} not expanded near first use")
    if re.search(r"[\U0001F300-\U0001FAFF]", main_part.split("Closes #")[0]):
        findings.append("emoji in body")
    for f in findings:
        print(f)
    return 1 if findings else 0

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
