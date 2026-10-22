# Consent policy for audit trajectories

SmartBench audits third-party repositories. Publishing an audit trajectory is a
separate decision from the audit itself, and it needs the repository owner's
explicit consent.

## Rule

- **No third-party trajectory is published without explicit written consent.**
- A public issue, PR or commit is **not** consent. It only shows that the code is
  public.
- The `real_world` split is empty until a case is marked `granted` in
  [`v0/consent.json`](v0/consent.json) with a link to the consenting comment.
- Cases in `pending` are excluded from the published dataset and from the
  public sources file.
- Cases in `declined` are excluded and **local copies are deleted** (see below).

## What is published

For a consented case, the trajectory contains only:

- rule/analysis identifiers, project-relative paths and line numbers;
- the finding and its verification summary;
- the public issue/PR URL and outcome;
- no raw prompts, raw model responses, secrets or absolute paths.

## Every scan is staged

1. `smartbench quick` / `smartbench unified` saves the redacted trajectory
   locally.
2. If the project has a GitHub `origin` and the trajectory has findings, the
   repository is added to the local consent queue
   (`~/.smartbench/consent/outreach.json`) as `staged`. Nothing is published.
3. `smartbench consent send` resolves a **public** maintainer contact and sends
   at most one request per repository (dry run by default; `--send` to deliver).
4. A reply is recorded in `trajectories/v0/consent.json`; only `granted` with
   evidence enters the dataset.

`SMARTBENCH_CONSENT_AUTOSEND=1` (with SMTP configured) lets a scan send its own
staged request. The GitHub `Consent outreach` workflow can also run on a
schedule; it defaults to a dry run unless the `CONSENT_AUTOSEND` repository
variable is `true` or a manual run sets `send=true`.

## Email outreach

- Contacts come only from the public GitHub profile or an email written in
  `SECURITY.md` / `CONTRIBUTING.md`. Commit-author emails are not harvested.
- One email per repository; no findings, code or vulnerability details are
  included, and security policies take precedence.
- At most 5 messages per run; no automatic follow-ups.
- A case with no public email is marked `contact_missing`; it is not sent.
  You can supply a local, uncommitted `SMARTBENCH_CONSENT_CONTACTS` JSON mapping
  (`{"owner/repo": "maintainer@example.org"}`) for a manual contact.
- SMTP is configured with `SMARTBENCH_SMTP_HOST`, `SMARTBENCH_SMTP_PORT`,
  `SMARTBENCH_SMTP_USER`, `SMARTBENCH_SMTP_PASSWORD` and
  `SMARTBENCH_SMTP_FROM`; see `send-consent.sh` in the working directory.

## Contributor leaderboard

Maintainers who grant consent can be listed in
[`CONTRIBUTORS.md`](CONTRIBUTORS.md), generated from `consent.json` by
`tools/build_contributors.py`. Listing is opt-in (display name or no listing).
Pending requests are never listed.

## How consent is recorded

1. Send the request in [`docs/CONSENT_REQUEST.md`](../docs/CONSENT_REQUEST.md)
   (existing issue/PR thread or email).
2. The maintainer replies with the consent statement, or declines.
3. Record the outcome in `trajectories/v0/consent.json`:

```json
{
  "id": "example-case",
  "repository": "owner/repo",
  "status": "granted",
  "requested_at": "2026-10-22",
  "granted_at": "2026-10-23",
  "evidence": "https://github.com/owner/repo/issues/1#issuecomment-...",
  "note": ""
}
```

`status` is one of `pending`, `granted`, `declined`.

## Declining, removal and local data

- **Declined:** do not publish; delete local trajectories for that repository:

  ```bash
  python tools/check_dataset_consent.py --purge-declined \
    --trajectory-dir ~/.smartbench/trajectories --yes
  ```

- **Already published and later withdrawn:** remove the entry from
  `consent.json`, regenerate the dataset, and remove the local trajectory. If
  the data was already released, we remove it from the current dataset and honor
  history-rewrite requests.
- **No response:** stays `pending`; it is not published.

## Restoring a consented case

1. Add **only that case** to `v0/sources/real_world.yaml` (never pending cases).
2. Set its ledger entry to `granted` with `evidence`, `granted_at` and
   `trajectory_ids`; optionally `display_name`, `listed` and `contribution`.
3. Run `python tools/build_trajectories_v0.py --root . --out trajectories/v0`
   and `python tools/build_contributors.py`, then open a PR. The consent gate
   and leaderboard check must pass.

## Enforcement

- `tools/check_dataset_consent.py` fails if any published `real_world` document
  lacks a `granted` consent with evidence.
- `.github/workflows/consent.yml` runs the check and verifies the generated
  contributor leaderboard on every dataset change.
- Takedown/consent requests can also be filed with the repository's
  [data consent issue template](../.github/ISSUE_TEMPLATE/data-consent.md).

## Scope

This policy covers **real-world audit trajectories**. The `benchmark` and
`negative` splits are a controlled before/after evaluation corpus and synthetic
clean fixtures; upstream licenses and attribution still apply to those excerpts.
