#!/usr/bin/env python3
"""Decide which checks are worth running, from the languages present in the diff.

A docs-only or SCSS-only PR shouldn't pay for `pnpm install` — that's the slowest
step in the job. Unknown extensions contribute nothing, so a new file type
degrades to "agent-only review" rather than crashing.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_ref, changed_files, git  # noqa: E402

# extension -> semgrep registry configs. Explicit configs only: `--config auto`
# phones home and `semgrep ci` wants an account.
SEMGREP = {
    ".ts": ("p/typescript", "p/react", "p/owasp-top-ten"),
    ".tsx": ("p/typescript", "p/react", "p/owasp-top-ten"),
    ".js": ("p/typescript", "p/react", "p/owasp-top-ten"),
    ".jsx": ("p/typescript", "p/react", "p/owasp-top-ten"),
    ".mjs": ("p/typescript", "p/owasp-top-ten"),
    ".cjs": ("p/typescript", "p/owasp-top-ten"),
    ".py": ("p/python",),
    ".go": ("p/golang",),
    ".rb": ("p/ruby",),
    ".php": ("p/php",),
    ".java": ("p/java",),
    ".sql": ("p/sql-injection",),
    ".tf": ("p/terraform",),
    ".dockerfile": ("p/dockerfile",),
}
ESLINT_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
REACT_EXTS = (".tsx", ".jsx")

# Agent configuration is executable in practice: hooks run, MCP servers are
# launched, instruction files steer coding agents. Nothing else in the pipeline
# looks at any of it.
LOCKFILES = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock")
AGENT_PATHS = (".claude/", ".cursor/", ".mcp.json", ".superpowers/", ".codex/",
               ".github/copilot", "skill.md", "agents.md", "claude.md")

# Paths where a missed defect is expensive: auth, data, money, admin, and the CI
# that holds the keys to all of it. A diff touching these gets the better model
# whatever its size.
SENSITIVE = ("auth", "session", "login", "password", "token", "secret", "credential",
             "payment", "billing", "checkout", "admin", "migration", "db/", "schema",
             "/api/", "proxy.ts", "middleware", "ratelimit", "rate-limit", ".github/")
BIG_FILES = 15
BIG_LINES = 400


def tier(paths, added):
    """-> "elevated" or "routine". Pure, so the routing is testable without a diff."""
    if len(paths) > BIG_FILES or added > BIG_LINES:
        return "elevated"
    low = [p.lower() for p in paths]
    if any(s in p for p in low for s in SENSITIVE):
        return "elevated"
    return "routine"


def route(paths, has_package_json=True):
    """-> (run_eslint, sorted semgrep configs). Pure, so it's testable."""
    configs = set()
    run_eslint = False
    for p in paths:
        name = os.path.basename(p).lower()
        ext = ".dockerfile" if name.startswith("dockerfile") else os.path.splitext(p)[1].lower()
        if ext in ESLINT_EXTS:
            run_eslint = True
        if ext in (".yml", ".yaml"):
            # Workflow files are worth scanning; arbitrary YAML config is not.
            if p.startswith(".github/"):
                configs.add("p/github-actions")
            continue
        configs.update(SEMGREP.get(ext, ()))
    return (run_eslint and has_package_json), sorted(configs)


def main():
    workdir = os.environ.get("WORKING_DIRECTORY", ".")
    paths = changed_files(base_ref())
    has_pkg = os.path.isfile(os.path.join(workdir, "package.json"))
    run_eslint, configs = route(paths, has_pkg)

    # react-doctor takes explicit paths, so it only ever sees the changed
    # components. Written to a file rather than an output to dodge quoting.
    # changed_files() drops lockfiles, so ask git directly. A PR that does not
    # touch dependencies does not need its dependency tree audited.
    raw = git("diff", "--name-only", base_ref() + "...HEAD").splitlines()
    deps_changed = any(os.path.basename(p) in LOCKFILES for p in raw)

    agent = [p for p in paths if any(a in p.lower() for a in AGENT_PATHS)]
    with open(os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"),
                           "agent-files.txt"), "w") as fh:
        fh.write("\n".join(agent))

    react = [p for p in paths if p.lower().endswith(REACT_EXTS)] if has_pkg else []
    tmp = os.environ.get("RUNNER_TEMP", "/tmp")
    with open(os.path.join(tmp, "react-files.txt"), "w") as fh:
        fh.write("\n".join(react))

    numstat = git("diff", "--numstat", base_ref() + "...HEAD")
    added = sum(int(l.split("\t")[0]) for l in numstat.splitlines()
                if l.split("\t")[0].isdigit())

    out = {
        "run_eslint": "true" if run_eslint else "false",
        "semgrep_configs": " ".join("--config " + c for c in configs),
        "changed_count": str(len(paths)),
        "run_react_doctor": "true" if react else "false",
        "run_skillspector": "true" if agent else "false",
        "run_audit": "true" if (deps_changed and has_pkg) else "false",
        "tier": tier(paths, added),
        "added_lines": str(added),
    }
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            for k, v in out.items():
                fh.write("{}={}\n".format(k, v))
    print(json.dumps(out, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
