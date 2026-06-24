# Доустановка extras (боты-плагины, AI-шлюз, авто-обновления)

Эти штуки ставятся поверх базового плейбука вручную (они вне него).

## maubot-плагины (переводчик-по-реакции, гифки, приветствие новичков)
1. Включите maubot в vars (`matrix_bot_maubot_enabled: true`), `just install-all`.
2. Откройте веб-панель maubot: `https://matrix.ВАШ_ДОМЕН/_matrix/maubot/` (логин из `matrix_bot_maubot_admins`).
3. Создайте **client** (matrix-аккаунт бота + access token).
4. Соберите .mbp каждого плагина из `maubot-plugins/<name>/` командой `mbc build` (или загрузите папку через панель) и
   залейте через панель (или API `POST /_matrix/maubot/v1/plugins/upload`).
5. Создайте **instance** для каждого плагина на этом client. Для giphy укажите в конфиге `api_key` (ключ Giphy).
6. greeter: впишите id вашей приветственной комнаты в конфиг и **добавьте бота в эту комнату** (иначе он не видит входы).
   Плагины-реакции/перевод/гифки работают только в комнатах, куда приглашён бот.

## AI-шлюз (llm-gateway.py) — бесплатные модели с автопереключением
1. Положите `llm-gateway.py` в `/usr/local/bin/`, создайте файлы ключей в `/root/.secrets/openrouter_key`
   (и опц. `/root/.secrets/groq_key`).
2. systemd-сервис, слушающий 0.0.0.0:8765 (пример внутри скрипта в шапке).
3. В vars baibot укажите `base_url: http://GATEWAY_HOST:8765/v1`. Шлюз сам выбирает живую бесплатную модель, для фото
   использует vision-модель, при исчерпании лимитов отдаёт понятное сообщение.

## Авто-уведомления об обновлениях (check-updates.py)
1. Положите `check-updates.py` в `/usr/local/bin/`, создайте `/root/.secrets/alerts_room` с id комнаты для уведомлений
   и `/root/.secrets/admin_token` (admin-scoped токен).
2. systemd timer (раз в неделю). Скрипт проверяет апстрим-репозиторий, спрашивает у AI-шлюза оценку рисков и постит
   уведомление с changelog + вердиктом в комнату уведомлений.

## Вынос медиа в S3 (Garage) — освободить локальный диск
[Garage](https://garagehq.deuxfleurs.fr/) — лёгкое S3-совместимое объектное хранилище для self-host. Synapse пишет медиа
локально + в S3, старое периодически переносится в S3 и локальные копии чистятся (плейбук ставит `...-migrate.timer`).

1. **Поднимите Garage** (на отдельной машине/контейнере; данные — на надёжном диске/пуле). Минимальный `docker-compose.yml`:
   ```yaml
   services:
     garage:
       image: dxflrs/garage:v2.3.0        # пиньте конкретный тег
       restart: unless-stopped
       volumes:
         - ./garage.toml:/etc/garage.toml:ro
         - /srv/garage/meta:/var/lib/garage/meta
         - /srv/garage/data:/var/lib/garage/data
       ports: ["3900:3900"]               # S3 API (admin 3903/RPC 3901 держите внутри)
   ```
   Минимальный `garage.toml`: `metadata_dir`/`data_dir`, `db_engine="lmdb"`, `replication_factor=1`,
   `rpc_secret` (`openssl rand -hex 32`), секции `[s3_api]` (`s3_region`, `api_bind_addr=[::]:3900`) и `[admin]`
   (`admin_token`). Полный референс параметров — в доке Garage.
2. **Инициализация:**
   ```bash
   docker exec garage /garage status                                  # узнать node id
   docker exec garage /garage layout assign -z dc1 -c 100G <node-id>
   docker exec garage /garage layout apply --version 1
   docker exec garage /garage bucket create matrix-media
   docker exec garage /garage key create matrix-key
   docker exec garage /garage bucket allow --read --write matrix-media --key matrix-key
   docker exec garage /garage key info matrix-key --show-secret        # Key ID + Secret -> в vars
   ```
3. **Включите в `vars.yml`** блок `matrix_synapse_ext_synapse_s3_storage_provider_*` (см. закомментированный шаблон в
   `examples/vars.sample.yml`): `endpoint_url` — **прямой** адрес Garage (даёт path-style), `region_name` = `s3_region`
   из `garage.toml`, bucket/ключи из шага 2. Затем `just install-all` (или `just run-tags setup-synapse,start`).
4. **Перенесите существующее медиа:** `/matrix/synapse/ext/s3-storage-provider/bin/migrate` (дальше — авто по таймеру).
   - **Не** выставляйте S3-эндпоинт в интернет без нужды: локальные клиенты ходят по LAN; квоты на бакет
     (`garage bucket set-quotas`) распределяют место между сервисами; веб-морду (`khairul169/garage-webui`) закрывайте паролем.
