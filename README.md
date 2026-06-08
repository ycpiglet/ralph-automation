# ralph-automation

Reusable automation core for repository agent workflows.

Current scope is GitHub source preparation:

- importable `ralph_automation` package
- `ralph inventory --check`
- `ralph export --check|--diff|--apply`
- `ralph sync --check|--diff|--apply`
- `ralph sanitize --root . --check`
- `ralph publish-check --root . --check`
- `ralph publish-bundle --source . --dest <dir> --check|--apply`
- `ralph publish-tag-smoke --source . --repo-dir <dir> --install-dir <dir> --check|--apply`
- `ralph publish-github-plan --source . --remote-url <github-url> --install-dir <dir> --check`
- `ralph publish-github-status --remote-url <github-url> --check`
- `ralph publish-github-execute --source <clean-source> --remote-url <github-url> --install-dir <dir> [--work-dir <dir>] [--execute]`
- `ralph update-plan --root <host> --install-dir <dir> --check`
- `ralph update --root <host> --install-dir <dir> --check|--diff|--apply`
- `ralph release-preflight --source . --host-root <host> --remote-url <github-url> --check`
- package-data templates under `src/ralph_automation/templates/project/`
- package-local `tests/`
- GitHub Actions workflow under `.github/workflows/test.yml`
- no product files, host state, or local runtime state exported by default

The public GitHub repository is not created from this directory until the
sanitization gate is in place.

## Export Behavior

`ralph export` stages reusable host automation into installable package
templates.

- `--check` reports missing template files, conflicts, and unsafe content.
- `--diff` renders unified diffs for staged templates.
- `--apply` copies missing safe candidates only.
- It never exports `public/`, `supabase/`, `.env`, task/report history,
  runtime messages, or local tool settings.
- Existing divergent templates are conflicts and are not overwritten.

## Sync Behavior

`ralph sync` reads safe templates from package data under
`ralph_automation/templates/project`.

- Missing host files are reported as `create` updates.
- Existing managed host files are reported as `update` only when their current
  content still matches the previous `ralph.lock.json` per-file hash.
- `--diff` renders unified diffs.
- `--apply` creates missing safe templates and updates unchanged managed files.
- Existing divergent host files are conflicts and are not overwritten.

## Publish Check

Run this before creating a public GitHub repo or tag:

```powershell
PYTHONPATH=src python -m ralph_automation.cli sanitize --root . --check
PYTHONPATH=src python -m ralph_automation.cli publish-check --root . --check
PYTHONPATH=src python -m ralph_automation.cli publish-bundle --source . --dest .tmp/public-source --check
PYTHONPATH=src python -m ralph_automation.cli publish-tag-smoke --source . --repo-dir .tmp/tag-repo --install-dir .tmp/tag-install --check
PYTHONPATH=src python -m ralph_automation.cli publish-github-plan --source . --remote-url https://github.com/example/ralph-automation.git --install-dir .tmp/github-install --check
PYTHONPATH=src python -m ralph_automation.cli publish-github-status --remote-url https://github.com/example/ralph-automation.git --check
PYTHONPATH=src python -m ralph_automation.cli publish-github-status --remote-url https://github.com/example/ralph-automation.git --branch main --require-workflow --wait-workflow --check
PYTHONPATH=src python -m ralph_automation.cli publish-github-status --remote-url https://github.com/example/ralph-automation.git --branch main --require-workflow --wait-workflow --workflow-head-sha <commit-sha> --check
PYTHONPATH=src python -m ralph_automation.cli publish-github-execute --source .tmp/public-source --remote-url https://github.com/example/ralph-automation.git --install-dir .tmp/public-source/.tmp/github-install
PYTHONPATH=src python -m ralph_automation.cli release-preflight --source . --host-root tests/fixtures/host --remote-url https://github.com/example/ralph-automation.git --check
PYTHONPATH=src python -m pytest tests -q
```

`publish-check` verifies the package has CI, package-data templates, sanitizer
CI coverage, and no unignored legacy top-level template tree.

`sanitize` scans publishable source content for forbidden paths, local absolute
paths, and secret-like content. Generated local work directories such as
`.tmp/`, `build/`, `dist/`, `.pytest_cache/`, and `*.egg-info/` are ignored so a
local smoke run does not make the source package fail its own preflight.

`publish-bundle` selects only the files that should become the public GitHub
source tree: `.github/`, `src/`, `tests/`, `.gitignore`, `pyproject.toml`, and
`README.md`. It refuses to overwrite a non-empty destination.

`publish-tag-smoke` creates a clean local git repo, tags it, installs from
`git+file://...@tag`, and verifies installed sync templates. Use `--apply` for
the full local rehearsal.

`publish-github-plan` is non-mutating. It prints the exact external commands for
the Owner-approved boundary: first build the public bundle worktree, initialize
and commit/tag the local release, then verify or create the public GitHub
repository with `gh repo view` / `gh repo create --public`, push `main`, push
the release tag, install from `git+https://...@tag`, verify installed sync
templates, and run the workflow-required publish status check. The final manual
status command resolves the release SHA with a Python subprocess call instead
of shell command substitution, reducing PowerShell/Bash quoting drift for paths
with spaces. It blocks unignored files outside the `publish-bundle` public
source contract so `git add .` cannot accidentally publish host-only leftovers.
Placeholder owners such as `OWNER` are publish findings; replace `example` with
the real GitHub owner before treating the plan as release evidence.

`publish-github-status` is read-only. It checks local `gh` authentication and
repository availability so the external publish boundary can fail early before
repo creation, push, or tag commands are attempted. Existing repositories must
be public; a private repo is a publish finding, not a successful target. Add
`--require-workflow` after a push to require the configured workflow
(`--workflow-name`, default `test`) to be completed with a successful
conclusion. Add `--wait-workflow` for the real post-push gate; it polls until
the latest run succeeds or the timeout is reached. Add `--workflow-head-sha` to
ensure the run belongs to the release commit, not an older successful run on
the same branch.

`publish-github-execute` renders the exact Owner-approved execution sequence by
default. It only runs public GitHub mutation commands when `--execute` is
provided. The first executed step is `gh auth status`; if auth fails, repo
creation, git commit, push, tag, and install verification are not attempted.
Use a clean bundle from `publish-bundle` as `--source` for the real external
publish. When execution proceeds, the clean source is copied into a throwaway
git worktree under `<source>/.tmp/github-worktree` by default, and all
`git init/add/commit/tag` steps run there before repo create/push, instead of
mutating the original clean source. Use `--work-dir` only for an empty directory under the source
`.tmp/` tree. The repo ensure step is fail-closed: it accepts an existing public
repo, creates a public repo only when `gh repo view` reports not found, and
blocks private repos or other lookup failures. After pushing and verifying the GitHub tag install, execution runs
`publish-github-status --require-workflow --wait-workflow --check` so a failed,
missing, timed-out, or never-green GitHub Actions run keeps the publish
incomplete. During real execution it resolves the release worktree `HEAD` and
passes it as `--workflow-head-sha`, so a previous successful workflow run cannot
stand in for the commit just published.

## Host Update Plan

Host projects pin the upstream dependency in `ralph.yml`:

```yaml
upstream:
  package: ralph-automation
  remote_url: https://github.com/example/ralph-automation.git
  ref: v0.1.0
```

Then run:

```powershell
PYTHONPATH=src python -m ralph_automation.cli update-plan --root <host> --install-dir .tmp/ralph-upstream --check
PYTHONPATH=src python -m ralph_automation.cli update --root <host> --install-dir <host>/.tmp/ralph-upstream --check
PYTHONPATH=src python -m ralph_automation.cli update --root <host> --install-dir <host>/.tmp/ralph-upstream --diff
PYTHONPATH=src python -m ralph_automation.cli update --root <host> --install-dir <host>/.tmp/ralph-upstream --apply
PYTHONPATH=src python -m ralph_automation.cli lock --root <host> --write
```

`update-plan` is non-mutating. It prints the install command for the pinned
GitHub ref and the follow-up `sync --check`, `sync --diff`, `sync --apply`
commands, followed by `lock --write` to record the installed version and
template digest. The digest ignores install-time `__pycache__` files and
normalizes text line endings, so source and installed package checks compare the
same template content across platforms. `update-plan --check` uses the same
trust checks as executable update: `upstream.package` must be
`ralph-automation`, the remote must be GitHub, the ref must be a SemVer-like
release tag or 40-character SHA, and the install dir must be empty under
`.tmp/` or `.ralph/`.

`update` executes the same flow instead of only printing it. It installs the
pinned upstream into a staging directory, verifies the installed package has
sync templates, and then runs installed-target sync commands. `--check` and
`--diff` write only to the staging install directory; `--apply` runs
`sync --apply`, verifies with a post-apply `sync --check`, and then writes the
host `ralph.lock.json`. Executable update rejects unsafe install targets: use an
empty directory under the host `.tmp/` or `.ralph/` tree.

The lock file records both an aggregate template digest and per-file
`managed_files` hashes. That lets future sync runs automatically update files
that still match the previous upstream while blocking files that were edited in
the host project.

`release-preflight` is the single non-mutating pre-publication plan check. It
aggregates sanitize, publish-check, bundle plan, local tag smoke plan, GitHub
publish plan, host update plan, host upstream match, executable host update
command shape, host sync conflict detection, and host lock freshness into one
report. It does not replace `publish-tag-smoke --apply` or the real
`publish-github-execute --execute` / workflow status evidence. Release tags such
as `v0.1.0` are accepted for normal distribution; a 40-character commit SHA is
stricter if the host must be protected from force-moved tags.
