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
| `routine-model` | `claude-sonnet-5` | Small, low-risk diffs. |
| `elevated-model` | `claude-opus-5` | Large or sensitive diffs. |
| `effort` | `medium` | `low`–`max`. Ignored on Haiku 4.5 / Sonnet 4.5, which reject it. |
| `reviewer-name` | `Inquisitor` | Name shown on the review and each inline comment. |
| `max-reviews-per-pr` | `10` | Stop after this many reviews on one PR. `0` disables. |
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

A routine review says so in its footer, so a cheaper review is never a silent one,
and the run log names the model and tier. Set the `model` input to pin one and skip
the routing entirely.

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
| `.ts .tsx .js .jsx .mjs .cjs` | ESLint + semgrep `p/typescript` `p/react` `p/owasp-top-ten` |
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

1. GitHub has marked the thread **outdated** — the anchored code actually changed.
2. This run no longer reports an equivalent finding.

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

It also stops on its own after `max-reviews-per-pr` reviews, so a PR you push to
thirty times does not cost thirty reviews, and it posts at most 20 inline comments
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

## Caching

The system block (prompt + your `AGENTS.md`) carries a `cache_control` breakpoint.
It is byte-identical on every run in a repo, so it is the only part of the request
that reliably repays the 1.25x write cost. That is a few percent of a ~50k-token
request — real, but small.

The larger prize is the ~43k tokens of file content, near-identical between two
runs on the same PR. It does **not** cache today, because caching is a prefix match
and the volatile diff is sent first. Reordering so stable content precedes the diff
would make most of that cacheable on a re-push within the TTL, cutting a second
run's input cost by roughly 80%. It also changes what the model reads first, which
can change review quality — so measure before assuming it is free.

Every run logs `cache write/read`. If reads stay at zero across consecutive pushes,
the breakpoint is costing you 25% on that block and should be removed.

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
