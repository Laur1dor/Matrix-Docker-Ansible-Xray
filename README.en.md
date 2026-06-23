# Matrix-Docker-Ansible-Xray

A ready-to-run, "family/team" **Matrix messenger** built on
[spantaleev/matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy) — but with a
pre-assembled, tested set: **voice/video calls, guest conferences, an AI assistant that reads photos, a translator,
GIFs, reminders, moderation, a helpdesk, a newcomer greeter, 6 messenger bridges** and an **optional Xray (VLESS/REALITY)
censorship-circumvention toolkit**.

Works on a **single VPS**, on a **home Proxmox/server**, or **behind your existing reverse proxy**
(Traefik / nginx / Nginx Proxy Manager) — on the same or a separate machine.

> 🇬🇧 This is the English README. 🇷🇺 The primary one is [`README.md`](README.md).
> 📖 What it does & how to use it (plain language, RU): [`docs/FUNCTIONALITY.ru.md`](docs/FUNCTIONALITY.ru.md).
> ⚠️ **Never commit secrets** — see [Security](#-security). The repo only ships placeholder templates.

---

## ⚡ What's inside

**Core.** Synapse (homeserver) · MAS — modern auth, invite-token registration · Element Web · Element Admin
(Ketesa/synapse-admin) · ntfy (push) · well-known delegation (so IDs look like `@you:example.com`).

**Realtime.** Element Call + LiveKit (1:1, groups, screen share, **guests via link, no signup**, participant cap) ·
coturn (TURN/NAT traversal) · automatic E2EE for private chats.

**Bots.**
- 🤖 **AI assistant** (baibot) — answers anything, **understands attached photos**, in Russian by default; runs through a
  local **free-model gateway** with auto-failover on rate limits (see [`llm-gateway.py`](llm-gateway.py)).
- 🌐 **Translator + GIFs** (maubot): `!tr`, `!giphy`, and **translate-by-reaction** (flag emoji or 🌐).
- 👋 **Newcomer greeter** — a personal welcome DM to every new user.
- ⏰ **Reminders**, 🛡 **Moderation** (Draupnir), 🆘 **Helpdesk** (Honoroit).

**Bridges** (self-service, local users only): Telegram · WhatsApp · Signal · Discord · Instagram · Android-SMS.

**Infra.** Optional **Xray toolkit** (`xray-manage` — hot-swap VLESS without rebuilds) · DB auto-compression · Postgres
backups · fast purge of deleted rooms · **weekly update check with an AI risk assessment** ([`check-updates.py`](check-updates.py)).

---

## 📋 Requirements

**Server:** Debian 12 (or Ubuntu), **≥ 6 GB RAM** (4 GB is tight), **2+ cores**, ~25 GB disk to start (media grows).
Docker required; on a Proxmox LXC enable **nesting=1**. One public IP.

**Domain:** one domain (e.g. `example.com`) + subdomains. Defaults:

| Subdomain | Purpose |
|---|---|
| `matrix.` | the homeserver + federation |
| `element.` *(any name)* | Element web client |
| `admin.` | admin panel |
| `call.` | Element Call |
| `push.` | ntfy |
| apex `example.com` | well-known delegation (so IDs are `@you:example.com`) |

Subdomain names are your choice (set in config). **DNS:** apex → `A` to your IP; subdomains → `CNAME` to the apex (or
`A` to the same IP). A **wildcard cert** `*.example.com` (+ apex) is easiest.

**Open ports:** `80`, `443` (HTTP/HTTPS incl. federation on 443). For calls (LiveKit/TURN) — UDP/TCP ranges listed in
[`docs/PORTS.md`](docs/PORTS.md). Single public server → just unfirewalled; behind NAT → port-forward them.

---

## 🏗 Deployment options

The Matrix stack always runs in Docker (the playbook installs it). The difference is **what sits in front and terminates TLS**:

- **A — single server / VPS (simplest):** the playbook runs its **own Traefik** and gets Let's Encrypt certs itself.
  Nothing external needed.
- **B — behind your reverse proxy (Traefik / nginx / NPM):** Matrix serves plain HTTP on one port; your proxy terminates
  TLS and forwards every matrix host to `http://MATRIX_HOST:PORT` by Host header. Examples:
  [`examples/traefik-matrix.yaml`](examples/traefik-matrix.yaml), [`examples/nginx-matrix.conf`](examples/nginx-matrix.conf).
  **Don't** attach header-mangling middleware — it breaks Element Call and OAuth.
- **C — home Proxmox / behind a router:** same as A or B, plus forward 80/443 and the call media ports to the server.

> Call (RTC) media **cannot** go through an HTTP reverse proxy — those ports hit the Matrix host directly.

---

## 🚀 Install

1. Install deps on the server: `git`, **`sudo`** (mandatory; absent on minimal Debian), Docker
   (`curl -fsSL https://get.docker.com | sh`), **Ansible via pipx** (distro packages are often too old; need ≥ 2.15.1), `just`.
2. Clone the upstream **spantaleev/matrix-docker-ansible-deploy** and run `just roles`.
3. Clone **this** repo alongside it and run the interactive script — it asks everything, explains where to get each
   value, generates all random secrets, and writes your `vars.yml`:
   ```bash
   ./setup.sh
   ```
4. Copy the resulting `vars.yml` into `inventory/host_vars/<your-domain>/vars.yml`, set up `inventory/hosts`.
5. `just install-all` → register the admin (`just register-user admin <password> yes`).
6. (Option B) Configure your external proxy from [`examples/`](examples/) + add DNS records.
7. (Optional) Install the maubot plugins and the AI gateway — see [`docs/EXTRAS.md`](docs/EXTRAS.md).

A full annotated value reference ("what to change") is in [`examples/vars.sample.yml`](examples/vars.sample.yml).

---

## 🔒 Security

- **Never commit your real `vars.yml`** — it holds passwords, keys, tokens. [`.gitignore`](.gitignore) blocks `vars.yml`,
  backups, `.secrets/`, keys, xray links. Only `examples/vars.sample.yml` (placeholders) is tracked.
- Registration is **closed** — invite-token only.
- **Bridges are local-users-only** — a foreign homeserver can't attach its account.
- **Private chats are E2EE by default** — enforced server-side, no manual toggling.

---

## 🔄 Maintenance

- Update: `just update && just install-all` (read upstream `CHANGELOG.md`). The built-in weekly checker posts an AI risk
  assessment to an admin room — see [`check-updates.py`](check-updates.py).
- DB: state-group auto-compression + logical Postgres dumps.
- Logs: `journalctl -u matrix-synapse.service -f` (and other services).

---

## 📂 Repo layout

| Path | What |
|---|---|
| [`examples/vars.sample.yml`](examples/vars.sample.yml) | full annotated config template (placeholders) |
| [`examples/`](examples/) | external Traefik / nginx examples |
| [`setup.sh`](setup.sh) | interactive `vars.yml` generator |
| [`llm-gateway.py`](llm-gateway.py) | local free-LLM gateway (pool + failover + vision) for the AI bot |
| [`check-updates.py`](check-updates.py) | weekly update check with AI risk assessment |
| [`maubot-plugins/`](maubot-plugins/) | maubot plugins: translate-by-reaction, GIFs, newcomer greeter |
| [`docs/`](docs/) | functionality, ports, extras install |

---
Based on [matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy) (AGPL-3.0).
