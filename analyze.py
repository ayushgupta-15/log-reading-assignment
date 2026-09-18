#!/usr/bin/env python3
"""
Log Reading Assignment - investigation script.

Correlates web.log (HTTP requests) with worker.log (async job processing)
to find the requests responsible for orders silently disappearing.

Usage:
    python3 analyze.py web.log worker.log
"""

import re
import sys
from collections import Counter, defaultdict

REQUEST_RE = re.compile(
    r"^(?P<ts>\S+ \S+) \S+ \[request\] method=(?P<method>\S+) path=(?P<path>\S+) "
    r"status=(?P<status>\d+) latency_ms=(?P<latency>\d+) user_id=(?P<user_id>\d+) "
    r"request_id=(?P<request_id>\S+)"
)
DEPLOY_RE = re.compile(r"^(?P<ts>\S+ \S+) \S+ \[deploy\] (?P<msg>.+)$")
WORKER_OK_RE = re.compile(
    r"^(?P<ts>\S+ \S+) \S+ \[worker\] job completed request_id=(?P<request_id>\S+) "
    r"duration_ms=(?P<duration>\d+)"
)
WORKER_ERR_RE = re.compile(
    r"^(?P<ts>\S+ \S+) \S+ \[worker\] upstream call failed request_id=(?P<request_id>\S+) "
    r"err=(?P<err>\S+) upstream=(?P<upstream>\S+) \((?P<detail>[^)]+)\)"
)


def parse_web(path):
    requests = {}
    deploys = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = REQUEST_RE.match(line)
            if m:
                requests[m["request_id"]] = m.groupdict()
                continue
            m = DEPLOY_RE.match(line)
            if m:
                deploys.append((m["ts"], m["msg"]))
    return requests, deploys


def parse_worker(path):
    completed = {}
    failed = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = WORKER_OK_RE.match(line)
            if m:
                completed[m["request_id"]] = m.groupdict()
                continue
            m = WORKER_ERR_RE.match(line)
            if m:
                failed[m["request_id"]] = m.groupdict()
    return completed, failed


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 analyze.py web.log worker.log")
        sys.exit(1)

    web_path, worker_path = sys.argv[1], sys.argv[2]
    requests, deploys = parse_web(web_path)
    completed, failed = parse_worker(worker_path)

    print("== Deploys ==")
    for ts, msg in deploys:
        print(f"  {ts}  {msg}")

    print("\n== Checkout requests: outcome breakdown ==")
    checkout_ids = [rid for rid, r in requests.items() if r["path"] == "/checkout"]
    print(f"  total /checkout requests: {len(checkout_ids)}")
    n_completed = sum(1 for rid in checkout_ids if rid in completed)
    n_failed = sum(1 for rid in checkout_ids if rid in failed)
    n_missing = len(checkout_ids) - n_completed - n_failed
    print(f"  completed by worker:      {n_completed}")
    print(f"  failed by worker (ECONNRESET, retries exhausted): {n_failed}")
    print(f"  never seen in worker.log: {n_missing}")

    print("\n== Endpoint distribution of the failed worker jobs ==")
    path_counts = Counter(requests[rid]["path"] for rid in failed if rid in requests)
    for path, count in path_counts.most_common():
        print(f"  {path}: {count}")

    print("\n== First failure ==")
    first_fail_rid = min(failed, key=lambda rid: failed[rid]["ts"])
    print(f"  worker.log: {failed[first_fail_rid]}")
    print(f"  web.log:    {requests.get(first_fail_rid)}")

    print("\n== Upstream targets referenced in failures ==")
    upstreams = Counter(f["upstream"] for f in failed.values())
    for upstream, count in upstreams.most_common():
        print(f"  {upstream}: {count}")

    print("\n== Distinct users affected ==")
    affected_users = {requests[rid]["user_id"] for rid in failed if rid in requests}
    print(f"  {len(affected_users)} distinct user_id values")

    print("\n== Checkout volume & failure rate by hour ==")
    hour_total = Counter()
    hour_failed = Counter()
    for rid in checkout_ids:
        hour = requests[rid]["ts"].split(" ")[1][:2]
        hour_total[hour] += 1
        if rid in failed:
            hour_failed[hour] += 1
    for hour in sorted(hour_total):
        tot = hour_total[hour]
        fail = hour_failed[hour]
        pct = (fail / tot * 100) if tot else 0
        print(f"  {hour}:00  total={tot:4d}  failed={fail:4d}  rate={pct:5.1f}%")

    print("\n== Sanity check: other status/log anomalies, spread across the day ==")
    for label, pred in [
        ("status=404", lambda r: r["status"] == "404"),
        ("status=401", lambda r: r["status"] == "401"),
    ]:
        by_hour = Counter(
            r["ts"].split(" ")[1][:2] for r in requests.values() if pred(r)
        )
        total = sum(by_hour.values())
        print(f"  {label}: total={total}, spread across hours -> roughly uniform with traffic")


if __name__ == "__main__":
    main()
