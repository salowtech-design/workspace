"""Bounded local HTTP load diagnostic. NOT a capacity test and not part of CI.

Run through run.sh so it gets the throwaway TiDB (unistore) and the real uvicorn server:
    TIDB_SERVER=... PYTHON=... LOAD_LEVELS=10,50,100 LOAD_SECONDS=30 bash tests/browser/run.sh load_http

What it does: makes synthetic employees in the throwaway store, gives each one a stored Django session (this BYPASSES the
login and MFA flow), then drives real HTTP requests at the running server from one thread per simulated user, at a few
fixed concurrency levels. Each request is checked for a 200 with the user's own id in the body (a redirect to sign-in or
an empty page is a failure, not a success). Each simulated user sends X-Forwarded-For with its own address, which the app
accepts from the local trusted proxy address, so the per-address rate limiter sees many clients.

Limits, always true of this tool: the load generator, the server and the database share one machine (see the report for
CPU count and memory); the database is a single-node unistore, not TiDB Cloud; one uvicorn worker; loopback network;
no real browsers; no login or MFA cost; read-mostly workload with a small share of chat sends. The numbers are a floor on
one box, not a capacity figure.

Finite by design: each level runs for LOAD_SECONDS (default 30) after a short warmup, request timeout 15 s, and the run
STOPS at the first level that shows sustained trouble (see STOP below). It never pushes on after a collapse.
"""

import http.client
import json
import os
import random
import re
import sys
import threading
import time
import uuid

LEVELS = [int(x) for x in os.environ.get("LOAD_LEVELS", "10,50,100").split(",") if x.strip()]
SECONDS = int(os.environ.get("LOAD_SECONDS", "30"))
WARMUP = 3
TIMEOUT = 15
THINK = (0.4, 1.2)  # seconds between one user's requests
# STOP: after a level, stop the run if any holds. Failures count everything that is not an expected success;
# a chat send answered with the app's own 429 (its per-person send limit) is counted apart and is not a failure.
STOP_FAIL_RATE = 0.10
STOP_P95 = 8.0
STOP_MEM_MB = 250
REPORT = os.environ.get("LOAD_REPORT", "/tmp/load_report.json")

PAGES = [  # (name, path, weight, text the real page must contain)
    ("tasks", "/tasks/", 25, "Tasks"),
    ("notifications", "/notifications/", 15, "Notifications"),
    ("chat_list", "/chat/", 15, "Chat"),
    ("chat_space", None, 20, "Load group"),
    ("calendar", "/calendar/", 10, "Calendar"),
    ("attendance_history", "/attendance/history/", 5, "My attendance"),
    ("posts_feed", "/posts/", 10, "Work Log"),
]
SEND_SHARE = 0.05


def _meminfo():
    d = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")
        d[k] = int(v.split()[0])
    return d


def _procs():
    out = {}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = open(f"/proc/{pid}/cmdline").read().replace("\0", " ")
            rss = int(re.search(r"VmRSS:\s+(\d+)", open(f"/proc/{pid}/status").read()).group(1))
        except Exception:  # noqa: BLE001
            continue
        for tag, needle in (("uvicorn", "uvicorn"), ("tidb", "tidb-server")):
            if needle in cmd and "bash" not in cmd.split()[0]:
                out[tag] = max(out.get(tag, 0), rss // 1024)
    return out


SESSION_NAME = "salow_workspace_session"
LISTS = {}  # employee id -> that user's task list page (/tasks/ itself redirects to it)


def setup(n_users):
    global SESSION_NAME
    import django
    from django.conf import settings

    django.setup()
    SESSION_NAME = settings.SESSION_COOKIE_NAME
    from django.test import Client
    from django.utils import timezone

    from apps.accounts.models import CustomUser
    from apps.attendance.models import WorkSession
    from apps.chat import services as cs
    from apps.core.models import Notification
    from apps.posts.models import Post
    from apps.tasks import services as ts

    users = []
    for i in range(n_users):
        users.append(
            CustomUser.objects.create(
                employee_id=f"LD-{i:04d}", full_name=f"Load User {i:04d}", email=f"ld{i:04d}@salowtech.com",
                role="employee", status="active", is_active=True, email_verified=True,
            )
        )
    now = timezone.now()
    today = timezone.localdate()
    Notification.objects.bulk_create([Notification(user=u, kind="load", title=f"Load notice {k}", link="/tasks/") for u in users for k in range(3)])
    Post.objects.bulk_create([Post(author=u, title=f"Load log {u.employee_id}", content="Synthetic load text.", work_date=today) for u in users])
    WorkSession.objects.bulk_create(
        [WorkSession(user=u, work_date=today, check_in_at=now.replace(hour=4, minute=0), check_out_at=now.replace(hour=12, minute=0),
                     state="completed", verification_method="face") for u in users[: max(1, n_users // 2)]]
    )
    for u in users:
        tl = ts.create_list(u, "Load list")
        LISTS[u.employee_id] = f"/tasks/list/{tl.public_id}/"
        for k in range(5):
            ts.create_task(u, tl, f"Load task {k}")
    spaces = {}  # employee id -> that user's group (a group holds at most 50 others, so groups of 40)
    for gi in range(0, len(users), 40):
        chunk = users[gi: gi + 40]
        grp = cs.create_group(chunk[0], "Load group", [u.pk for u in chunk[1:]])
        for k in range(40):
            cs.post_message(chunk[k % len(chunk)], grp.public_id, f"load message {k}", f"seed-{gi}-{k}")
        for u in chunk:
            spaces[u.employee_id] = str(grp.public_id)
    sessions = []
    for u in users:
        c = Client()
        c.force_login(u)
        sessions.append((u.employee_id, c.cookies[settings.SESSION_COOKIE_NAME].value))
    return sessions, spaces


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.by = {}  # name -> list of (seconds, outcome)

    def add(self, name, sec, outcome):
        with self.lock:
            self.by.setdefault(name, []).append((sec, outcome))


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


def user_loop(idx, eid, sess, port, space, stop_at, stats, start_evt):
    ip = f"10.{(idx >> 16) & 255}.{(idx >> 8) & 255}.{(idx & 255) or 1}"
    rnd = random.Random(idx)
    cookies = {SESSION_NAME: sess}
    space = space[eid]
    conn = None
    names = [p[0] for p in PAGES]
    weights = [p[2] for p in PAGES]
    start_evt.wait()

    def hdr():
        h = {"Host": f"app.salowtech.com:{port}", "X-Forwarded-For": ip, "Accept": "text/html"}
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        return h

    def req(method, path, body=None, extra=None):
        nonlocal conn
        if conn is None:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
        h = hdr()
        if extra:
            h.update(extra)
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read().decode("utf-8", "replace")
        for sc in r.msg.get_all("Set-Cookie") or []:
            m = re.match(r"(csrftoken)=([^;]+)", sc)
            if m:
                cookies[m.group(1)] = m.group(2)
        return r.status, data

    while time.time() < stop_at:
        send = rnd.random() < SEND_SHARE
        name = "chat_send" if send else rnd.choices(names, weights)[0]
        t0 = time.time()
        try:
            if send:
                if "csrftoken" not in cookies:
                    req("GET", f"/chat/space/{space}/")
                st, data = req(
                    "POST", f"/chat/space/{space}/send/",
                    body=f"body=load+{uuid.uuid4().hex[:8]}&client_msg_id={uuid.uuid4().hex}",
                    extra={"Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": cookies.get("csrftoken", ""), "Referer": f"http://app.salowtech.com:{port}/"},
                )
                outcome = "ok" if st == 302 else ("throttled" if st == 429 else f"bad_{st}")
            else:
                path = LISTS[eid] if name == "tasks" else (next(p[1] for p in PAGES if p[0] == name) or f"/chat/space/{space}/")
                st, data = req("GET", path)
                need = next(p[3] for p in PAGES if p[0] == name)
                if st == 200 and eid in data and need in data:
                    outcome = "ok"
                elif st == 200:
                    outcome = "bad_body"
                else:
                    outcome = f"bad_{st}"
        except Exception as ex:  # noqa: BLE001
            outcome = "timeout" if "timed out" in str(ex) else "conn_error"
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = None
        stats.add(name, time.time() - t0, outcome)
        time.sleep(rnd.uniform(*THINK))


def run_level(n, sessions, port, space):
    stats = Stats()
    stop_at_holder = time.time() + WARMUP + SECONDS + 2
    start = threading.Event()
    ths = [threading.Thread(target=user_loop, args=(i, e, s, port, space, stop_at_holder, stats, start), daemon=True) for i, (e, s) in enumerate(sessions[:n])]
    for t in ths:
        t.start()
    cpu0 = time.process_time()
    wall0 = time.time()
    start.set()
    samples = []
    while time.time() < stop_at_holder:
        time.sleep(5)
        m = _meminfo()
        samples.append({"load1": os.getloadavg()[0], "mem_avail_mb": m["MemAvailable"] // 1024, **_procs()})
    for t in ths:
        t.join(timeout=TIMEOUT + 5)
    wall = time.time() - wall0
    client_cpu = time.process_time() - cpu0
    # drop the warmup window by request: stats were not time-stamped, so the warmup is simply included and said so.
    rows = {}
    allv, fails, throttled, total = [], 0, 0, 0
    for name, lst in stats.by.items():
        lat = sorted(x[0] for x in lst)
        oc = {}
        for _, o in lst:
            oc[o] = oc.get(o, 0) + 1
        rows[name] = {"n": len(lst), "p50": round(pct(lat, .5), 3), "p95": round(pct(lat, .95), 3), "p99": round(pct(lat, .99), 3), "max": round(lat[-1], 3), "outcomes": oc}
        for sec, o in lst:
            total += 1
            allv.append(sec)
            if o == "throttled":
                throttled += 1
            elif o != "ok":
                fails += 1
    allv.sort()
    return {
        "users": n, "seconds": round(wall, 1), "requests": total, "req_per_s": round(total / wall, 1),
        "failures": fails, "fail_rate": round(fails / total, 4) if total else 1.0, "throttled_chat_sends": throttled,
        "p50": round(pct(allv, .5), 3), "p95": round(pct(allv, .95), 3), "p99": round(pct(allv, .99), 3),
        "client_cpu_share_of_wall": round(client_cpu / wall, 2), "per_page": rows,
        "min_mem_avail_mb": min((s["mem_avail_mb"] for s in samples), default=None),
        "max_load1": max((s["load1"] for s in samples), default=None),
        "max_rss_mb": {k: max(s.get(k, 0) for s in samples) for k in ("uvicorn", "tidb")},
    }


def main():
    port = int(os.environ["DRILL_APP"].rsplit(":", 1)[1])
    n_users = max(LEVELS)
    print(f"setup: {n_users} synthetic users, stored sessions (login bypassed)", flush=True)
    t0 = time.time()
    sessions, space = setup(n_users)
    print(f"setup done in {time.time() - t0:.0f}s", flush=True)
    env = {"cpus": os.cpu_count(), "mem_total_mb": _meminfo()["MemTotal"] // 1024, "seconds_per_level": SECONDS, "warmup_included_s": WARMUP,
           "think_time_s": THINK, "request_timeout_s": TIMEOUT, "stop_rules": {"fail_rate": STOP_FAIL_RATE, "p95_s": STOP_P95, "mem_avail_mb": STOP_MEM_MB}}
    levels, stopped = [], None
    for n in LEVELS:
        res = run_level(n, sessions, port, space)
        levels.append(res)
        print(f"level {n}: {res['requests']} req, {res['req_per_s']}/s, fail {res['fail_rate']:.1%}, p50 {res['p50']}s p95 {res['p95']}s p99 {res['p99']}s, "
              f"client cpu {res['client_cpu_share_of_wall']}, min mem {res['min_mem_avail_mb']} MB, load1 max {res['max_load1']}", flush=True)
        why = []
        if res["fail_rate"] > STOP_FAIL_RATE:
            why.append("failure rate")
        if res["p95"] > STOP_P95:
            why.append("p95 latency")
        if res["min_mem_avail_mb"] is not None and res["min_mem_avail_mb"] < STOP_MEM_MB:
            why.append("low memory")
        if why:
            stopped = {"after_users": n, "because": why}
            print(f"STOP after {n} users: {', '.join(why)}. Not escalating.", flush=True)
            break
    json.dump({"env": env, "levels": levels, "stopped": stopped}, open(REPORT, "w"), indent=1)
    print("report:", REPORT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
