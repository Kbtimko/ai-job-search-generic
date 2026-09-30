# External cron trigger (cron-job.org)

The daily scans are triggered from **cron-job.org**, not from GitHub's `schedule:` cron.
This file is the version-controlled record of that setup, because the schedule itself
lives in a third-party web UI where it is otherwise invisible to this repo.

## Why

GitHub's scheduled-workflow dispatcher is best-effort and deprioritizes free-tier
private repositories. Measured trigger delay for `daily-scan.yml` (cron `0 11 * * *`):

| Window | Delay behind the 11:00 UTC cron |
| --- | --- |
| 2026-07-26 – 08-06 | 62–161 min |
| 2026-08-07 – 08-14 | 28–47 min |
| 2026-08-15 – 08-26 | 19–29 min (the best stretch on record) |
| 2026-08-27 – 08-31 | **250–619 min** |

There has never been an on-time period. By contrast, all 12 historical
`workflow_dispatch` runs started within **2–10 seconds** of being requested. The delay
is entirely in the `schedule:` dispatcher, so triggering `workflow_dispatch` externally
bypasses it.

Note this buys a *less congested* scheduler, not a guaranteed one — cron-job.org, like
Cloudflare and GitHub, is best-effort. It is not an SLA.

A second benefit: `0 11 * * *` is UTC, so the "7:00 AM America/New_York" comment in the
workflow is only true during EDT and silently becomes 6:00 AM EST each November.
cron-job.org schedules in a named timezone, so it does not drift.

## The two jobs

Both are `POST`, and both expect **HTTP 204 No Content** on success.

| Setting | Job scan | Pipeline scan |
| --- | --- | --- |
| URL | `https://api.github.com/repos/<your-github-user>/<your-repo>/actions/workflows/daily-scan.yml/dispatches` | `…/actions/workflows/pipeline-scan.yml/dispatches` |
| Schedule | 07:00, `America/New_York` | 07:20, `America/New_York` |
| Body | `{"ref":"main"}` | `{"ref":"main"}` |

Headers on both:

```
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
Authorization: Bearer <PAT>
```

**Enable failure notifications on both jobs.** This is not optional — see below.

## The token

A **fine-grained** PAT, scoped to `<your-github-user>/<your-repo>` only, with a single repository
permission: **Actions: Read and write**. That is the least privilege that can trigger a
`workflow_dispatch`.

The blast radius if it leaks is not data theft — it is *spend*. The token can trigger
scans, and each job scan bills roughly $0.70 of Anthropic credit plus Actions minutes.

## The failure mode that will actually bite

Fine-grained PATs expire within a year. On expiry the POST returns **401 and nothing
happens** — no run, no digest email, no red X anywhere you would normally look. Nothing
in this repo can detect it, because from GitHub's side the trigger simply never arrives.

Two mitigations, both required:

1. cron-job.org's failure notification (`onFailure`), so a non-2xx response emails you.
2. The `schedule:` cron in `daily-scan.yml` is deliberately **left enabled** as a
   backstop. If the external trigger dies, you still get a late scan rather than none.

## Why the guard job exists

Because the backstop stays on, two triggers can fire on the same day. The workflow sets
`concurrency.cancel-in-progress: false`, so the second one **queues rather than being
dropped** — without a guard that means two full scans, two digest emails, and twice the
API bill.

The `guard` job in `daily-scan.yml` admits the first *successful* run of each UTC day and
skips the rest. To run anyway (verification, a re-run after a bad scan), dispatch
manually with the `force` input set to true.

`pipeline-scan.yml` deliberately has **no** guard: it is deterministic, makes no API
calls, costs $0, and a second same-day run simply finds nothing new.
