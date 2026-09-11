#!/usr/bin/env python3
"""Runnable check for the two bits of non-trivial logic: PII matching and routing.

No framework. `python3 review/test_checks.py` — silent pass, non-zero on failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detect import route
from pii import luhn, scan_line
from review import (SYSTEM, all_in_one, content_key, coverage_note, dot,
                    fenced, fix_block, marker, marker_of, tally)


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


def test_marker_is_stable_across_rewording_and_movement():
    # The failure this exists to prevent: five threads for one defect, because
    # the model reworded it each run and the line moved as the file was edited.
    a = {"id": "secrets-inherit-overbroad", "path": "wf.yml", "line": 11,
         "body": "Forwards every secret."}
    b = dict(a, line=47, body="Passes all secrets wholesale.")
    assert marker(a) == marker(b)
    # Different defect, same file.
    assert marker(a) != marker(dict(a, id="mutable-tag-pin"))
    # Same defect, different file.
    assert marker(a) != marker(dict(a, path="other.yml"))
    # Model noise in the slug must not create a new identity.
    assert marker(dict(a, id="  Secrets-Inherit-Overbroad  ")) == marker(a)
    assert marker(dict(a, id="")) == "wf.yml#unnamed"


def test_marker_round_trips_through_a_comment_body():
    f = {"id": "unbounded-loop", "path": "a.ts", "line": 3, "severity": "nit",
         "category": "perf", "body": "Loops forever on empty input."}
    body = "text" + fix_block(f) + "\n\n<!-- inq:{} -->".format(marker(f))
    assert marker_of(body) == "a.ts#unbounded-loop"
    assert marker_of("a comment with no marker") is None


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


def test_comment_body_carries_no_remedy_and_the_prompt_does():
    f = {"id": "x", "path": "a.ts", "line": 9, "severity": "major",
         "category": "security", "body": "Rejects on network failure.",
         "remedy": "Wrap the fetch and add an AbortSignal timeout."}
    block = fix_block(f)
    # The remedy belongs in the pasteable prompt, never in the visible comment.
    assert "AbortSignal" in block
    assert "What is wrong:" in block and "Suggested fix:" in block
    # A missing remedy must not blow up the template.
    assert "Not supplied" in fix_block({k: v for k, v in f.items() if k != "remedy"})


def test_one_shot_covers_every_finding():
    fs = [dict(id=str(i), path="f{}.ts".format(i), line=i, severity="minor",
               category="code-quality", body="b{}".format(i), remedy="r{}".format(i))
          for i in range(3)]
    out = all_in_one(fs)
    for i in range(3):
        assert "f{}.ts:{}".format(i, i) in out
        assert "r{}".format(i) in out
    assert "fix all 3 in one go" in out
    assert out.count("Steps:") == 1, "steps should appear once, not per finding"
    assert all_in_one([]) == ""


def test_coverage_note_flags_skipped_and_missing_files():
    paths = ["a.ts", "b.ts", "c.ts"]
    clean = {"files_reviewed": [{"path": p, "verdict": "clean"} for p in paths]}
    assert coverage_note(clean, paths) == ""
    # Explicitly skipped.
    skipped = {"files_reviewed": [{"path": "a.ts", "verdict": "not-reviewed"},
                                 {"path": "b.ts", "verdict": "clean"},
                                 {"path": "c.ts", "verdict": "clean"}]}
    assert "a.ts" in coverage_note(skipped, paths)
    # Silently omitted from the ledger entirely — the real failure mode.
    partial = {"files_reviewed": [{"path": "a.ts", "verdict": "clean"}]}
    note = coverage_note(partial, paths)
    assert "b.ts" in note and "c.ts" in note
    assert "2 file(s) not reviewed" in note


def test_categories_match_the_documented_topics():
    # The prompt names the topics and the schema constrains them; if they drift
    # apart the model returns a category the enum rejects, failing the whole call.
    try:
        from models import CATEGORIES as enum
    except ImportError:
        return  # pydantic absent locally; CI installs it
    assert len(enum) == len(set(enum))
    for slug in enum:
        assert "`{}`".format(slug) in SYSTEM, slug
    # The four conditional topics must keep their guard.
    for slug in ("accessibility", "usability", "testing", "ai-safety"):
        i = SYSTEM.index("`{}`".format(slug))
        assert "ONLY if" in SYSTEM[i:i + 200], slug


def test_fenced_widths():
    assert fenced("plain").startswith("```\n")
    assert fenced("a ``` b").startswith("````")
    assert fenced("a ````` b").startswith("``````")



def test_tools_refuse_to_leave_the_repo():
    # These arguments arrive from a model reading an untrusted diff.
    import tools
    assert "refused" in tools.read_file("../../../etc/passwd")
    assert "refused" in tools.read_file("/etc/passwd")
    assert "refused" in tools.search("x", "../etc/*")
    assert "refused" in tools.list_files("../..")
    assert "refused" in tools.history("/etc")



def test_inert_diffs_skip_the_model_call():
    from review import worth_reviewing
    # A docs-and-assets-only PR is not worth paying a model to read.
    assert worth_reviewing(["README.md", "public/logo.svg", "docs/a.txt"]) == []
    # One real file makes the whole diff worth reviewing.
    assert worth_reviewing(["README.md", "lib/a.ts"]) == ["lib/a.ts"]
    # Workflow YAML is NOT inert — it is exactly where CI secrets leak.
    assert worth_reviewing([".github/workflows/ci.yml"]) == [".github/workflows/ci.yml"]
    # Lockfiles never reach here; common.skipped() drops them from changed_files.
    from common import skipped
    assert skipped("pnpm-lock.yaml") and skipped("yarn.lock")



def test_model_tiering():
    from detect import tier
    # Small, low-risk UI work does not need the expensive model.
    assert tier(["components/Button.tsx"], 20) == "routine"
    assert tier(["app/feed/feed.module.scss"], 40) == "routine"
    # Size escalates.
    assert tier(["a{}.ts".format(i) for i in range(20)], 50) == "elevated"
    assert tier(["a.ts"], 900) == "elevated"
    # So does anything where a missed defect is expensive.
    for p in ("lib/sessionAccess.ts", "app/api/me/route.ts", "db/schema/x.ts",
              "app/admin/page.tsx", ".github/workflows/ci.yml", "lib/ratelimit.ts"):
        assert tier([p], 5) == "elevated", p



def test_agent_config_paths_are_recognised():
    from detect import AGENT_PATHS
    def hit(p):
        return any(a in p.lower() for a in AGENT_PATHS)
    # Hooks run, MCP servers get launched, instruction files steer agents.
    for p in (".mcp.json", ".claude/settings.json", ".cursor/hooks.json",
              ".cursor/mcp.json", "AGENTS.md", "CLAUDE.md", "docs/SKILL.md"):
        assert hit(p), p
    for p in ("lib/a.ts", "app/page.tsx", "README.md", "db/schema/x.ts"):
        assert not hit(p), p



def test_compact_keeps_findings_and_drops_padding():
    import json
    from review import compact
    # ESLint emits an entry per linted file and inlines each file's source, so a
    # real report was 96% padding and the read cap truncated the findings away.
    report = json.dumps(
        [{"filePath": "clean{}.ts".format(i), "messages": [], "source": "x" * 500}
         for i in range(50)]
        + [{"filePath": "bad.ts", "source": "y" * 5000,
            "messages": [{"ruleId": "no-x", "severity": 2, "line": 3}]}])
    out = compact("eslint.json", report)
    kept = json.loads(out)
    assert len(kept) == 1 and kept[0]["filePath"] == "bad.ts"
    assert kept[0]["messages"][0]["ruleId"] == "no-x"
    assert "source" not in kept[0], "the file body must not be shipped"
    assert len(out) < len(report) / 10
    # A report with nothing in it says nothing.
    assert compact("eslint.json", "[]") == ""
    # Anything unparseable is passed through rather than swallowed.
    assert compact("eslint.json", "not json at all") == "not json at all"



def test_touched_since_needs_real_evidence():
    from review import touched_since
    # No commit recorded, or a commit that no longer exists after a force-push,
    # is not evidence that anything was fixed.
    assert touched_since("review/review.py", "") is False
    assert touched_since("review/review.py", "deadbeefdeadbeef") is False
    assert touched_since("", "HEAD") is False
    # A file identical to HEAD has not been touched since HEAD.
    assert touched_since("review/review.py", "HEAD") is False


def test_paths_are_made_relative_to_the_working_directory():
    from detect import rel_to_workdir
    # Repo root is the app root: nothing to strip.
    assert rel_to_workdir("lib/a.ts", ".") == "lib/a.ts"
    assert rel_to_workdir("lib/a.ts", "") == "lib/a.ts"
    # App in a subdirectory: the check steps run there, git reports from above.
    assert rel_to_workdir("app/lib/a.ts", "app") == "lib/a.ts"
    assert rel_to_workdir("app/lib/a.ts", "app/") == "lib/a.ts"
    # A path outside the working directory is left alone rather than mangled.
    assert rel_to_workdir("docs/a.md", "app") == "docs/a.md"
    # A directory that merely shares a prefix must not be stripped.
    assert rel_to_workdir("application/a.ts", "app") == "application/a.ts"



def test_second_order_effects_are_in_both_prompts():
    from review import SYSTEM, fix_block
    # A change can be right in isolation and still break something. Both the
    # review prompt and the pasteable fix prompt have to ask that question —
    # the reviewer to find it, the fixing agent not to reintroduce it.
    for phrase in ("SECOND-ORDER EFFECTS", "already written under the old behaviour",
                   "Denormalised or cached", "Other paths to the same outcome",
                   "Deploy and rollback order"):
        assert phrase in SYSTEM, phrase
    # Claims must be checked, not speculated.
    assert "Report only consequences you have actually checked" in SYSTEM
    block = fix_block({"id": "x", "path": "a.ts", "line": 1, "severity": "minor",
                       "category": "code-quality", "body": "b", "remedy": "r"})
    assert "makes untrue elsewhere" in block
    # And widening the diff is the author's call, not the agent's.
    assert "let the author decide" in block



def test_turn_cap_is_counted_in_turns_not_tool_calls():
    # A single turn can carry several parallel tool calls, so comparing calls
    # against a turn cap reported a cap that had not been hit — and that line is
    # the signal for whether the cap is too tight.
    import inspect
    from review import call_claude
    src = inspect.getsource(call_claude)
    assert "turns += 1" in src
    assert "if turns >= MAX_ITERATIONS" in src
    assert "if calls >= MAX_ITERATIONS" not in src


def test_prompt_gives_a_stopping_condition_in_both_directions():
    from review import SYSTEM
    # Frugality alone would make it stop early; thoroughness alone would make it
    # read everything. It needs both halves.
    assert "Stop reading when another read would not change your findings" in SYSTEM
    assert "do not stop early" in SYSTEM



def test_changed_files_are_preloaded_not_left_to_tool_calls():
    """Runs the real builder against this repo's own last commit. No API calls."""
    import subprocess
    import review
    head1 = subprocess.run(("git", "rev-parse", "-q", "--verify", "HEAD~1"),
                           capture_output=True, text=True)
    if head1.returncode != 0:
        return  # shallow clone; nothing to diff against
    prompt, paths = review.build_prompt(head1.stdout.strip())
    if not prompt:
        return  # a docs-only commit is correctly skipped
    # Every changed file's contents are in the prompt, so none costs a tool call.
    for p in paths:
        assert "## {}".format(p) in prompt, p
    assert "do not spend a tool call re-reading" in prompt
    # And the reads it cannot predict are still pushed to the tools, batched.
    assert "callers and callees" in prompt
    assert "ONE turn" in prompt



def test_build_artefacts_are_never_reviewed():
    from common import skipped
    # git add -A once committed compiled bytecode here, and the reviewer then
    # treated .pyc files as changed files to open.
    for p in ("review/__pycache__/review.cpython-312.pyc", ".venv/lib/x.py",
              "vendor/dep.go", "node_modules/a/index.js", "dist/bundle.js"):
        assert skipped(p), p
    for p in ("review/review.py", "lib/a.ts", "app/page.tsx"):
        assert not skipped(p), p



def test_cache_ttls_match_what_each_block_can_reuse():
    import inspect
    from review import call_claude
    src = inspect.getsource(call_claude)
    # The system block is identical across runs and across PRs, so an hour of
    # reuse is worth the 2x write. The diff block cannot outlive its own run —
    # the next push changes it — so paying the 1h premium buys nothing.
    sys_idx = src.index('"text": SYSTEM')
    msg_idx = src.index('"text": prompt')
    assert '"ttl": "1h"' in src[sys_idx:msg_idx], "system block should hold 1h"
    assert '"ttl": "5m"' in src[msg_idx:], "diff block should be 5m"
    assert '"ttl": "1h"' not in src[msg_idx:], "diff block must not pay the 1h premium"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
