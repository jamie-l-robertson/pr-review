#!/usr/bin/env python3
"""Replay the reviewer over already-merged PRs so its quality can be graded.

Two data points and one of them wrong is not evidence. This runs the real
pipeline against real merged diffs and writes a report with a blank verdict
column — you fill that in, then you know whether to trust it, and whether a
pricier model earns its cost.

    python review/backtest.py 100 101 102        # specific PRs
    python review/backtest.py --last 10          # last N merged PRs
    python review/backtest.py --at <sha> --base <sha>   # one exact diff

Needs ANTHROPIC_API_KEY in the environment and `pip install anthropic`.

Each PR is checked out into a throwaway worktree, so file contents are the ones
that existed at that commit rather than whatever is on your branch today.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def sh(*args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError("{}: {}".format(" ".join(args[:3]), r.stderr.strip()[:400]))
    return r.stdout


def merged_prs(limit):
    raw = sh("gh", "pr", "list", "--state", "merged", "--limit", str(limit),
             "--json", "number,title,mergeCommit,baseRefOid,headRefOid")
    return json.loads(raw)


def pr_meta(number):
    raw = sh("gh", "pr", "view", str(number),
             "--json", "number,title,mergeCommit,baseRefOid,headRefOid")
    return json.loads(raw)


def review_one(pr, model, workdir):
    """Run the real reviewer against this PR's head, in an isolated worktree."""
    base, head = pr["baseRefOid"], pr["headRefOid"]
    try:
        sh("git", "cat-file", "-e", head + "^{commit}")
    except RuntimeError:
        return {"error": "head commit {} is gone (branch deleted and gc'd)".format(head[:8])}

    tmp = tempfile.mkdtemp(prefix="backtest-")
    try:
        sh("git", "worktree", "add", "--detach", "-f", tmp, head)
        env = dict(os.environ, BASE=base, NO_POST="1", MODEL=model)
        env.pop("DRY_RUN", None)
        r = subprocess.run([sys.executable, os.path.join(HERE, "review.py")],
                           capture_output=True, text=True, cwd=tmp, env=env)
        if r.returncode != 0:
            return {"error": r.stderr.strip()[-400:]}
        usage = [l for l in r.stderr.splitlines() if "tokens in/out" in l]
        out = json.loads(r.stdout) if r.stdout.strip() else {"summary": "", "findings": []}
        out["usage"] = usage[0] if usage else ""
        return out
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tmp],
                       capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prs", nargs="*", type=int)
    ap.add_argument("--last", type=int, help="Instead of listing PRs, take the last N merged.")
    ap.add_argument("--at", help="Review this exact commit instead of a PR head. "
                                 "Use to re-test recall against a diff you know the "
                                 "answer to. Requires --base.")
    ap.add_argument("--base", help="Base commit for --at.")
    ap.add_argument("--model", default=os.environ.get("MODEL") or "claude-sonnet-5")
    ap.add_argument("--out", default="backtest.md")
    args = ap.parse_args()

    if args.at:
        if not args.base:
            ap.error("--at requires --base")
        prs = [{"number": 0, "title": "commit " + args.at[:8],
                "baseRefOid": args.base, "headRefOid": args.at}]
    elif args.last:
        prs = merged_prs(args.last)
    elif args.prs:
        prs = [pr_meta(n) for n in args.prs]
    else:
        ap.error("give PR numbers, --last N, or --at SHA --base SHA")

    rows, total = [], 0
    for pr in prs:
        print("PR #{} {}".format(pr["number"], pr["title"][:60]), file=sys.stderr)
        res = review_one(pr, args.model, os.getcwd())
        if "error" in res:
            print("  skipped: {}".format(res["error"]), file=sys.stderr)
            rows.append((pr, None, res["error"]))
            continue
        total += len(res["findings"])
        print("  {} finding(s)  {}".format(len(res["findings"]), res.get("usage", "")), file=sys.stderr)
        rows.append((pr, res, None))

    with open(args.out, "w") as fh:
        fh.write("# Backtest — {}\n\n".format(args.model))
        fh.write("{} PRs, {} findings. Grade each: **real** / **wrong** / **trivial**.\n"
                 "A finding you would not have wanted to see is a cost, not a neutral.\n\n".format(
                     len(rows), total))
        for pr, res, err in rows:
            fh.write("## PR #{} — {}\n\n".format(pr["number"], pr["title"]))
            if err:
                fh.write("_skipped: {}_\n\n".format(err))
                continue
            fh.write("_{}_\n\n{}\n\n".format(res.get("usage", ""), res["summary"]))
            if not res["findings"]:
                fh.write("No findings.\n\n")
            for f in res["findings"]:
                fh.write("| verdict | {}:{} | {} | {} |\n|---|---|---|---|\n\n".format(
                    f["path"], f["line"], f["severity"], f["category"]))
                fh.write("{}\n\n".format(f["body"]))
    print("\nwrote {} — {} findings across {} PRs".format(args.out, total, len(rows)), file=sys.stderr)


if __name__ == "__main__":
    main()
