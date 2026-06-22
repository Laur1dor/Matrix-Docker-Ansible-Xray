#!/usr/bin/env python3
"""Weekly check for matrix-docker-ansible-deploy updates -> notify admin in the alerts room."""
import json, os, subprocess, time, urllib.request, urllib.parse

REPO = "/root/matrix-docker-ansible-deploy"
TOK = open("/root/.secrets/admin_token").read().strip()
ROOM = open("/root/.secrets/alerts_room").read().strip()
STATE = "/root/.secrets/last_notified_update"
BASE = "https://matrix.example.com"

def git(*a):
    return subprocess.run(["git", "-C", REPO] + list(a), capture_output=True, text=True).stdout.strip()

branch = git("rev-parse", "--abbrev-ref", "HEAD")
if branch in ("", "HEAD"):
    branch = "master"
git("fetch", "-q", "origin")
local = git("rev-parse", "HEAD")
remote = git("rev-parse", f"origin/{branch}")
if not remote or local == remote:
    print("up to date"); raise SystemExit(0)

last = open(STATE).read().strip() if os.path.exists(STATE) else ""
if remote == last:
    print("already notified for", remote[:8]); raise SystemExit(0)

n = git("rev-list", "--count", f"HEAD..origin/{branch}")
log = git("log", "--oneline", "--no-decorate", f"HEAD..origin/{branch}")
log_lines = "\n".join(log.splitlines()[:15])
text = (f"🔔 Доступно обновление matrix-docker-ansible-deploy — {n} новых коммитов (ветка {branch}).\n\n"
        f"Последние изменения:\n{log_lines}\n\n"
        f"Как обновить (сначала глянь CHANGELOG.md на breaking-changes):\n"
        f"  cd {REPO} && just update && just install-all\n\n"
        f"(Автоматическое еженедельное уведомление.)")

ride = urllib.parse.quote(ROOM)
req = urllib.request.Request(
    f"{BASE}/_matrix/client/v3/rooms/{ride}/send/m.room.message/upd{int(time.time())}",
    data=json.dumps({"msgtype": "m.text", "body": text}).encode(),
    headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"}, method="PUT")
urllib.request.urlopen(req, timeout=30).read()
open(STATE, "w").write(remote)
print("notified:", n, "commits")
