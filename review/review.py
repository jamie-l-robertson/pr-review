#!/usr/bin/env python3
"""Assemble PR context, ask Claude for findings, post them as inline comments.

Context is deliberately wider than the diff: full post-change contents of every
changed file, plus one hop of imports in each direction. A reviewer that can only
see the hunk invents problems that the surrounding code already solves.
"""
import json
import hashlib
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import MAX_FILE_BYTES, base_ref, changed_files, git, skipped  # noqa: E402

MODEL = os.environ.get("MODEL") or "claude-opus-5"
BUDGET = int(os.environ.get("MAX_CONTEXT_BYTES") or 400_000)
WORKDIR = os.environ.get("WORKING_DIRECTORY", ".")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
PR = os.environ.get("PR_NUMBER", "")
DRY_RUN = bool(os.environ.get("DRY_RUN"))

IMPORT_RX = re.compile(r"""(?:from|import)\s+['"]([^'"]+)['"]""")
TS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs")

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor", "nit"]},
                    "category": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["path", "line", "severity", "category", "body"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "findings"],
    "additionalProperties": False,
}

SYSTEM = """You are reviewing a pull request. Report only defects you can point at in \
the diff: correctness bugs, security holes, data loss, accessibility failures, and \
violations of the project conventions quoted below.

Rules:
- Comment only on lines the PR ADDS. You are given the whole file and its neighbours \
for context, but a finding anchored outside the diff cannot be posted.
- No praise, no summary of what the code does, no style opinions the project's own \
conventions do not state. If the change is fine, return an empty findings array.
- One finding per real problem. Say what breaks and under what input, then the fix.
- Pre-check output (lint/SAST/PII) is included below. It is NOISY. Verify each item \
against the actual code before repeating it, and silently drop false positives — \
in particular, fictional place and character names are not personal data.
- severity: blocker (data loss, security, crash), major (wrong behaviour), \
minor (real but contained), nit (trivial). Do not inflate.

Project conventions follow. They are authoritative for this repo.
"""


def fenced(text, lang=""):
    """Wrap in a backtick run longer than any inside. Markdown files carry their
    own fences, and a naive ``` would end the block early and shred the prompt."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    bar = "`" * max(3, longest + 1)
    return "{}{}\n{}\n{}".format(bar, lang, text, bar)


def read(path, limit=MAX_FILE_BYTES):
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit + 1)
    except OSError:
        return None
    if b"\0" in raw[:1024]:
        return None  # binary
    truncated = len(raw) > limit
    text = raw[:limit].decode("utf-8", "replace")
    return text + ("\n… (truncated)\n" if truncated else "")


def resolve_import(spec, from_path, alias_root):
    """Best-effort TS/JS module resolution. Returns a repo path or None."""
    if spec.startswith("."):
        cand = os.path.normpath(os.path.join(os.path.dirname(from_path), spec))
    elif spec.startswith("@/") and alias_root is not None:
        cand = os.path.normpath(os.path.join(alias_root, spec[2:]))
    else:
        return None  # a package, not our code
    for suffix in ("",) + TS_EXTS + tuple("/index" + e for e in TS_EXTS):
        if os.path.isfile(cand + suffix):
            return cand + suffix
    return None


def alias_root_for(workdir):
    """Read tsconfig paths so `@/x` resolves. Only the common single-alias case."""
    try:
        with open(os.path.join(workdir, "tsconfig.json")) as fh:
            # tsconfig allows comments and trailing commas; regex beats a JSON parser here.
            body = fh.read()
    except OSError:
        return None
    m = re.search(r'"@/\*"\s*:\s*\[\s*"([^"]+)"', body)
    if not m:
        return None
    return os.path.normpath(os.path.join(workdir, m.group(1).replace("/*", "")))


def neighbours(paths, alias_root):
    """One hop each way: what the changed files import, and what imports them."""
    out = []
    seen = set(paths)
    for path in paths:
        if not path.endswith(TS_EXTS):
            continue
        text = read(path) or ""
        for spec in IMPORT_RX.findall(text):
            hit = resolve_import(spec, path, alias_root)
            if hit and hit not in seen and not skipped(hit):
                seen.add(hit)
                out.append(hit)
    # Reverse direction: who imports the changed files.
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem in ("index", "route", "page", "layout"):
            continue  # too common to be a useful signal
        found = git("grep", "-l", "--", "from ['\"].*" + re.escape(stem) + "['\"]")
        for hit in found.splitlines()[:5]:
            if hit and hit not in seen and not skipped(hit):
                seen.add(hit)
                out.append(hit)
    return out


def house_rules():
    parts = []
    for d in {".", WORKDIR}:
        for name in ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md"):
            p = os.path.normpath(os.path.join(d, name))
            text = read(p, 20_000)
            if text:
                parts.append("# {}\n{}".format(p, text))
    return "\n\n".join(parts) if parts else "(No conventions file found in this repo.)"


def findings_context():
    """Whatever the pre-check steps managed to write. Missing == that check was skipped."""
    tmp = os.environ.get("RUNNER_TEMP", "/tmp")
    out = []
    for label, name in (("ESLint", "eslint.json"), ("Semgrep", "semgrep.json"), ("PII scan", "pii.json")):
        text = read(os.path.join(tmp, name), 60_000)
        if text and text.strip() not in ("", "[]", "{}"):
            out.append("## {} output\n{}".format(label, fenced(text, "json")))
    return "\n\n".join(out) if out else "(No pre-check findings — the checks passed or were skipped.)"


def commentable_lines(base):
    """New-side line numbers the PR adds, per path. A comment outside this set
    422s the ENTIRE review, so findings elsewhere get folded into the summary."""
    from common import added_lines
    out = {}
    for path, line, _ in added_lines(base):
        out.setdefault(path, set()).add(line)
    return out


def build_prompt(base):
    paths = changed_files(base)
    if not paths:
        return None, []
    diff = git("diff", "--unified=3", base + "...HEAD", "--", *paths)

    blocks = ["# Diff under review\n" + fenced(diff, "diff")]
    used = len(diff)

    blocks.append("# Full contents of changed files (post-change)")
    for p in paths:
        text = read(p)
        if text is None:
            continue
        if used + len(text) > BUDGET:
            blocks.append("(remaining changed files omitted — context budget reached)")
            break
        used += len(text)
        blocks.append("## {}\n{}".format(p, fenced(text)))

    nb = neighbours(paths, alias_root_for(WORKDIR))
    if nb:
        blocks.append("# Neighbouring files (not changed — context only)")
    for p in nb:
        text = read(p, 8_000)
        if text is None or used + len(text) > BUDGET:
            break
        used += len(text)
        blocks.append("## {}\n{}".format(p, fenced(text)))

    blocks.append("# Pre-check findings (noisy — verify before repeating)\n" + findings_context())
    return "\n\n".join(blocks), paths


def call_claude(prompt):
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM + "\n\n" + house_rules(),
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise SystemExit("Claude declined to review this diff: {}".format(resp.stop_details))
    text = next(b.text for b in resp.content if b.type == "text")
    print("tokens in/out: {}/{}".format(resp.usage.input_tokens, resp.usage.output_tokens), file=sys.stderr)
    return json.loads(text)


def gh(*args, stdin=None):
    r = subprocess.run(("gh",) + args, capture_output=True, text=True, input=stdin)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def fingerprint(path, line, body):
    return hashlib.sha1("{}:{}:{}".format(path, line, body[:60]).encode()).hexdigest()


def existing_fingerprints():
    """So a re-push doesn't repeat findings the author hasn't fixed yet."""
    try:
        raw = gh("api", "repos/{}/pulls/{}/comments".format(REPO, PR), "--paginate")
    except RuntimeError:
        return set()
    return {fingerprint(c["path"], c.get("line") or 0, c["body"]) for c in json.loads(raw)}


def post(result, valid):
    seen = existing_fingerprints()
    comments, orphans = [], []
    for f in result["findings"]:
        label = "**{}** · {}\n\n{}".format(f["severity"], f["category"], f["body"])
        if f["line"] in valid.get(f["path"], ()):
            if fingerprint(f["path"], f["line"], label) not in seen:
                comments.append({"path": f["path"], "line": f["line"], "side": "RIGHT", "body": label})
        else:
            orphans.append("- `{}:{}` — {}".format(f["path"], f["line"], label.replace("\n\n", " ")))

    body = result["summary"]
    if orphans:
        body += "\n\n<details><summary>Findings outside the diff ({})</summary>\n\n{}\n</details>".format(
            len(orphans), "\n".join(orphans))
    payload = {"event": "COMMENT", "body": body, "comments": comments}

    path = "repos/{}/pulls/{}/reviews".format(REPO, PR)
    try:
        gh("api", path, "--input", "-", stdin=json.dumps(payload))
    except RuntimeError as e:
        # One bad line rejects the whole review — don't lose it.
        print("inline review rejected ({}); posting summary only".format(e), file=sys.stderr)
        payload["comments"] = []
        payload["body"] = body + "\n\n_(Inline anchoring failed; findings listed above.)_"
        gh("api", path, "--input", "-", stdin=json.dumps(payload))
    print("posted {} inline, {} in summary".format(len(comments), len(orphans)), file=sys.stderr)


def main():
    base = base_ref()
    prompt, paths = build_prompt(base)
    if not prompt:
        print("nothing reviewable in this diff", file=sys.stderr)
        return
    if DRY_RUN:
        print(prompt)
        print("\n--- {} files, {} bytes of context, model {} ---".format(
            len(paths), len(prompt), MODEL), file=sys.stderr)
        return
    post(call_claude(prompt), commentable_lines(base))


if __name__ == "__main__":
    main()
