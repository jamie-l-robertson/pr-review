"""Shared helpers for the PR review checks. Stdlib only, Python 3.9+."""
import os
import subprocess

# Never worth scanning, linting or feeding to the model.
SKIP_DIRS = ("node_modules/", "public/", ".next/", "dist/", "build/", ".worktrees/")
SKIP_NAMES = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "poetry.lock", "go.sum")
MAX_FILE_BYTES = 100_000


def base_ref():
    """Merge-base to diff against. GitHub gives us the base sha on the PR event."""
    sha = os.environ.get("BASE_SHA") or os.environ.get("BASE")
    if sha:
        return sha
    # Local fallback so the scripts are runnable outside CI.
    return git("merge-base", "origin/main", "HEAD").strip()


def git(*args):
    return subprocess.run(
        ("git",) + args, capture_output=True, text=True, check=False
    ).stdout


def skipped(path):
    return (
        any(d in path for d in SKIP_DIRS)
        or os.path.basename(path) in SKIP_NAMES
    )


def changed_files(base):
    out = git("diff", "--name-only", "--diff-filter=ACMR", base + "...HEAD")
    return [p for p in out.splitlines() if p and not skipped(p)]


def added_lines(base):
    """Yield (path, new_line_number, text) for every line the PR adds.

    Parses `git diff` rather than shelling out per file: one subprocess, and the
    hunk headers give us real new-file line numbers for free.
    """
    diff = git("diff", "--unified=0", base + "...HEAD")
    path, lineno = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path, lineno = line[6:], 0
        elif line.startswith("@@"):
            # @@ -old,n +new,n @@
            try:
                lineno = int(line.split("+")[1].split(",")[0].split(" ")[0])
            except (IndexError, ValueError):
                lineno = 0
        elif line.startswith("+") and not line.startswith("+++"):
            if path and not skipped(path):
                yield path, lineno, line[1:]
            lineno += 1
    return
