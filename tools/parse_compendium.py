#!/usr/bin/env python3
"""Parse the Agricola General Compendium PDF into a structured card DB.

Usage:
    python tools/parse_compendium.py <path-to-compendium.pdf> [out.json]

Produces a JSON list of card entries:
    {code, deck, num, edition, type, name, meta, vp, cost, prereq,
     players, text, rulings}

The compendium is two-column A4. We parse via font metadata:
  - card codes:  CMBX12 @ ~14.3pt, matching ^<deck><num>$
  - card names:  CMBX8 @ 8pt immediately after a code
  - meta line:   CMR5 @ 5pt parenthesized (cost/VP/req/players)
  - body text:   CMR9; rulings start with a CMSY '⇒' and use CMR8/CMR9
Sections (deck/type/edition) come from CMBX10/12 headings.
"""

import json
import re
import sys

import pymupdf

BOUNDARY = 278
CODE_RE = re.compile(r"^(E|I|K|M|WM|FR|FL|WA|G|Z|NL|Ö|Č|P|BI|A|B|C|D)(\d{1,3})$")
HEADING_RE = re.compile(
    r"(Minor Improvements|Occupations|Major Improvements)\s*\(([^)]+)\)")

LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
             "’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-",
             "¨O": "Ö", "ˇC": "Č"}  # combining accents in CM fonts


def norm(s):
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    return s


def find_gutter(spans):
    """Locate the column gutter: the widest x-gap between span boxes
    in the middle of the page (span = (.., x0, y, x1))."""
    xs = sorted((s[3], s[5]) for s in spans if s[2] < 12)
    events = []
    for x0, x1 in xs:
        events.append((x0, 1))
        events.append((x1, -1))
    events.sort()
    depth = 0
    gap_start = None
    best = (0, BOUNDARY)
    for x, delta in events:
        depth += delta
        if depth == 0:
            gap_start = x
        elif depth == 1 and delta == 1 and gap_start is not None:
            width = x - gap_start
            mid = (gap_start + x) / 2
            if 200 < mid < 450 and width > best[0]:
                best = (width, mid)
            gap_start = None
    return best[1]


def page_lines(page):
    """Lines in column-reading order; each line is a list of spans
    (text, font, size, x, y, x1) sharing a y position within one column."""
    d = page.get_text("dict")
    spans = []
    for block in d["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                text = norm(span["text"]).strip()
                if not text:
                    continue
                x, y = span["origin"]
                spans.append((text, span["font"], round(span["size"], 1),
                              x, y, span["bbox"][2]))
    if not spans:
        return []
    boundary = find_gutter(spans)
    out = []
    for col in (sorted([s for s in spans if s[3] < boundary],
                       key=lambda s: (round(s[4], 1), s[3])),
                sorted([s for s in spans if s[3] >= boundary],
                       key=lambda s: (round(s[4], 1), s[3]))):
        cur_y, line = None, []
        for s in col:
            if cur_y is None or abs(s[4] - cur_y) > 2.5:
                if line:
                    out.append(line)
                line, cur_y = [s], s[4]
            else:
                line.append(s)
        if line:
            out.append(line)
    return out


def parse_meta(meta):
    out = {"vp": 0, "cost": "", "prereq": "", "players": ""}
    inner = meta.strip()
    if inner.startswith("("):
        inner = inner[1:]
    if inner.endswith(")"):
        inner = inner[:-1]
    for part in re.split(r"[.;] ?", inner):
        part = part.strip().rstrip(".")
        if not part:
            continue
        m = re.match(r"^(-?\d+)\s?VP$", part)
        if m:
            out["vp"] = int(m.group(1))
            continue
        m = re.match(r"^Cost\s+(.*)$", part)
        if m:
            out["cost"] = m.group(1).strip()
            continue
        m = re.match(r"^(?:Req|Prerequisite)s?\.?\s+(.*)$", part, re.I)
        if m:
            out["prereq"] = m.group(1).strip()
            continue
        if re.match(r"^\d\s*[-+]\s*\d?\s*players?$", part) or re.match(r"^\d\+$", part):
            out["players"] = part
            continue
        out["prereq"] = (out["prereq"] + "; " + part).strip("; ")
    return out


def parse(pdf_path):
    doc = pymupdf.open(pdf_path)
    cards = []
    deck = ctype = edition = None
    cur = None
    name_stash = None
    meta_stash = []
    state = None  # None | "name" | "meta" | "body"

    def flush():
        nonlocal cur, state
        if cur is not None:
            cur["name"] = " ".join(cur["name_parts"]).strip()
            cur["text"] = " ".join(cur["body"]).strip()
            cur["rulings"] = [" ".join(r).strip() for r in cur["ruling_items"]]
            for k in ("name_parts", "body", "ruling_items"):
                del cur[k]
            cards.append(cur)
        cur, state = None, None

    for pno in range(22, 165):  # printed pages 23..165
        for line in page_lines(doc[pno]):
            joined = " ".join(s[0] for s in line)
            max_size = max(s[2] for s in line)
            all_bold = all(s[1].startswith("CMBX") for s in line)
            code_span = next((s for s in line
                              if s[1].startswith("CMBX") and s[2] > 12
                              and CODE_RE.match(s[0])), None)

            # Section headings (large bold, no card code).
            if all_bold and max_size >= 10 and code_span is None:
                m = HEADING_RE.search(joined)
                if m:
                    flush()
                    ctype = {"Minor Improvements": "minor",
                             "Occupations": "occupation",
                             "Major Improvements": "major"}[m.group(1)]
                    deck = m.group(2).strip()
                    if "Original edition" in joined:
                        edition = "Original"
                    elif "Revised edition" in joined or "FotM" in joined:
                        edition = "Revised" if "Revised" in joined else edition
                    continue
                if "Original edition" in joined:
                    edition = "Original"
                    continue
                if "Revised edition" in joined:
                    edition = "Revised"
                    continue
                continue

            # New card: the line holding the big bold code; small bold
            # spans on the same line are the card name. If the meta line
            # wrapped, the name sits on the previous all-bold line
            # (kept in name_stash) and small spans share the code line.
            if code_span is not None:
                name_parts = [s[0] for s in line
                              if s is not code_span and s[1] == "CMBX8"]
                meta_parts = list(meta_stash)
                meta_stash = []
                meta_parts += [s[0] for s in line
                               if s[2] <= 6 and not s[1].startswith("CMBX")]
                stash, name_stash = name_stash, None
                if not name_parts and stash:
                    name_parts = stash
                elif stash and cur is not None:
                    (cur["ruling_items"][-1] if cur["ruling_items"]
                     else cur["body"]).append(" ".join(stash))
                flush()
                m = CODE_RE.match(code_span[0])
                cur = {"code": code_span[0], "deck": m.group(1),
                       "num": int(m.group(2)),
                       "edition": edition, "type": ctype,
                       "name_parts": name_parts,
                       "meta": " ".join(meta_parts), "body": [],
                       "ruling_items": []}
                state = "meta" if meta_parts else "name"
                if cur["meta"].rstrip().endswith(")"):
                    state = "body"
                continue

            if cur is None:
                continue

            # Hold a pure CMBX8 line: it may be the next card's name
            # (when the meta line wraps). Ruling reference lines are
            # excluded by requiring at least one non-code-like token.
            if (all(s[1] == "CMBX8" for s in line)
                    and any(not re.match(r"^[A-ZÖČ]{1,2}\d", t)
                            for t in joined.split())):
                if name_stash:
                    (cur["ruling_items"][-1] if cur["ruling_items"]
                     else cur["body"]).append(" ".join(name_stash))
                name_stash = [joined]
                meta_stash = []
                continue
            # A tiny (5pt) line while a name is stashed is the upcoming
            # card's meta (its code line may come after it).
            if name_stash and all(s[2] <= 6 for s in line):
                meta_stash.append(joined)
                continue
            if name_stash:
                (cur["ruling_items"][-1] if cur["ruling_items"]
                 else cur["body"]).append(" ".join(name_stash))
                for m_ln in meta_stash:
                    (cur["ruling_items"][-1] if cur["ruling_items"]
                     else cur["body"]).append(m_ln)
                name_stash = None
                meta_stash = []

            small = all(s[2] <= 6 for s in line)
            # Name continuation (wrapped bold name line before meta/body).
            if state == "name" and all_bold and max_size <= 9:
                cur["name_parts"].append(joined)
                continue
            # Meta line(s): 5pt parenthesized.
            if small and state in ("name", "meta"):
                cur["meta"] = (cur["meta"] + " " + joined).strip()
                state = "body" if cur["meta"].rstrip().endswith(")") else "meta"
                continue
            if state in ("name", "meta"):
                state = "body"
            if joined.startswith("⇒"):
                rest = joined.lstrip("⇒").strip()
                cur["ruling_items"].append([rest] if rest else [])
                continue
            if cur["ruling_items"]:
                cur["ruling_items"][-1].append(joined)
            else:
                cur["body"].append(joined)
    flush()

    def clean(s):
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"([a-z])- ([a-z])", r"\1\2", s)  # de-hyphenate
        return s.strip()

    for c in cards:
        c.update(parse_meta(c["meta"]))
        c["text"] = clean(c["text"])
        c["rulings"] = [clean(r) for r in c["rulings"]]
    return cards


def main():
    pdf_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "compendium_cards.json"
    cards = parse(pdf_path)
    with open(out_path, "w") as f:
        json.dump(cards, f, indent=1, ensure_ascii=False)
        f.write("\n")
    import collections
    counts = collections.Counter((c["edition"], c["type"], c["deck"]) for c in cards)
    for key in sorted(counts, key=str):
        print(key, counts[key])
    print("total:", len(cards))
    missing_name = [c["code"] for c in cards if not c["name"]]
    missing_text = [c["code"] for c in cards if not c["text"]]
    print("missing names:", len(missing_name), missing_name[:10])
    print("missing text:", len(missing_text), missing_text[:10])


if __name__ == "__main__":
    main()
