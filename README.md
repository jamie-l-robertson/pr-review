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
jobs:
  review:
    uses: jamie-l-robertson/pr-review/.github/workflows/review.yml@v1
    secrets: inherit
```

`ANTHROPIC_API_KEY` needs to exist as a secret on the repo or the account.
That's the whole setup — no scripts to copy, no config file.

### Inputs

| Input | Default | |
|---|---|---|
| `working-directory` | `.` | Where `package.json` / `tsconfig.json` live. |
| `model` | `claude-opus-5` | Any Anthropic model id. |
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
