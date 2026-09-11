"""Read-only tools over the checked-out repo, for the reviewer to explore with.

The runner already has the whole repo on disk at fetch-depth 0. Before this, the
bundle was chosen up front and capped, so a defect whose evidence sat in a file
nobody thought to include could not be found at all — which is what made findings
show up on a second push that were in the first diff all along.

Everything here is read-only and confined to the repo. Paths are resolved and
checked against the root, no argument reaches a shell, and every result is capped
so one call cannot blow the context budget.
"""
import os
import subprocess

ROOT = os.path.realpath(os.getcwd())
# Every tool result is replayed on each later turn of the loop, so an oversized
# one is not paid for once but once per remaining turn. Keep them tight and make
# the model ask again for more.
MAX_BYTES = 60_000
MAX_LINES = 200
MAX_HITS = 40


def _resolve(path):
    """Absolute path inside the repo, or None. Blocks traversal and symlink escapes."""
    if not path or os.path.isabs(path):
        return None
    full = os.path.realpath(os.path.join(ROOT, path))
    if full != ROOT and not full.startswith(ROOT + os.sep):
        return None
    return full


def _run(args):
    return subprocess.run(args, capture_output=True, text=True, cwd=ROOT, check=False)


def read_file(path, start_line=1, end_line=0):
    """Read a file from the repository, with line numbers.

    Args:
        path: Repository-relative path, e.g. lib/mail.ts.
        start_line: First line to return, 1-based.
        end_line: Last line to return. 0 means to the end, capped.
    """
    full = _resolve(path)
    if full is None:
        return "refused: {} is outside the repository".format(path)
    if not os.path.isfile(full):
        return "no such file: {}".format(path)
    try:
        with open(full, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError as e:
        return "could not read {}: {}".format(path, e)
    if b"\0" in raw[:1024]:
        return "{} is binary".format(path)
    lines = raw[:MAX_BYTES].decode("utf-8", "replace").splitlines()
    start = max(1, int(start_line or 1))
    end = len(lines) if not end_line else min(len(lines), int(end_line))
    end = min(end, start + MAX_LINES - 1)
    if start > len(lines):
        return "{} has only {} lines".format(path, len(lines))
    body = "\n".join("{}\t{}".format(i, lines[i - 1]) for i in range(start, end + 1))
    more = "" if end >= len(lines) else "\n… {} more lines".format(len(lines) - end)
    return body + more


def search(pattern, path_glob=""):
    """Search the repository for a literal string or regex. Returns file:line matches.

    Args:
        pattern: Text or regular expression to find.
        path_glob: Optional path filter, e.g. "lib/*.ts" or "app/".
    """
    args = ["git", "grep", "-n", "-I", "-E", "--", pattern]
    if path_glob:
        if _resolve(path_glob.split("*")[0].rstrip("/") or ".") is None:
            return "refused: {} is outside the repository".format(path_glob)
        args = ["git", "grep", "-n", "-I", "-E", pattern, "--", path_glob]
    r = _run(args)
    hits = [l for l in r.stdout.splitlines() if l][:MAX_HITS]
    if not hits:
        return "no matches"
    extra = "" if len(hits) < MAX_HITS else "\n… more matches not shown; narrow the pattern"
    return "\n".join(hits) + extra


def list_files(directory="."):
    """List the repository's tracked files under a directory.

    Args:
        directory: Repository-relative directory, e.g. lib or app/api.
    """
    if _resolve(directory) is None:
        return "refused: {} is outside the repository".format(directory)
    r = _run(["git", "ls-files", "--", directory])
    names = [l for l in r.stdout.splitlines() if l][:MAX_HITS * 4]
    return "\n".join(names) if names else "nothing tracked under {}".format(directory)


def history(path, count=5):
    """Recent commits touching a path, newest first, with subject and date.

    Args:
        path: Repository-relative file or directory.
        count: How many commits to return.
    """
    if _resolve(path) is None:
        return "refused: {} is outside the repository".format(path)
    r = _run(["git", "log", "--max-count={}".format(min(int(count or 5), 20)),
              "--date=short", "--format=%h %ad %s", "--", path])
    return r.stdout.strip() or "no history for {}".format(path)
