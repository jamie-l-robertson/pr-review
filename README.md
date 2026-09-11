# pr-review

AI pull-request review for GitHub, with deterministic checks in front of it.

On every PR it runs secret scanning, lint, SAST and a PII scan — **routed by the
languages actually present in the diff** — then hands Claude the diff, the check output and the repo's own conventions — and
lets it read the rest of the checkout itself through read-only tools. Findings come
back as inline review comments.

## Use it

`.github/workflows/review.yml` in the consuming repo:

```yaml
name: review
on: pull_request

# Required. A reusable workflow cannot request more permission than its caller
# grants, and most repos default the token to read-only — without this block the
# run fails at startup, before any job begins.
permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    uses: jamie-l-robertson/pr-review/.github/workflows/review.yml@v1
    # Not `secrets: inherit` — that forwards every secret your repo holds, when
    # the reviewer needs exactly one.
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Pin to a commit

`v1` is a **moving tag** — it is repointed whenever this repo changes, so anything
tracking it can change without a commit in your repo. For anything you care about,
pin a commit SHA in **both** places:

```yaml
jobs:
  review:
    uses: jamie-l-robertson/pr-review/.github/workflows/review.yml@<sha>
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    with:
      tooling-ref: <sha>
```

Both, because `uses:` pins the workflow while `tooling-ref` pins the scripts that
workflow checks out. Pinning only the first leaves `review.py` and the check
scripts floating on the tag, which is most of what actually runs.

`ANTHROPIC_API_KEY` needs to exist as a secret on the repo or the account:

```bash
gh secret set ANTHROPIC_API_KEY --repo <owner>/<repo>
```

That's the whole setup — no scripts to copy, no config file.

### Inputs

| Input | Default | |
|---|---|---|
| `working-directory` | `.` | Where `package.json` / `tsconfig.json` live. |
| `model` | *(auto)* | Pin a model id to override the tiering below. |
| `routine-model` | `claude-opus-5` | Small, low-risk diffs. Set `claude-sonnet-5` to trade depth for cost. |
| `test-model` | `claude-haiku-4-5` | Used when the PR title contains `[test review]`. |
| `elevated-model` | `claude-opus-5` | Large or sensitive diffs. |
| `effort` | `medium` | `low`–`max`. Ignored on Haiku 4.5 / Sonnet 4.5, which reject it. |
| `reviewer-name` | `Inquisitor` | Name shown on the review and each inline comment. |
| `max-iterations` | `10` | Tool-use turns. Cost grows with the square of this. |
| `max-reviews-per-pr` | `5` | Stop after this many reviews on one PR. `0` disables. |
| `skillspector-ref` | `v2.11.2` | Pinned; SkillSpector is not on PyPI. |
| `react-doctor-version` | `latest` | Pin it if `latest` ever surprises you. |
| `pnpm-version` | `10` | Only used when linting. Better set `packageManager` in your `package.json`. |
| `lint-command` | autodetect | Override. Must write ESLint JSON to `$RUNNER_TEMP/eslint.json`. |
| `max-context-bytes` | `400000` | Ceiling on the bundle sent to the API. |
| `tooling-ref` | `v1` | Ref of this repo to run. |

A repo with the app in a subdirectory:

```yaml
    with:
      working-directory: app
```

## Which model runs

Picked from the diff, because most PRs do not need the expensive model and the
ones that do are recognisable up front:

| Condition | Model |
|---|---|
| More than 15 files, or more than 400 added lines | elevated |
| Touches auth, session, token, payment, admin, db, schema, migration, `/api/`, proxy, middleware, rate limiting, or `.github/` | elevated |
| Anything else | routine |
| PR title contains `[test review]` | **test** — overrides both |

A routine review says so in its footer, so a cheaper review is never a silent one,
and the run log names the model and tier. Set the `model` input to pin one and skip
the routing entirely.

**`[test review]` in the PR title** forces the cheapest model. Use it when you are
exercising the pipeline — a pin bump, a new pre-check, a workflow change — rather
than reviewing the code. Those runs are marked in the footer as a pipeline test so
their findings are never mistaken for a real review.

Both tiers default to Opus. The routing still exists, so setting `routine-model` to
`claude-sonnet-5` restores the cheaper path for small diffs in one line.

This is tuned on judgement, not measurement — the thresholds are a guess at where
risk starts. Watch whether routine reviews start missing things you care about, and
move the line if they do.

## Thoroughness

A single call is not exhaustive by nature, and the symptom is findings arriving on
the second or third push that were sitting in the first diff all along. Two things
push against that without paying for a second pass:

**Effort defaults to `medium`.** `xhigh` was tried and reverted: on a replay of a
known diff it produced 19,829 output tokens and corrupted the structured output —
raw JSON fragments leaking into a comment body, plus a junk `placeholder` finding.
More thinking is not automatically a better review, and the failure was silent
enough to ship. Raise it only with a `backtest.py` replay either side.

**Every changed file gets a verdict.** The output schema requires one entry per
changed file: `clean`, `defects-reported`, or `not-reviewed`. A file cannot be
silently skipped, because the ledger has to account for it, and `not-reviewed` is
explicitly allowed so the honest answer is available and never forced into a false
`clean`. Anything skipped, or missing from the ledger entirely, is listed on the
review as **not reviewed — treat as unknown**.

If findings still trickle in across pushes after that, the next lever is a genuine
second pass over the same diff, and it costs roughly double. Measure with
`backtest.py --at <sha> --base <sha>` against a diff whose answer you already know
before paying for it.

## Closing the CVE gap

The review scope claims `cybersecurity` covers "dependency and CVE exposure", but
semgrep matches patterns, not advisories — nothing fed that topic. `pnpm audit`
now does, gated on a lockfile actually moving in the diff. Auditing the whole tree
on every PR would report the same backlog forever and teach everyone to skip it.

## Second-order effects

A change can be correct in isolation and still break something. The prompt asks
what a change makes *untrue* elsewhere, not only whether the new lines are right:

- rows already written under the old behaviour — does this need a backfill?
- denormalised or cached copies: counters, aggregates, search indexes, ISR/CDN
- concurrency with what already runs: triggers, crons, queue consumers, migrations
  against live traffic
- other paths to the same outcome — a fix at one call site, not at the second
- contracts: callers, tests, types, response shapes, constraints, persisted enums
- deploy and rollback order

Consequences must be **checked, not speculated**: "this might affect callers"
without having opened them is worth nothing, and the tools exist to go and look.

The pasteable fix prompt carries the same question, so an agent fixing a finding
does not reintroduce the problem one layer out. It is told to surface a knock-on
rather than silently widen the diff — whether to fix it too is the author's call.

## What the model is not shown

`.github/`, `.claude/`, `.cursor/`, `.superpowers/`, `.codex/`, `.vscode/` and
`.idea/` are excluded from the review, along with docs and binary assets. They
change for reasons a code reviewer has no view on — a pin bump, a hook tweak — and
a one-line workflow edit was pulling a full review every time.

**The deterministic checks still see them.** semgrep `p/github-actions` scans
workflows, SkillSpector scans hooks and MCP config, and betterleaks scans
everything. Excluding them from the model is not excluding them from the pipeline.

A PR containing nothing else skips the model call entirely. A mixed PR sends only
the source files — this filters, it does not merely gate.

## Review scope

Eight topics, four of them conditional — a topic whose condition does not hold is
skipped rather than strained at. The `category` on every finding is one of these,
constrained by the output schema so it cannot drift.

| Category | Covers | Applies |
|---|---|---|
| `security` | OWASP Top 10 / CWE: injection, access control, authn/authz, SSRF, XSS, CSRF, unsafe crypto | always |
| `cybersecurity` | secrets, dependency CVEs, infra and IaC, CI/CD, supply chain, over-broad tokens | always |
| `performance` | complexity, N+1 and unindexed queries, concurrency, memory, caching, payload size | always |
| `accessibility` | WCAG 2.2 AA: semantics, keyboard, focus, contrast, target size, reduced motion | only if UI |
| `usability` | destructive affordances, missing loading/empty/error states, lost work, dead ends | only if user-facing |
| `code-quality` | correctness, design, error handling, resource cleanup, readability | always |
| `testing` | uncovered behaviour, weak assertions, implementation-coupled tests, flakiness, over-mocking | only if tests exist |
| `ai-safety` | prompt injection, unvalidated model output in a sink, excessive agency, unbounded spend | only if it calls an LLM |

Plus `prompt-injection`, reserved for text in the diff that tries to instruct the
reviewer (see **Untrusted input**).

Scope is not a quota. Most diffs touch two or three of these, and the prompt says
so explicitly — an invented finding costs more than a missed one.

## What blocks and what doesn't

**betterleaks is a hard gate.** If the diff contains a credential the job fails there
and nothing is sent to the Anthropic API — that ordering is the point of the design,
not just a cost saving.

Everything else is advisory. Lint, semgrep and the PII scan feed the model as
context and appear in the review; they never fail the PR. The review itself is
posted as `COMMENT`, never `REQUEST_CHANGES`.

## Language routing

Checks only run when the diff contains files they apply to, so a docs-only or
SCSS-only PR skips `pnpm install` entirely.

| In the diff | Runs |
|---|---|
| `.ts .tsx .js .jsx .mjs .cjs` | ESLint **on the changed files only** + semgrep `p/typescript` `p/react` `p/owasp-top-ten` |
| `.tsx .jsx` | react-doctor, on the changed components only |
| `.claude/` `.cursor/` `.mcp.json` `SKILL.md` `AGENTS.md` `CLAUDE.md` | SkillSpector |
| `.py` `.go` `.rb` `.php` `.java` `.sql` `.tf` | the matching semgrep ruleset |
| `.yml .yaml` under `.github/` | semgrep `p/github-actions` |
| anything else | agent review only |
| a lockfile moved | `pnpm audit` — dependency CVEs |
| always | betterleaks + PII |

Unknown extensions contribute nothing rather than failing.

## Conventions

`AGENTS.md`, `CLAUDE.md` and `CONTRIBUTING.md` from the repo root and
`working-directory` go into the system prompt verbatim, so the reviewer enforces
the rules the repo already documents. Nothing to configure, and no rules invented.

## Where the tools come from

Nothing is vendored into this repo, deliberately:

| Tool | Source | Pinned |
|---|---|---|
| betterleaks | binary from its GitHub release, **sha256-verified against the published checksums** | `BETTERLEAKS_VERSION` |
| react-doctor | `npx`, Modified MIT | `react-doctor-version` (default `latest`) |
| SkillSpector | `uv tool install` from git, Apache-2.0 | `skillspector-ref` (a tag, never a HEAD) |
| semgrep | pip, rules pulled from the public registry | `SEMGREP_VERSION` |
| ESLint | **the consuming repo's own `node_modules`** | that repo's lockfile |
| anthropic SDK | pip | `ANTHROPIC_SDK_VERSION` |

ESLint is the one that matters. It has to come from the consumer, because it needs
that repo's `eslint.config.*`, its plugins and its rule overrides — a copy vendored
here would lint every repo against the wrong config and report confident nonsense.
The rest are pinned by version so a run is reproducible; bump them here and every
consumer picks it up on the next `v1`.

## Severities

| | | |
|---|---|---|
| 🔴 | `blocker` | data loss, security, crash |
| 🟠 | `major` | wrong behaviour |
| 🟡 | `minor` | real but contained |
| 🟢 | `nit` | trivial |

The dot leads every inline comment, and the review body opens with a tally
(worst first, zeroes omitted) so the shape of a review is legible before you
read a word of it. A clean run says so outright rather than posting an empty
review — silence is ambiguous, and "nothing to report" is not.

## What a comment says, and what the prompt says

An inline comment states **only what is wrong** — the defect, what triggers it, and
what it costs, in at most three sentences. It never prescribes a fix, because a
one-line "use X instead" invites you to apply it without checking whether the
report is even right.

The fix lives in a collapsed **Prompt to fix this** block on the same comment,
written as instructions to a coding agent: which function and guard to reach for,
what the corrected behaviour must be, what else has to change with it, and what
would make the obvious fix wrong.

A separate standalone comment on the PR carries **one prompt covering every
finding**, so you paste once rather than opening twenty threads. It is edited in
place on each push rather than reposted.

## Resolving threads

On each push the reviewer closes its own stale threads, but only when **both**
signals agree:

1. The file has changed since the comment was written, or GitHub has marked the
   thread outdated.
2. This run no longer reports an equivalent finding.

GitHub's `outdated` flag alone is not enough: it only trips when the anchored hunk
disappears, so fixing a defect by editing *around* it — adding the assertions a
test was missing, say — leaves the thread looking current forever. Asking git
whether the file moved at all since the comment's commit catches the ordinary case.
A commit that no longer exists after a force-push counts as no evidence.

Either signal alone is not enough. A model omitting a finding is not evidence the
bug was fixed — it may simply not have mentioned it this time — and on its own that
rule would eventually hide something real. Code changing near a comment is not
evidence either. Requiring both means a thread you never touched stays open, and a
finding still being reported stays open even if you rewrote the line.

Matching is on an **id**, not on text or position. Every finding carries a
kebab-case slug naming the defect — embedded invisibly as `<!-- inq:path#slug -->`.

Two attempts got this wrong before it worked, both for the same reason:

1. **Hashing the comment text.** One defect produced five duplicate threads,
   because the model reworded it every run and the line kept moving.
2. **Asking the model for a stable id.** It coined three different slugs for one
   defect — `secrets-inherit-external-repo`, `secrets-inherit-thirdparty-workflow`,
   `secret-to-external-reusable-workflow`. Instructing a model to be consistent
   across independent calls is a wish, not a mechanism.

What works is closing the loop: the ids already open on the PR are fed back into
the prompt, so reusing one is a lookup rather than a feat of memory.

It only ever touches threads it opened itself — a human conversation is not its to
close.

**This needs a token the default one cannot provide.** Resolution is GraphQL-only
(`resolveReviewThread`; there is no REST equivalent), and `GITHUB_TOKEN` is refused
with `Resource not accessible by integration` no matter what permissions it is
granted. Run the job under a GitHub App token (`actions/create-github-app-token`)
or a PAT to enable it. Without one, everything else still works — the reviewer logs
the refusal once per run and leaves the threads open, and GitHub still collapses
them as **Outdated** on its own.

## Silencing it

Put `[skip review]` in the PR title. The job is skipped entirely — no checks, no
API call. Draft PRs are skipped for the same reason.

It also stops on its own after `max-reviews-per-pr` reviews — five by default — so
a PR you push to thirty times does not cost thirty reviews, and it posts at most 20 inline comments
per run — a review wanting to leave forty has misread the diff, not found forty bugs.

## Untrusted input

Everything in the bundle — diffs, file contents, commit messages, check output —
is fenced off in the system prompt as data, not instructions. Text in a diff that
tells the reviewer to approve the change or withhold findings is reported as a
`blocker` finding rather than obeyed.

The blast radius is small by construction: the model's only output is a fixed JSON
schema that becomes comments. It has no tools and cannot touch the repo. Fork PRs
get no review at all, so reaching this at all requires push access — the realistic
vector is a dependency-update branch or vendored third-party content, not a stranger.

## What a run costs

Measured on a real 24-file PR, elevated tier:

| | |
|---|---|
| Input (1x) | 1,009,539 → $5.05 |
| Cache reads (0.1x) | 2,546,048 → $1.27 |
| Cache writes (2x) | 80,179 → $0.80 |
| Output | 19,201 → $0.48 |
| **Total** | **~$7.60** |

That was at 40 turns. Every turn replays the whole conversation, so loop cost grows
roughly with the square of the turn count — the exploration that makes the reviewer
better is also what makes it expensive, and the two cannot be separated by tuning
the prompt.

One read returns up to 400 lines. That is deliberately generous against a 5-turn
budget: cost grows with the **square** of the turn count but only linearly with
result size, so a single large read is cheaper than a second turn. It also covers
almost any file in a repo that caps its own files at 400 lines.

`max-iterations` defaults to **10** for that reason — a handful of targeted reads,
not a tour of the repo. Raise it only if findings look thin, and read the cost line
in the log when you do. The cap is the only real control: a mid-loop bail does not
work, because the findings only exist in the final message.

10 leaves room to follow a change outwards — callers, the second call site,
whether a counter was backfilled — which is what the second-order rules ask for
and what 5 could not afford. It is still far below the 40 that cost $7.60.

Hitting the cap is safe. The reviewer is asked once more, with no tools, to report
from what it has already read and to mark anything it did not genuinely examine as
`not-reviewed` rather than `clean` — so a cheap run degrades into a shallower
review, never into no review. At a tight cap that wrap-up call is the normal path rather than
an exception.

The log reports `turns: N/M` alongside the token counts, so you can see whether a
review finished early or ran to the ceiling. The model does **not** minimise turns
on its own — at a cap of 40 it used all 40 — so the prompt gives it an explicit
stopping condition in both directions: stop when another read would not change the
findings, but do not stop while a changed file is unopened or a raised consequence
unchecked.

Watch for `hit the N-turn cap` in the log. Occasionally is fine. On every PR, with
lots of files marked `not-reviewed`, the cap is costing you findings.

Caching saved roughly $10 on that run. It is not optional at this scale.

## Lint is scoped to the diff

ESLint runs against the changed files, not the repo. Linting everything made the
step exit 1 on debt nobody on the PR wrote — so a red step meant nothing, and an
error the PR actually introduced was invisible among the existing ones. Scoped, a
red lint step means *this change* broke something.

It also shrinks the report the reviewer reads: 155KB across 443 files became 12KB
across 2 on a real PR.

Paths are relativised to `working-directory` before the tools see them, since git
reports from the repo root and those steps run in the app directory.

## Check output is compacted first

Tools pad their reports. ESLint emits an entry for every file it lints and inlines
each file's source, so a 443-file repo produced 155KB of which 436 entries were
`"messages": []` — and the 60KB read cap then truncated the real findings away.
Lint output was reaching the model as a wall of empty objects.

Reports are now parsed and stripped before the cap: entries with no findings are
dropped and inlined source removed, since the reviewer can open the file itself.
That report became 7.4KB with all 16 findings intact. Anything unparseable is
passed through rather than swallowed.

## Auditing the prompt

The system block is cached with a 1h TTL and read back at 0.1x, so **trimming it
saves almost nothing** — a run's tokens go on the conversation and the tool
results, not the instructions. Accuracy is what matters there, and a stale
instruction is expensive in a way length is not: the prompt claimed "you are given
the whole file and its neighbours" long after the tool loop replaced the bundle,
which invited the model to reason about files it had never opened.

If you change what the reviewer is given, change what the prompt says it is given,
in the same commit.

## Caching

Two breakpoints, with deliberately different lifetimes:

| Block | TTL | Why |
|---|---|---|
| System prompt + your `AGENTS.md` | **1h** | Byte-identical on every run and every PR in the repo, so an hour of reuse repays the 2x write. |
| Diff, changed files, check output | **5m** | Diff-specific: the next push changes it, so it can never be reused by a later run. It only has to survive this loop, whose turns are seconds apart. 1.25x to write instead of 2x. |

Tool definitions need no breakpoint of their own — everything before the system
breakpoint is already in the cached prefix.

Nothing else is worth caching. The rest of a request is derived from the diff, and
the diff is what changed.

### Where the tokens actually go

The larger prize is the ~43k tokens of file content, near-identical between two
runs on the same PR. It does **not** cache today, because caching is a prefix match
and the volatile diff is sent first. Reordering so stable content precedes the diff
would make most of that cacheable on a re-push within the TTL, cutting a second
run's input cost by roughly 80%. It also changes what the model reads first, which
can change review quality — so measure before assuming it is free.

Every run logs `cache write/read`. If reads stay at zero across consecutive pushes,
the breakpoint is costing you 25% on that block and should be removed.

## Testing it without spending anything

Most of what can break here is plumbing, and plumbing does not need a model:

```bash
python review/test_checks.py   # PII, routing, tiering, ids, coverage, sandbox
python review/test_wrapup.py   # the turn cap degrading into a wrap-up call
```

`test_wrapup.py` injects a fake client into `call_claude`, so the loop, the
history mirroring and the fallback are all exercised for nothing. Both run on
every PR here.

For an end-to-end run against a real diff, use the cheapest model rather than the
configured one — the wiring is what you are testing, not the review quality:

```bash
MODEL=claude-haiku-4-5 python review/backtest.py --at <sha> --base <sha>
```

That is a few pennies against several dollars on the elevated model.

## Measuring it

`review/backtest.py` replays the reviewer over already-merged PRs and writes a
report with a blank verdict column:

```bash
python review/backtest.py --last 10
python review/backtest.py --last 10 --model claude-opus-5 --out backtest-opus.md
```

Each PR is reviewed inside a throwaway git worktree at that PR's head commit, so
it sees the code as it was, not as it is now. Grade the findings real / wrong /
trivial before trusting the tool or paying for a bigger model. A finding you would
not have wanted to see is a cost, not a neutral.

## Optional: run it as your own bot

Two things the default `GITHUB_TOKEN` cannot do, both fixed by the same setup:
resolving review threads, and posting as anything other than `github-actions[bot]`.

Create a **private GitHub App** on your own account — Settings → Developer settings
→ GitHub Apps → New. It is private by default: owned by you, installable only on
your account, never listed anywhere.

- **Name it whatever you want the bot called** — comments post as `<AppName>[bot]`.
- **Permissions:** Repository → Pull requests → **Read and write**. Nothing else.
- **Where can this be installed:** Only on this account.
- Generate a private key, then install the App on the repos you want reviewed.

Then set two secrets on each repo (or once on the account):

```bash
gh secret set APP_ID --repo <owner>/<repo>          # the numeric App ID
gh secret set APP_PRIVATE_KEY --repo <owner>/<repo> # paste the whole .pem
```

and pass them through in the stub:

```yaml
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      APP_ID: ${{ secrets.APP_ID }}
      APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
```

All of it is optional. Leave the two out and everything still works — the reviewer
falls back to the default token, posts as `github-actions[bot]`, and leaves stale
threads for GitHub to collapse as outdated.

## Naming

`reviewer-name` brands the review body and every inline comment. The comment
**author** stays `github-actions[bot]` — that identity is fixed for the default
token and cannot be renamed. Changing it means running the job under a GitHub App
and minting a token with `actions/create-github-app-token`, at which point comments
post as `<YourApp>[bot]` with its own avatar. Not wired up here.

## Limits

- **Fork PRs get no review.** They have no access to secrets. `pull_request_target`
  would fix that by checking out untrusted code with your API key — deliberately not done.
- Comments are posted, never auto-resolved.
- The import resolver is TypeScript-shaped. Other languages degrade to
  "diff + full changed files", which is still well beyond hunk-only review.

## Hacking on it

```bash
python review/test_checks.py     # PII matching + check routing
DRY_RUN=1 python review/review.py  # print the assembled prompt, call nothing
```

`DRY_RUN` is the one to reach for — run it in a real repo against a real branch to
see exactly what the model would be sent, and what it costs, before spending anything.
