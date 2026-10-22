# Consent request template

Use this message in an existing issue/PR thread or by email. Do not send it to
multiple maintainers in one thread; one repository per request.

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
