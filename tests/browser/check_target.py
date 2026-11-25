"""Run before any migration or seed write. Fails unless Django's effective database settings are the throwaway TiDB this run started.
Prints only non-secret target facts (engine, host, port, names). Arguments: expected port."""

import sys

import django
from django.conf import settings

django.setup()
want_port = int(sys.argv[1])
db = settings.DATABASES["default"]
facts = {
    "settings": settings.SETTINGS_MODULE,
    "engine": db["ENGINE"],
    "host": db["HOST"],
    "port": db["PORT"],
    "name": db["NAME"],
    "test_name": db.get("TEST", {}).get("NAME"),
    "user": db["USER"],
}
print("Django target:", ", ".join("%s=%s" % kv for kv in facts.items()))
ok = (
    settings.SETTINGS_MODULE == "config.settings.test_tidb"
    and facts["host"] == "127.0.0.1"
    and int(facts["port"]) == want_port
    and facts["name"] == "drill_db"
    and facts["test_name"] == "test_drill_db"
    and list(settings.DATABASES) == ["default"]
)
if not ok:
    print("ABORT: Django would not use the throwaway database. Nothing was written.")
    sys.exit(2)
from django.db import connection  # noqa: E402

with connection.cursor() as c:
    c.execute("SELECT DATABASE()")
    cur = c.fetchone()[0]
    c.execute("SELECT @@port")
    port = int(c.fetchone()[0])
if cur != "drill_db" or port != want_port:
    print("ABORT: the live connection is not the throwaway server. Nothing was written.")
    sys.exit(2)
print("Live connection confirmed: database drill_db on the owned port.")
