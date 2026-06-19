# Matrix-Docker-Ansible-Xray

Self-hosted **Matrix (Synapse)** на основе [spantaleev/matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy), адаптированный под типовой **домашний Proxmox-стенд**: сервис живёт в LXC, наружу торчит **только роутер (главный фаервол)**, TLS терминирует **внешний Traefik** на отдельной машине, а исходящий трафик при блокировках идёт через **Xray (VLESS/REALITY)** с хот-свапом и фейловером.

> 🇷🇺 Это основной README. English version: [README.en.md](README.en.md) *(WIP)*.
> Рассчитано на homelab/devops: без разжёвывания, но с объяснением «что/зачем/почему».

## Что внутри
Synapse + MAS (новая авторизация) · Element Web · Element Admin · Ketesa (synapse-admin) · ntfy (push) · **Element Call + LiveKit** (звонки 1-1, группы, шара экрана) · coturn (легаси-TURN) · автокомпрессия БД · логические бэкапы Postgres · **Xray-тулкит** (`xray-manage`) · **livekit-ip-watcher** (авто-обновление внешнего IP).

## Архитектура

```
                 Интернет
                    │  (1 белый IP)
            ┌───────▼────────┐
            │  Роутер/OpenWRT │  фаервол + DPI-байпас (zapret)
            │  port-forwards  │
            └───┬────────┬────┘
       80/443   │        │  LiveKit media: 7882/udp,7881/tcp,
                │        │  3479/udp,5350/tcp,30000-30020/udp
        ┌───────▼──┐  ┌──▼──────────────────────────────┐
        │ .105     │  │ .106  LXC (Debian 12)            │
        │ Traefik  │  │  внутр. Traefik :81 (без TLS)    │
        │ (TLS,    │──┼─▶ Synapse/MAS/Element/Ketesa/... │
        │  CF DNS) │  │  LiveKit · coturn · ntfy         │
        └──────────┘  │  Xray (selective egress)         │
                      └──────────────────────────────────┘
```

- **Внешний Traefik (.105)** держит сертификаты (Cloudflare DNS-01, wildcard) и проксирует все matrix-хосты на **один** бэкенд `http://<LXC>:81` (Host-роутинг).
- **Внутренний Traefik (.106:81)** — «чёрный ящик» плейбука: TLS выключен, раскидывает по сервисам, разруливает «кражу роутов» MAS/Ketesa, федерацию и т.д.
- **Федерация** — на 443 через well-known делегацию (порт 8448 наружу не нужен).
- **Медиа звонков** идёт мимо .105 — напрямую port-forward'ом роутера на .106 (RTC нельзя проксировать через HTTP-reverse-proxy).

## Топология (пример)
| Хост | Роль |
|---|---|
| Роутер (OpenWRT) | Единственный белый IP, port-forwards, DPI-байпас (zapret) |
| `.105` | Внешний Traefik (docker-compose), TLS, Cloudflare DNS-challenge |
| `.106` | LXC с Matrix-стеком (этот репо) |

## DNS (Cloudflare, DNS-only / grey cloud)
Апекс `A` → белый IP; поддомены `CNAME` → апекс:
`matrix`, `elementweb`, `admin`, `elementcall`, `push` (+ апекс для well-known).
Wildcard-сертификат `*.domain` (+апекс) покрывает все поддомены — отдельные серты не нужны.

## Порты наружу (на белый IP)
| Порт | Proto | → | Зачем |
|---|---|---|---|
| 80, 443 | TCP | .105 | HTTP(S) (всё, включая федерацию на 443) |
| 7882 | UDP | .106 | LiveKit ICE/UDP (основное медиа) |
| 7881 | TCP | .106 | LiveKit ICE/TCP (фоллбэк) |
| 3479 | UDP | .106 | TURN/UDP |
| 5350 | TCP | .106 | TURN/TLS (терминит внутр. Traefik) |
| 30000-30020 | UDP | .106 | TURN relay range |
| 3478, 49152-49172 | TCP/UDP | .106 | coturn (легаси-звонки), если включён |

## Внешний Traefik: один dynamic-файл
`/<traefik>/dynamic/matrix.yaml` — роутит все matrix-хосты на `:81` LXC. **Без** header/auth-middleware (ломают Element Call). См. [`examples/traefik-matrix.yaml`](examples/traefik-matrix.yaml).

## Установка (кратко)
На .106 (Debian 12, ≥6 ГБ RAM, Docker-in-LXC требует `nesting=1`):
1. Зависимости: `git`, **`sudo`** (в минимальном Debian нет — обязателен для ansible `become`), Docker (`get.docker.com`), **Ansible через pipx** (репозиторный 2.14 слишком стар, нужен ≥2.15.1), `just`.
2. `git clone` плейбука → `just roles`.
3. `inventory/hosts` (local connection) + `inventory/host_vars/<matrix-domain>/vars.yml` (см. [`examples/vars.sample.yml`](examples/vars.sample.yml)).
4. `just install-all` → регистрация админа (`just register-user admin <pass> yes`).

Ключевые переменные (фронт внешнего Traefik + федерация на 443):
```yaml
matrix_playbook_reverse_proxy_type: playbook-managed-traefik
matrix_playbook_ssl_enabled: true
traefik_config_entrypoint_web_secure_enabled: false        # TLS внутри off
traefik_container_web_host_bind_port: '0.0.0.0:81'
traefik_config_entrypoint_web_forwardedHeaders_trustedIPs: ['<TRAEFIK_IP>/32']
matrix_synapse_http_listener_resource_names: ["client","federation"]
matrix_federation_public_port: 443
matrix_synapse_federation_port_enabled: false
matrix_synapse_tls_federation_listener_enabled: false
```

## Xray-тулкит (опциональный обход блокировок)
`xray-manage` — хот-свап VLESS-апстрима без пересборки Matrix (Matrix завязан только на стабильный локальный порт прокси):
```
xray-manage sub '<url>'    # подписка (base64/plain)
xray-manage link 'vless://...'
xray-manage refresh | status | test | on | off
```
Конфиг генерится с **observatory + balancer** (авто-выбор живого сервера) и `fallbackTag: direct` (умерли все апстримы → выход напрямую). Роутинг **селективный**: локалка/RU → direct, домены из `proxy-domains.txt` → через VLESS, остальное → direct. Сервис гонится через прокси заданием `http_proxy=http://172.17.0.1:10809` его контейнеру.

## Обслуживание
- Обновление: `just update && just install-all` (читай `CHANGELOG.md` на breaking-changes).
- БД: `synapse-auto-compressor` (сжатие state-groups) + `postgres-backup` (логические дампы).
- Бэкап всего LXC — на уровне Proxmox (PBS) + ZFS; борг не обязателен.
- Логи: `journalctl -fu matrix-synapse` (и т.д.).

## Статус
✅ База, звонки, федерация, админки, гости, xray-тулкит, бэкап/компрессия.
🚧 Дописывается: мосты (Telegram/WhatsApp/Discord/Signal/Instagram/SMS), боты (baibot/maubot/…), English README, setup-скрипт.

---
Базируется на [matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy) (AGPL-3.0).
