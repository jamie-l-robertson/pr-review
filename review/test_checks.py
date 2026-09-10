#!/usr/bin/env python3
"""Runnable check for the two bits of non-trivial logic: PII matching and routing.

No framework. `python3 review/test_checks.py` — silent pass, non-zero on failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detect import route
from pii import luhn, scan_line
from review import content_key, dot, fenced, fix_block, tally


def kinds(text):
    return {k for k, _ in scan_line(text)}


def test_pii_hits():
    assert "email" in kinds('const to = "jamie.robertson@example.co.uk";')
    assert "uk_phone" in kinds('phone: "07700 900123"')
    assert "uk_phone" in kinds('phone: "+44 7700 900123"')
    assert "uk_ni_number" in kinds('ni: "AB 12 34 56 C"')
    # QQ is HMRC's deliberately-invalid placeholder prefix; not real PII.
    assert "uk_ni_number" not in kinds('ni: "QQ 12 34 56 C"')
    assert "uk_postcode" in kinds('postcode: "EH12 5AB"')
    assert "ipv4" in kinds('host = "203.0.113.45"')
    assert "person_name" in kinds('firstName: "Jane Doe",')
    # A name sharing a line with real contact details is a name.
    assert "person_name" in kinds('{ label: "Jane Doe", email: "j@example.com" }')
    # 4242... is Stripe's test card and a valid Luhn number; we want it flagged.
    assert "card_number" in kinds('card: "4242 4242 4242 4242"')


def test_pii_false_positives():
    # A long digit run that isn't a card.
    assert "card_number" not in kinds('id: "1234567890123456"')
    # Loopback and private ranges are noise, not PII.
    assert "ipv4" not in kinds('host = "127.0.0.1"')
    assert "ipv4" not in kinds('host = "192.168.1.10"')
    assert not kinds("// Baal Secundus is the second moon of Baal")
    assert not kinds("const factionName = 'Blood Angels';")
    # A product catalogue is full of capitalised bigrams under a bare `name:`.
    assert not kinds('{ name: "Demon Red", brand: "Vallejo" }')
    assert not kinds('{ name: "Abyss Blue" }')
    # A version string must not read as an IP.
    assert "ipv4" not in kinds('"next": "16.2.11"')


def test_luhn():
    assert luhn("4242424242424242")
    assert not luhn("4242424242424243")


def test_route():
    # SCSS-only PR: nothing to run, so no pnpm install and no semgrep.
    assert route(["styles/_tokens.scss", "README.md"]) == (False, [])
    # Mixed TS + Python picks up both rule sets.
    eslint, configs = route(["lib/paints.ts", "scripts/seed.py"])
    assert eslint is True
    assert "p/typescript" in configs and "p/python" in configs
    # No package.json -> eslint can't run even with TS files present.
    assert route(["lib/paints.ts"], has_package_json=False)[0] is False
    # Workflow YAML is scanned; arbitrary YAML is not.
    assert route([".github/workflows/ci.yml"])[1] == ["p/github-actions"]
    assert route(["docker-compose.yml"])[1] == []
    # An unknown extension is silently agent-only, not a crash.
    assert route(["notes.rst"]) == (False, [])


def test_fix_block_survives_fences_in_the_body():
    # A finding quoting a markdown fence must not end the block early and spill
    # the rest of the prompt into the comment as prose.
    f = {
        "path": "docs/x.md", "line": 3, "severity": "minor", "category": "style",
        "body": "This block is wrong:\n```js\nfoo()\n```\nUse ts instead.",
    }
    out = fix_block(f)
    assert "````text" in out, "wrapper must be wider than the fence it contains"
    assert out.rstrip().endswith("</details>")
    assert "Use ts instead." in out


def test_severity_dots():
    # Worst first, zeroes omitted, and an off-schema severity still renders.
    fs = [{"severity": "nit"}, {"severity": "blocker"}, {"severity": "nit"}]
    assert tally(fs) == "🔴 1 blocker · 🟢 2 nit"
    assert tally([]) == ""
    assert dot("weird") == "⚪"
    assert len({dot(s) for s in ("blocker", "major", "minor", "nit")}) == 4


def test_content_key_ignores_line_numbers():
    # An outdated comment reports line: null, so identity must not involve the
    # line — otherwise nothing ever matches and every outdated thread resolves.
    # Real bodies always exceed the 200-char prefix — the fix block alone is
    # ~600 chars — so build one the way post() does.
    f = {"path": "a.ts", "line": 9, "severity": "major", "category": "security",
         "body": "Secrets are inherited wholesale rather than passed explicitly."}
    body = "🟠 **Inquisitor** · major · security\n\n{}".format(f["body"]) + fix_block(f)
    assert len(body) > 200
    assert content_key("a.ts", body) == content_key("a.ts", body)
    assert content_key("a.ts", body) != content_key("b.ts", body)
    assert content_key("a.ts", body) != content_key("a.ts", body.replace("major", "nit"))
    # Detail past the prefix does not break the match, which is what lets a
    # reworded tail still resolve its thread.
    assert content_key("a.ts", body) == content_key("a.ts", body + "\n\nmore text")


def test_fenced_widths():
    assert fenced("plain").startswith("```\n")
    assert fenced("a ``` b").startswith("````")
    assert fenced("a ````` b").startswith("``````")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
