# pr-review

AI pull-request review for GitHub, with deterministic checks in front of it.

On every PR it runs secret scanning, lint, SAST and a PII scan — **routed by the
languages actually present in the diff** — then hands Claude the diff, the full
contents of every changed file, one hop of imports in each direction, the check
output and the repo's own conventions. Findings come back as inline review comments.

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
    secrets: inherit
```

### Pin to a commit

`v1` is a **moving tag** — it is repointed whenever this repo changes, so anything
tracking it can change without a commit in your repo. For anything you care about,
pin a commit SHA in **both** places:

```yaml
jobs:
  review:
    uses: jamie-l-robertson/pr-review/.github/workflows/review.yml@<sha>
    secrets: inherit
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
| `model` | `claude-sonnet-5` | Any Anthropic model id. |
| `reviewer-name` | `Inquisitor` | Name shown on the review and each inline comment. |
| `max-reviews-per-pr` | `10` | Stop after this many reviews on one PR. `0` disables. |
| `pnpm-version` | `10` | Only used when linting. Better set `packageManager` in your `package.json`. |
| `lint-command` | autodetect | Override. Must write ESLint JSON to `$RUNNER_TEMP/eslint.json`. |
| `max-context-bytes` | `400000` | Ceiling on the bundle sent to the API. |
| `tooling-ref` | `v1` | Ref of this repo to run. |

A repo with the app in a subdirectory:

```yaml
    with:
      working-directory: app
```

## What blocks and what doesn't

**gitleaks is a hard gate.** If the diff contains a credential the job fails there
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
| `.py` `.go` `.rb` `.php` `.java` `.sql` `.tf` | the matching semgrep ruleset |
| `.yml .yaml` under `.github/` | semgrep `p/github-actions` |
| anything else | agent review only |
| always | gitleaks + PII |

Unknown extensions contribute nothing rather than failing.

## Conventions

`AGENTS.md`, `CLAUDE.md` and `CONTRIBUTING.md` from the repo root and
`working-directory` go into the system prompt verbatim, so the reviewer enforces
the rules the repo already documents. Nothing to configure, and no rules invented.

## Where the tools come from

Nothing is vendored into this repo, deliberately:

| Tool | Source | Pinned |
|---|---|---|
| gitleaks | binary from its GitHub release | `GITLEAKS_VERSION` in the workflow |
| semgrep | pip, rules pulled from the public registry | `SEMGREP_VERSION` |
| ESLint | **the consuming repo's own `node_modules`** | that repo's lockfile |
| anthropic SDK | pip | `ANTHROPIC_SDK_VERSION` |

ESLint is the one that matters. It has to come from the consumer, because it needs
that repo's `eslint.config.*`, its plugins and its rule overrides — a copy vendored
here would lint every repo against the wrong config and report confident nonsense.
The rest are pinned by version so a run is reproducible; bump them here and every
consumer picks it up on the next `v1`.

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
