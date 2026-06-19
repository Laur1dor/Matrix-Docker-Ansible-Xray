# Matrix-Docker-Ansible-Xray

Self-hosted **Matrix (Synapse)** на основе [spantaleev/matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy), адаптированный под типовой **домашний Proxmox-стенд**: сервис живёт в LXC, наружу торчит **только роутер (главный фаервол)**, TLS терминирует **внешний Traefik** на отдельной машине, а исходящий трафик при блокировках идёт через **Xray (VLESS/REALITY)** с хот-свапом и фейловером.

Сверху — полный «бытовой» набор: **мессенджер + звонки/видео, гости-конференции, AI-ассистент с распознаванием фото, переводчик, напоминания, модерация, хелпдеск и 6 мостов** (Telegram/WhatsApp/Discord/Signal/Instagram/SMS) с самостоятельным подключением.

> 🇷🇺 Это основной README (обзор и установка). 
> 📖 **Что умеет и как пользоваться (простым языком):** [`docs/FUNCTIONALITY.ru.md`](docs/FUNCTIONALITY.ru.md)
> 🇬🇧 English overview: [`README.en.md`](README.en.md)
> Рассчитано на homelab/devops: без воды, но с объяснением «что/зачем/почему».

> ⚠️ **Секреты в git не коммитим.** В репозитории — только [`examples/vars.sample.yml`](examples/vars.sample.yml) с плейсхолдерами. Реальный `vars.yml` (пароли, ключи, токены) заблокирован в [`.gitignore`](.gitignore). Подробнее — раздел [Безопасность](#безопасность).

## Что внутри
**Ядро:** Synapse · MAS (новая авторизация, регистрация по токену) · Element Web · Element Admin (Ketesa/synapse-admin) · ntfy (push) · well-known делегация (user-id вида `@you:domain`).
**Звонки:** Element Call + LiveKit (1-1, группы до 10, шара экрана, **гости по ссылке**) · coturn (легаси-TURN).
**Боты:** **baibot** (AI, vision, RU по умолчанию, E2EE) · **maubot + переводчик** (`!tr`) · **reminder-bot** · **registration-bot** · **Draupnir** (модерация) · **Honoroit** (хелпдеск).
**Мосты (самоподключение, только для своих):** Telegram · WhatsApp · Signal · Discord · Instagram · Android-SMS.
**Инфраструктура:** **Xray-тулкит** (`xray-manage`, хот-свап без пересборки) · **livekit-ip-watcher** (авто-обновление белого IP) · автокомпрессия БД · логические бэкапы Postgres · быстрая очистка удалённых комнат.

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
        │  CF DNS) │  │  LiveKit · coturn · ntfy · боты  │
        └──────────┘  │  мосты · Xray (selective egress) │
                      └──────────────────────────────────┘
```

- **Внешний Traefik (.105)** держит сертификаты (Cloudflare DNS-01, wildcard) и проксирует все matrix-хосты на **один** бэкенд `http://<LXC>:81` (Host-роутинг). Пример: [`examples/traefik-matrix.yaml`](examples/traefik-matrix.yaml).
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
| 5350 | TCP | .106 | TURN/TLS |
| 30000-30020 | UDP | .106 | TURN relay range |
| 3478, 49152-49172 | TCP/UDP | .106 | coturn (легаси-звонки) |

## Быстрый старт
На .106 (Debian 12, ≥6 ГБ RAM, Docker-in-LXC требует `nesting=1`):

1. **Зависимости:** `git`, **`sudo`** (в минимальном Debian нет — обязателен для ansible `become`), Docker (`get.docker.com`), **Ansible через pipx** (репозиторный 2.14 слишком стар, нужен ≥2.15.1), `just`.
2. `git clone` апстрим-плейбука → `just roles`.
3. **Сгенерировать `vars.yml` под свой домен** (заполнит все случайные секреты автоматически):
   ```bash
   ./setup.sh --domain example.com --public-ip 1.2.3.4 --traefik-ip 192.168.1.105
   ```
   Останется вписать только API-ключи (Groq/OpenRouter), Telegram `api_id/api_hash`, пароль веб-панели maubot и id комнаты Honoroit — скрипт перечислит их в конце.
4. Скопировать готовый `vars.yml` в `inventory/host_vars/<domain>/vars.yml`, поднять `inventory/hosts` (local connection).
5. `just install-all` → зарегистрировать админа (`just register-user admin <pass> yes`).
6. На внешнем Traefik подложить [`examples/traefik-matrix.yaml`](examples/traefik-matrix.yaml).

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
Конфиг генерится с **observatory + balancer** (авто-выбор живого сервера) и `fallbackTag: direct` (умерли все апстримы → выход напрямую, сервер остаётся в сети). Роутинг **селективный**: локалка/RU → direct, домены из `proxy-domains.txt` → через VLESS, остальное → direct (zapret уже разбирается с общим DPI). Сервис гонится через прокси заданием `http_proxy=http://172.17.0.1:10809` его контейнеру. Мосты WhatsApp/Signal/Telegram ходят через socks (`172.17.0.1:10808`).

## Безопасность
- **Реальный `vars.yml` НИКОГДА не коммитим** — там пароли, ключи шифрования, API-ключи, Telegram-креды. [`.gitignore`](.gitignore) блокирует `vars.yml`, `*.bak`, `.secrets/`, ключи, xray-ссылки и подписки. В git едет только `examples/vars.sample.yml` (плейсхолдеры).
- Регистрация **закрыта**: только по токену (админ выдаёт из панели MAS) — спам/чужие аккаунты исключены.
- **Мосты — только для локальных юзеров.** В правах мостов нет правила `*`; чужой homeserver не сможет привязать свой Telegram/WhatsApp. Инвайты и сообщения от чужих вашим юзерам — работают штатно (это федерация).
- **Личные диалоги шифруются по умолчанию** (E2EE). baibot и переводчик (maubot) тоже работают в зашифрованных комнатах.

## Обслуживание
- Обновление: `just update && just install-all` (читай `CHANGELOG.md` апстрима на breaking-changes).
- БД: `synapse-auto-compressor` (сжатие state-groups) + `postgres-backup` (логические дампы; Proxmox PBS затем забирает их).
- Бэкап всего LXC — на уровне Proxmox (PBS) + ZFS.
- Логи: журналд по контейнерам, напр. `journalctl -u matrix-synapse.service -f`.
- Удалённые комнаты вычищаются из БД за ~1 час (а не за 28 дней) — в админке не висит «Deletion in progress».

## Статус
✅ **Готово и работает:** ядро, авторизация (MAS), звонки/видео + гости, федерация на 443, админки, поиск по всем юзерам, E2EE-диалоги, AI-бот с распознаванием фото, переводчик, напоминания, модерация (Draupnir), хелпдеск (Honoroit), приветственная комната, 6 мостов с самоподключением (только для своих), Xray-тулкит, авто-IP для LiveKit, бэкап/компрессия, быстрая очистка комнат.

---
Базируется на [matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy) (AGPL-3.0).
