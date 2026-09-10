#!/usr/bin/env python3
"""Scan the lines a PR *adds* for personal data. Advisory only — never a gate.

gitleaks already covers credentials and is the hard gate. This catches the other
half: a real person's email or address landing in a test fixture or seed script.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import added_lines, base_ref  # noqa: E402

PATTERNS = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),
    # UK mobile/landline, with or without +44.
    ("uk_phone", re.compile(r"(?<!\d)(?:\+44\s?7\d{3}|\(?07\d{3}\)?)\s?\d{3}\s?\d{3}(?!\d)")),
    ("uk_ni_number", re.compile(r"(?<![A-Z0-9])[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D](?![A-Z0-9])")),
    ("uk_postcode", re.compile(r"(?<![A-Z0-9])[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}(?![A-Z0-9])", re.I)),
    ("ipv4", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")),
    ("card_number", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
)

# A capitalised bigram under an unambiguously person-shaped key. A bare `name:`
# is NOT one — product catalogues are full of "Demon Red" and "Abyss Blue", and a
# check that is 100% false positives just teaches everyone to skip the PII block.
NAME_KEY = re.compile(
    r"(?:first_?name|last_?name|full_?name|sur_?name|customer_?name|contact_?name|"
    r"recipient|billing_?name|cardholder)\s*[:=]\s*"
    r"[\"']([A-Z][a-z]+(?: [A-Z][a-z]+)+)[\"']",
    re.I,
)
# Any capitalised bigram is a name if it shares a line with real contact details.
NAME_NEARBY = re.compile(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b")

# Loopback/RFC1918/documentation ranges and version-ish strings are noise.
BORING_IPS = re.compile(r"^(0\.|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|255\.|1\.0\.0$)")


def luhn(digits):
    """An unverified card regex fires on every long digit run — checksum it."""
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def scan_line(text):
    """-> list of (kind, matched_text). Pure, so it's testable."""
    hits = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            raw = m.group(0)
            if kind == "card_number":
                digits = re.sub(r"\D", "", raw)
                if not (13 <= len(digits) <= 19) or not luhn(digits):
                    continue
            elif kind == "ipv4":
                if any(int(o) > 255 for o in raw.split(".")) or BORING_IPS.match(raw):
                    continue
            elif kind == "uk_postcode":
                # Hex colours, css classes and ids trip the postcode shape constantly.
                if re.search(r"[#$.]\s*$|^[0-9a-f]{6}$", raw, re.I):
                    continue
            hits.append((kind, raw))
    for m in NAME_KEY.finditer(text):
        hits.append(("person_name", m.group(1)))
    if any(k in ("email", "uk_phone", "uk_postcode") for k, _ in hits):
        for m in NAME_NEARBY.finditer(text):
            hits.append(("person_name", m.group(1)))
    return hits


def main():
    findings = []
    for path, lineno, text in added_lines(base_ref()):
        if len(text) > 2000:  # minified or generated
            continue
        for kind, raw in scan_line(text):
            findings.append({
                "path": path,
                "line": lineno,
                "kind": kind,
                "snippet": text.strip()[:160],
                "match": raw,
            })
    json.dump(findings[:200], sys.stdout, indent=2)
    print(file=sys.stdout)
    print("pii: {} finding(s)".format(len(findings)), file=sys.stderr)


if __name__ == "__main__":
    main()
