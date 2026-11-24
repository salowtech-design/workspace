"""
Salow Workspace — Real HTTP 1,000-User Multi-Worker Load & Soak Benchmark.
Replaces in-process Django Client tests with real HTTP socket requests across workers.
Complies with Section 15 of the IAM Foundation Completion Contract.

Features:
- Real HTTP connections via requests.Session (simulates genuine TCP sockets and HTTP headers).
- Multi-threaded virtual users executing realistic user journeys:
  1. Real HTTP POST to /auth/login/ with employee ID and password.
  2. Session cookie preservation (__Host-salow_workspace_session / sessionid).
  3. Real HTTP GET to /auth/security/ (Dashboard Read SLO).
  4. Real HTTP POST to /auth/security/draft/save/ (Write SLO).
- Real-time measurement of p50, p95, p99 latency, throughput (req/s), and error rate.
- Distinguishes expected 429 rate-limit responses from server errors (5xx).
"""

import argparse
import concurrent.futures
import json
import time
from typing import NamedTuple

import requests


class RequestMetric(NamedTuple):
    action: str
    status_code: int
    duration_ms: float
    success: bool


def format_stats(latencies: list[float]) -> dict:
    if not latencies:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0, "max": 0.0}
    sorted_lats = sorted(latencies)
    n = len(sorted_lats)
    return {
        "p50": sorted_lats[int(n * 0.50)],
        "p95": sorted_lats[min(int(n * 0.95), n - 1)],
        "p99": sorted_lats[min(int(n * 0.99), n - 1)],
        "avg": sum(sorted_lats) / n,
        "max": sorted_lats[-1],
    }


def simulate_virtual_user(
    base_url: str,
    user_index: int,
    iterations: int = 1,
) -> list[RequestMetric]:
    emp_id = f"ST-BENCH-{user_index:04d}"
    password = "BenchmarkUserPass2026!"
    metrics = []

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": f"SalowLoadBench/1.0 (VU-{user_index:04d})",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    for _ in range(iterations):
        # 1. Login Request
        t0 = time.perf_counter()
        try:
            r_login = session.post(
                f"{base_url}/auth/login/",
                data={"employee_id": emp_id, "password": password},
                allow_redirects=False,
                timeout=15,
            )
            lat_login = (time.perf_counter() - t0) * 1000.0
            is_success = r_login.status_code in (200, 302)
            metrics.append(RequestMetric("login", r_login.status_code, lat_login, is_success))
        except Exception:
            metrics.append(RequestMetric("login", 0, (time.perf_counter() - t0) * 1000.0, False))

        # 2. Authenticated Dashboard Navigation (Read)
        t1 = time.perf_counter()
        try:
            r_nav = session.get(f"{base_url}/auth/security/", timeout=15)
            lat_nav = (time.perf_counter() - t1) * 1000.0
            is_nav_ok = r_nav.status_code == 200
            metrics.append(RequestMetric("read_dashboard", r_nav.status_code, lat_nav, is_nav_ok))
        except Exception:
            metrics.append(
                RequestMetric("read_dashboard", 0, (time.perf_counter() - t1) * 1000.0, False)
            )

        # 3. Draft Save (Write)
        t2 = time.perf_counter()
        try:
            r_write = session.post(
                f"{base_url}/auth/security/draft/save/",
                data={
                    "section": "workplace_safety",
                    "draft_content": json.dumps({"notes": f"VU-{user_index} benchmark write"}),
                },
                timeout=15,
            )
            lat_write = (time.perf_counter() - t2) * 1000.0
            is_write_ok = r_write.status_code in (200, 302)
            metrics.append(
                RequestMetric("write_draft", r_write.status_code, lat_write, is_write_ok)
            )
        except Exception:
            metrics.append(
                RequestMetric("write_draft", 0, (time.perf_counter() - t2) * 1000.0, False)
            )

    return metrics


def run_benchmark(target_url: str, vus: int, workers: int, iterations: int):
    print("=" * 72)
    print("SALOW WORKSPACE — REAL HTTP 1,000-USER MULTI-WORKER BENCHMARK")
    print("=" * 72)
    print(f"Target URL:        {target_url}")
    print(f"Virtual Users:     {vus}")
    print(f"Parallel Workers:  {workers}")
    print(f"Iterations per VU: {iterations}")
    print("-" * 72)

    start_time = time.perf_counter()
    all_metrics: list[RequestMetric] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(simulate_virtual_user, target_url, i, iterations): i
            for i in range(1, vus + 1)
        }

        completed = 0
        for f in concurrent.futures.as_completed(futures):
            try:
                res = f.result()
                all_metrics.extend(res)
            except Exception as exc:
                print(f"[!] Worker error: {exc}")
            completed += 1
            if completed % 100 == 0 or completed == vus:
                elapsed = time.perf_counter() - start_time
                print(f"[*] Progress: {completed}/{vus} VUs completed ({elapsed:.1f}s elapsed)")

    total_time = time.perf_counter() - start_time
    total_reqs = len(all_metrics)

    reads = [m.duration_ms for m in all_metrics if m.action == "read_dashboard"]
    writes = [m.duration_ms for m in all_metrics if m.action == "write_draft"]
    logins = [m.duration_ms for m in all_metrics if m.action == "login"]

    successes = sum(1 for m in all_metrics if m.success)
    throttled = sum(1 for m in all_metrics if m.status_code == 429)
    errors = sum(1 for m in all_metrics if not m.success and m.status_code != 429)

    read_stats = format_stats(reads)
    write_stats = format_stats(writes)
    format_stats(logins)

    print("=" * 72)
    print("REAL HTTP BENCHMARK RESULTS SUMMARY:")
    print("=" * 72)
    print(f"Total Requests:       {total_reqs}")
    print(f"Elapsed Time:         {total_time:.2f} seconds")
    print(f"Throughput:           {total_reqs / total_time:.2f} req/s")
    print(
        f"Successful Requests:  {successes}/{total_reqs} ({successes / max(1, total_reqs) * 100:.1f}%)"
    )
    print(f"Throttled (HTTP 429): {throttled} (Expected rate-limiting enforcement)")
    print(f"Unhandled Errors:     {errors} ({errors / max(1, total_reqs) * 100:.2f}%)")
    print("-" * 72)
    print("AUTHENTICATED READ LATENCIES (SLO: p95 <= 500ms, p99 <= 1500ms):")
    print(f"  p50 (Median): {read_stats['p50']:.2f} ms")
    print(f"  p95:          {read_stats['p95']:.2f} ms")
    print(f"  p99:          {read_stats['p99']:.2f} ms")
    print(f"  Average:      {read_stats['avg']:.2f} ms")
    print("-" * 72)
    print("AUTHENTICATED WRITE LATENCIES (SLO: p95 <= 800ms):")
    print(f"  p50 (Median): {write_stats['p50']:.2f} ms")
    print(f"  p95:          {write_stats['p95']:.2f} ms")
    print(f"  p99:          {write_stats['p99']:.2f} ms")
    print(f"  Average:      {write_stats['avg']:.2f} ms")
    print("=" * 72)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real HTTP 1,000-User Load Benchmark")
    parser.add_argument("--url", default="http://localhost:8000", help="Target URL")
    parser.add_argument("--vus", type=int, default=1000, help="Number of virtual users")
    parser.add_argument("--workers", type=int, default=50, help="Parallel worker threads")
    parser.add_argument("--iterations", type=int, default=1, help="Iterations per VU")
    args = parser.parse_args()

    run_benchmark(args.url, args.vus, args.workers, args.iterations)
