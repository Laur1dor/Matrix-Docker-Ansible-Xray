# Matrix-Docker-Ansible-Xray

Self-hosted **Matrix (Synapse)** built on [spantaleev/matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy), adapted for a typical **home Proxmox lab**: the stack lives in an LXC, the **router is the only firewall / public ingress**, TLS terminates on a **separate external Traefik**, and outbound traffic can be sent through **Xray (VLESS/REALITY)** with hot-swap and failover when networks are hostile.

On top of that — a full "household" feature set: **messenger + voice/video, guest conferences, an AI assistant with image understanding, a translator, reminders, moderation, a helpdesk, and 6 bridges** (Telegram/WhatsApp/Discord/Signal/Instagram/SMS) with self-service onboarding.

> 🇬🇧 This is the English overview. 🇷🇺 Primary README: [`README.md`](README.md).
> 📖 **What it can do & how to use it (plain language, RU):** [`docs/FUNCTIONALITY.ru.md`](docs/FUNCTIONALITY.ru.md)

> ⚠️ **Never commit secrets.** Only [`examples/vars.sample.yml`](examples/vars.sample.yml) (placeholders) lives in git. The real `vars.yml` (passwords, keys, tokens) is blocked by [`.gitignore`](.gitignore). See [Security](#security).

## What's inside
**Core:** Synapse · MAS (modern auth, token-gated registration) · Element Web · Element Admin (Ketesa/synapse-admin) · ntfy (push) · well-known delegation (`@you:domain` user-ids).
**Calls:** Element Call + LiveKit (1-1, group up to 10, screen share, **guests via link**) · coturn (legacy TURN).
**Bots:** **baibot** (AI, vision, RU by default, E2EE) · **maubot + translator** (`!tr`) · **reminder-bot** · **registration-bot** · **Draupnir** (moderation) · **Honoroit** (helpdesk).
**Bridges (self-onboarding, local users only):** Telegram · WhatsApp · Signal · Discord · Instagram · Android-SMS.
**Infra:** **Xray toolkit** (`xray-manage`, hot-swap with no Matrix rebuild) · **livekit-ip-watcher** (auto public-IP tracking) · DB auto-compressor · logical Postgres backups · fast purge of deleted rooms.

## Architecture

```
                 Internet
                    │  (1 public IP)
            ┌───────▼────────┐
            │  Router/OpenWRT │  firewall + DPI bypass (zapret)
            │  port-forwards  │
            └───┬────────┬────┘
       80/443   │        │  LiveKit media: 7882/udp,7881/tcp,
                │        │  3479/udp,5350/tcp,30000-30020/udp
        ┌───────▼──┐  ┌──▼──────────────────────────────┐
        │ .105     │  │ .106  LXC (Debian 12)            │
        │ Traefik  │  │  internal Traefik :81 (no TLS)   │
        │ (TLS,    │──┼─▶ Synapse/MAS/Element/Ketesa/... │
        │  CF DNS) │  │  LiveKit · coturn · ntfy · bots  │
        └──────────┘  │  bridges · Xray (selective)      │
                      └──────────────────────────────────┘
```

- **External Traefik (.105)** holds the certs (Cloudflare DNS-01 wildcard) and proxies every Matrix host to a **single** backend `http://<LXC>:81` (Host routing). Example: [`examples/traefik-matrix.yaml`](examples/traefik-matrix.yaml).
- **Internal Traefik (.106:81)** is the playbook's black box: TLS off, it fans out to services, resolves MAS/Ketesa route-stealing, federation, etc.
- **Federation runs on 443** via well-known delegation (no public 8448).
- **Call media bypasses .105** — router port-forwards straight to .106 (RTC can't go through an HTTP reverse proxy).

## DNS (Cloudflare, DNS-only / grey cloud)
Apex `A` → public IP; subdomains `CNAME` → apex: `matrix`, `elementweb`, `admin`, `elementcall`, `push` (+ apex for well-known). A wildcard cert `*.domain` (+apex) covers everything.

## Public ports (on the public IP)
| Port | Proto | → | Purpose |
|---|---|---|---|
| 80, 443 | TCP | .105 | HTTP(S) (incl. federation on 443) |
| 7882 | UDP | .106 | LiveKit ICE/UDP (main media) |
| 7881 | TCP | .106 | LiveKit ICE/TCP (fallback) |
| 3479 | UDP | .106 | TURN/UDP |
| 5350 | TCP | .106 | TURN/TLS |
| 30000-30020 | UDP | .106 | TURN relay range |
| 3478, 49152-49172 | TCP/UDP | .106 | coturn (legacy calls) |

## Quick start
On .106 (Debian 12, ≥6 GB RAM, Docker-in-LXC needs `nesting=1`):

1. **Deps:** `git`, **`sudo`** (absent on minimal Debian — required for ansible `become`), Docker (`get.docker.com`), **Ansible via pipx** (distro 2.14 is too old, need ≥2.15.1), `just`.
2. `git clone` the upstream playbook → `just roles`.
3. **Generate `vars.yml` for your domain** (auto-fills all random secrets):
   ```bash
   ./setup.sh --domain example.com --public-ip 1.2.3.4 --traefik-ip 192.168.1.105
   ```
   You only fill in API keys (Groq/OpenRouter), Telegram `api_id/api_hash`, the maubot web password and the Honoroit room id — the script lists them at the end.
4. Copy the result to `inventory/host_vars/<domain>/vars.yml`, set up `inventory/hosts` (local connection).
5. `just install-all` → register the admin (`just register-user admin <pass> yes`).
6. Drop [`examples/traefik-matrix.yaml`](examples/traefik-matrix.yaml) on the external Traefik.

Key fronting/federation variables:
```yaml
matrix_playbook_reverse_proxy_type: playbook-managed-traefik
matrix_playbook_ssl_enabled: true
traefik_config_entrypoint_web_secure_enabled: false        # TLS off internally
traefik_container_web_host_bind_port: '0.0.0.0:81'
traefik_config_entrypoint_web_forwardedHeaders_trustedIPs: ['<TRAEFIK_IP>/32']
matrix_synapse_http_listener_resource_names: ["client","federation"]
matrix_federation_public_port: 443
matrix_synapse_federation_port_enabled: false
matrix_synapse_tls_federation_listener_enabled: false
```

## Xray toolkit (optional censorship bypass)
`xray-manage` hot-swaps the VLESS upstream without rebuilding Matrix (Matrix only ever talks to a stable local proxy port):
```
xray-manage sub '<url>'      # subscription (base64/plain)
xray-manage link 'vless://...'
xray-manage refresh | status | test | on | off
```
The generated config uses an **observatory + balancer** (auto-picks a live server) with `fallbackTag: direct` (all upstreams dead → go direct, the server stays online). Routing is **selective**: LAN/RU → direct, domains in `proxy-domains.txt` → via VLESS, the rest → direct. Point a service through the proxy with `http_proxy=http://172.17.0.1:10809` on its container; the WhatsApp/Signal/Telegram bridges use socks (`172.17.0.1:10808`).

## Security
- **Never commit the real `vars.yml`** — it holds passwords, encryption keys, API keys, Telegram creds. [`.gitignore`](.gitignore) blocks `vars.yml`, `*.bak`, `.secrets/`, keys, xray links/subscriptions. Only `examples/vars.sample.yml` (placeholders) is committed.
- Registration is **closed** — token only (admin issues tokens from the MAS panel).
- **Bridges are local-users-only.** There is no `*` permission rule, so a foreign homeserver cannot link its Telegram/WhatsApp. Invites/messages from remote users to your users still work (that's federation).
- **DMs are end-to-end encrypted by default.** baibot and the translator also operate in encrypted rooms.

## Maintenance
- Update: `just update && just install-all` (read upstream `CHANGELOG.md` for breaking changes).
- DB: `synapse-auto-compressor` + `postgres-backup` (Proxmox PBS then grabs the dumps).
- Whole-LXC backup via Proxmox PBS + ZFS.
- Logs: `journalctl -u matrix-synapse.service -f` (and per other containers).
- Rooms abandoned by everyone are purged from the DB in ~1h (no lingering "Deletion in progress").

## Status
✅ **Done and running:** core, MAS auth, calls/video + guests, federation on 443, admin panels, server-wide user search, E2EE DMs, AI bot with image understanding, translator, reminders, moderation (Draupnir), helpdesk (Honoroit), welcome room, 6 self-onboarding bridges (local-only), Xray toolkit, dynamic LiveKit IP, backups/compression, fast room cleanup.

---
Based on [matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy) (AGPL-3.0).
