# Consent request template

Use this message in an existing issue/PR thread (preferred) or by email as a
fallback. Do not send it to multiple maintainers in one thread; one repository
per request. A reply must contain the explicit consent statement; a thumbs-up or
"thanks" is not consent.

## GitHub thread version

```text
I'm preparing a public dataset of redacted audit trajectories from SmartBench
and would like to include this case.

The record would contain only analysis/rule IDs, project-relative file paths and
line numbers, the finding summary, and this public thread URL. It would not
include raw prompts/responses, secrets, or absolute paths. Nothing is published
without your explicit consent.

If you're comfortable with that, please reply with:

"I, <name>, consent to SmartBench publishing a redacted audit trajectory for
<owner/repo> under CC BY 4.0."

If you'd prefer not to be included, just say so — we won't publish it and will
delete our local copy. No response also means it stays unpublished. We can list
the repository on the data-contributor leaderboard only if you consent; tell us
if you prefer a display name or no listing.

Policy: https://github.com/xianyu-sheng/SmartBench/blob/main/trajectories/CONSENT.md
```

## Email version

---

**Subject:** Permission to publish a redacted SmartBench audit trajectory for `<owner/repo>`

Hi `<maintainer>`,

I maintain [SmartBench](https://github.com/xianyu-sheng/SmartBench), an
open-source code-diagnosis tool. We audited `<owner/repo>` and would like to
include a **redacted audit trajectory** in our public dataset
(`trajectories/v0`, data licensed CC BY 4.0).

The record would contain only:

- rule/analysis identifiers, project-relative file paths and line numbers;
- the finding(s) and the public verification summary;
- the public issue/PR URL and outcome.

It would **not** contain raw prompts, raw model responses, secrets or absolute
paths. Nothing is published unless you explicitly agree. With your permission we
will also list the repository on our data-contributor leaderboard (opt-in; you
can choose a display name or ask not to be listed).

If you agree, please reply with:

> I, `<name>`, consent to SmartBench publishing a redacted audit trajectory for
> `<owner/repo>` under CC BY 4.0.

If you prefer not to, just say so: we will not publish it and will delete our
local copy. You can also ask for removal at any time.

Policy: https://github.com/xianyu-sheng/SmartBench/blob/main/trajectories/CONSENT.md
