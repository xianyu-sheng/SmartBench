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

## Enforcement

- `tools/check_dataset_consent.py` fails if any published `real_world` document
  lacks a `granted` consent with evidence.
- `.github/workflows/consent.yml` runs the check on every dataset change.
- Takedown/consent requests can also be filed with the repository's
  [data consent issue template](../.github/ISSUE_TEMPLATE/data-consent.md).

## Scope

This policy covers **real-world audit trajectories**. The `benchmark` and
`negative` splits are a controlled before/after evaluation corpus and synthetic
clean fixtures; upstream licenses and attribution still apply to those excerpts.
