# Log Reading Assignment — Answers

Analysis reproduced by `analyze.py web.log worker.log` (see that file for the exact
correlation logic; log files themselves are not included per the assignment notes).

## 1. When did the problem start?

**~14:31–14:33 on 2026-07-02.**

- `web.log` shows a deploy marker right before the incident:
  ```
  2026-07-02 14:31:13.000 INFO [deploy] release v2.14.3 deployed to production (commit 219c39f)
  ```
- The first background-job failure appears 89 seconds later, in `worker.log`:
  ```
  2026-07-02 14:32:42.692 ERROR [worker] upstream call failed request_id=16ce72300cf58a32 err=ECONNRESET upstream=10.0.3.44:8443 (retries exhausted)
  ```
- That `request_id` corresponds to this web request, which the web tier reported as
  successful even though the job behind it later failed:
  ```
  2026-07-02 14:32:40.073 INFO [request] method=POST path=/checkout status=202 latency_ms=36 user_id=59787 request_id=16ce72300cf58a32
  ```
- Before 14:32:42, **zero** `/checkout` jobs fail (checked every hour from 00:00–13:00 — 0
  failures each hour). Immediately after, failures appear every hour through the end of
  the log (23:59), so the problem was never resolved within the captured window.

## 2. Which endpoint is affected?

**`POST /checkout`.**

Every single one of the 2,385 `ERROR [worker] upstream call failed` entries in
`worker.log` has a `request_id` that maps back to a `/checkout` request in `web.log` —
0 of them map to `/login`, `/orders`, or anything else:

```
== Endpoint distribution of the failed worker jobs ==
  /checkout: 2385
```

`/login` and `/orders` also return `status=202` and hand off to the same worker, but
none of their jobs ever show up in the error set — only checkout jobs fail.

## 3. What do the failing requests have in common?

Every failing request is a `POST /checkout` that:

1. **Returns `status=202` to the user** (so the web tier reports success — this is why
   support saw "orders were successfully placed").
2. **Has a corresponding `worker.log` entry that fails**, not completes:
   ```
   ERROR [worker] upstream call failed request_id=<id> err=ECONNRESET upstream=10.0.3.44:8443 (retries exhausted)
   ```
   instead of the normal:
   ```
   INFO [worker] job completed request_id=<id> duration_ms=<n>
   ```
3. **All 2,385 failures target the exact same upstream address**, `10.0.3.44:8443` — no
   other upstream host appears anywhere in `worker.log`.
4. **The failure rate is high but not 100%**, and it's remarkably stable once the
   incident starts — consistently ~42–45% of checkout traffic per hour, hour after hour:

   ```
   14:00  total= 619  failed=  40  rate=  6.5%   (deploy landed mid-hour)
   15:00  total= 651  failed= 267  rate= 41.0%
   16:00  total= 687  failed= 303  rate= 44.1%
   17:00  total= 765  failed= 341  rate= 44.6%
   18:00  total= 838  failed= 364  rate= 43.4%
   19:00  total= 789  failed= 344  rate= 43.6%
   20:00  total= 701  failed= 308  rate= 43.9%
   21:00  total= 463  failed= 202  rate= 43.6%
   22:00  total= 323  failed= 146  rate= 45.2%
   23:00  total= 156  failed=  70  rate= 44.9%
   ```

   Overall: 2,385 / 5,624 = **42.4%** of all `/checkout` requests since the first
   failure never completed. A steady ~43% failure rate (never 0%, never 100%) is the
   signature of traffic being split across a small pool of backend instances/connections
   where **one of them** is broken — consistent with the upstream evidence in Q3/bonus
   below (all failures hit the same single IP).

## 4. How many distinct users were affected?

**2,335 distinct `user_id` values** had at least one silently-dropped order (2,385
failed checkouts total, so a handful of users had more than one failed attempt).

## Bonus: root cause

The evidence points at the `v2.14.3` deploy (commit `219c39f`) at 14:31:13:

- Failures start 89 seconds after that deploy and never occur before it.
- Every failure is the same error (`ECONNRESET`, retries exhausted) against the same
  single upstream host/port, `10.0.3.44:8443` — this string never appears anywhere else
  in either log, including in successful job logs (which don't log an upstream at all).
- The failure rate holds steady at ~43% rather than spiking to 100% or trending upward,
  which is more consistent with **checkout traffic being load-balanced across several
  backend/order-processing instances, and one of them (10.0.3.44:8443) becoming
  unreachable or misconfigured after the v2.14.3 rollout**, than with a total outage or a
  resource leak.
- The only other error activity in the logs (`[metrics-worker] AnalyticsUploadTimeout`,
  ~33/hour throughout the entire day, `WARN [db] slow query`, `401` on `/api/user`, `404`
  on `/product/<id>`) is flat/proportional to traffic across all 24 hours and shows no
  change at 14:31, so none of it is related — see Investigation Process below.

**Likely root cause:** the v2.14.3 deploy broke connectivity (bad config, cert, routing,
or network policy) from one checkout-processing backend to its upstream at
`10.0.3.44:8443`, while other instances in the pool kept working — so roughly
40-something percent of checkout jobs (whichever landed on the bad instance) silently
failed after already telling the customer their order was accepted.

## Investigation Process

- Started by profiling both files: line counts, distinct `path` values, status code
  distribution, and log levels/tags, to get a map of what's "normal" before hunting for
  anomalies.
- Noticed `status=202` (async-accepted) is only used by `/login`, `/orders`, and
  `/checkout`, and that the `202` count (27,917) exactly matches the total `[worker]`
  entry count in `worker.log` — confirmed these are the requests that hand off to a
  background job.
- Checked `404` on `/product/<id>` — random distribution across the whole day and across
  many distinct product IDs, no timestamp clustering. Ruled out as normal
  "product no longer exists" noise, unrelated to the incident.
- Checked `401` on `/api/user` — small, steady trickle proportional to traffic volume
  all day (expired-session type behavior). Ruled out as unrelated.
- Checked `WARN [db] slow query` in `web.log` — volume tracks overall traffic volume
  hour by hour (busier hours = more slow queries) with no spike or change at the
  incident time. Ruled out as background DB load, not the cause of missing orders.
- Checked `ERROR [metrics-worker] AnalyticsUploadTimeout` in `worker.log` — near-constant
  ~33/hour for all 24 hours of the log, including hours with zero checkout failures.
  Ruled out as an unrelated, pre-existing background issue with the analytics pipeline.
- Found `ERROR [worker] upstream call failed ... ECONNRESET ... retries exhausted` as the
  only other error type in `worker.log`, all 2,385 pointing at a single upstream
  (`10.0.3.44:8443`) and clustered entirely after 14:32:42.
- Cross-referenced every failing `request_id` back into `web.log`: 100% of them are
  `POST /checkout` requests that got `status=202` — i.e., the customer-facing response
  looked successful even though the backend job silently failed. This directly explains
  the support report ("orders placed but never appeared").
- Found the `[deploy] release v2.14.3` marker 89 seconds before the first failure and
  confirmed zero checkout failures in any hour before that deploy, versus a stable
  ~43%/hour failure rate in every hour after — pointing at the deploy as the trigger and
  ruling out a gradual resource leak (rate doesn't climb) or a total outage (rate isn't
  100%).
- Verified every `/checkout` request_id appears exactly once in `worker.log` (either as
  `job completed` or as an error) — no requests silently vanish without a matching worker
  log line, which confirms the worker log fully accounts for the discrepancy and no
  other failure mode is in play.
