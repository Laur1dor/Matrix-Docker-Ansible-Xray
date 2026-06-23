#!/usr/bin/env bash
# ============================================================================
#  setup.sh — interactive generator for your Matrix inventory vars.yml.
#  Asks what you want, explains where to get each value, generates all random
#  secrets, and writes a ready-to-use vars.yml. Then copy it into your
#  matrix-docker-ansible-deploy clone and run `just install-all`.
#
#  Nothing here is hardcoded to any specific domain/IP — you provide everything.
# ============================================================================
set -uo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$SELF_DIR/vars.yml}"
command -v openssl >/dev/null || { echo "Нужен openssl."; exit 1; }

say()  { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
ask()  { local p="$1" d="${2:-}" v; read -r -p "$p${d:+ [$d]}: " v; echo "${v:-$d}"; }
yesno(){ local p="$1" d="${2:-y}" v; read -r -p "$p ($([ "$d" = y ] && echo 'Y/n' || echo 'y/N')): " v; v="${v:-$d}"; [[ "$v" =~ ^[yYдД] ]]; }
rnd()  { openssl rand -hex "${1:-32}"; }
pw()   { openssl rand -base64 30 | tr -d '/+=' | cut -c1-28; }

cat <<'INTRO'
============================================================
  Настройка вашего Matrix-сервера. Я задам несколько вопросов,
  объясню что откуда взять, и соберу vars.yml. Случайные секреты
  (пароли БД, ключи) сгенерирую сам. Enter = значение по умолчанию.
============================================================
INTRO

# ---------- базовое ----------
DOMAIN=$(ask "Ваш домен (адреса будут @user:домен)" "example.com")
say "Имена поддоменов (можно оставить дефолтные):"
SD_MATRIX=$(ask "  поддомен homeserver" "matrix")
SD_ELEMENT=$(ask "  поддомен веб-клиента Element" "element")
SD_ADMIN=$(ask "  поддомен админ-панели" "admin")
SD_CALL=$(ask "  поддомен звонков (Element Call)" "call")
SD_PUSH=$(ask "  поддомен push-уведомлений (ntfy)" "push")

say "Как стоит фронт (TLS)?  A=встроенный Traefik (один сервер, сам берёт Let's Encrypt)"
echo "                         B=за вашим reverse-proxy (Traefik/nginx/NPM, TLS на нём)"
DEPLOY=$(ask "Вариант (A/B)" "A"); DEPLOY="${DEPLOY^^}"
PROXY_IP=""
if [ "$DEPLOY" = "B" ]; then
  PROXY_IP=$(ask "  IP/хост вашего reverse-proxy (он будет ходить на этот сервер)")
fi
PUBLIC_IP=$(ask "Публичный IP сервера (для звонков/LiveKit)")

# ---------- фичи ----------
say "Какие опциональные фичи включить?"
EN_AI=n; EN_MAUBOT=n; EN_REMINDER=n; EN_DRAUPNIR=n; EN_HONOROIT=n
EN_TG=n; EN_WA=n; EN_SIG=n; EN_DIS=n; EN_IG=n; EN_SMS=n
yesno "  AI-ассистент (baibot, понимает фото)" y && EN_AI=y
yesno "  Переводчик + гифки (maubot)" y && EN_MAUBOT=y
yesno "  Напоминания" y && EN_REMINDER=y
yesno "  Модерация (Draupnir)" n && EN_DRAUPNIR=y
yesno "  Хелпдеск (Honoroit)" n && EN_HONOROIT=y
say "  Мосты (привязка своих мессенджеров):"
yesno "    Telegram" n && EN_TG=y
yesno "    WhatsApp" n && EN_WA=y
yesno "    Signal"   n && EN_SIG=y
yesno "    Discord"  n && EN_DIS=y
yesno "    Instagram" n && EN_IG=y
yesno "    Android-SMS" n && EN_SMS=y

# ---------- ключи (только для включённого) ----------
OR_KEY=""; GROQ_KEY=""; TG_ID=""; TG_HASH=""
if [ "$EN_AI" = y ]; then
  say "AI-бот: нужен бесплатный ключ. OpenRouter: https://openrouter.ai/keys (рекоменд.)"
  OR_KEY=$(ask "  Ключ OpenRouter (sk-or-...)")
  echo "  (Опц.) Groq даёт щедрые лимиты: https://console.groq.com — можно оставить пустым."
  GROQ_KEY=$(ask "  Ключ Groq (gsk_..., можно пусто)")
fi
if [ "$EN_TG" = y ]; then
  say "Telegram-мост: api_id/api_hash с https://my.telegram.org → API development tools"
  TG_ID=$(ask "  api_id (число)")
  TG_HASH=$(ask "  api_hash")
fi

# ---------- генерируем vars.yml ----------
say "Генерирую секреты и собираю $OUT ..."
{
cat <<YAML
---
# Сгенерировано setup.sh. НЕ коммить этот файл — тут реальные секреты!
matrix_playbook_migration_validated_version: "{{ matrix_playbook_migration_expected_version }}"

# ----- домены -----
matrix_domain: ${DOMAIN}
matrix_server_fqn_matrix: ${SD_MATRIX}.${DOMAIN}
matrix_server_fqn_element: ${SD_ELEMENT}.${DOMAIN}
matrix_homeserver_implementation: synapse
matrix_well_known_matrix_client_enabled: true
matrix_static_files_file_matrix_server_enabled: true

# ----- ядро -----
matrix_homeserver_generic_secret_key: '$(rnd 32)'
postgres_connection_password: '$(rnd 32)'
matrix_synapse_max_upload_size_mb: 2048
matrix_synapse_enable_registration: false
matrix_synapse_user_directory_search_all_users: true
# личные комнаты создаются зашифрованными автоматически
matrix_synapse_encryption_enabled_by_default_for_room_type: invite
# удалённые комнаты быстро вычищаются (не висят "Deletion in progress")
matrix_synapse_configuration_extension_yaml: |
  forgotten_room_retention_period: 1h

# ----- авторизация (MAS): регистрация по токену-приглашению -----
matrix_authentication_service_enabled: true
matrix_authentication_service_config_secrets_encryption: '$(rnd 32)'
matrix_authentication_service_config_account_password_registration_enabled: true
matrix_authentication_service_config_account_registration_token_required: true
matrix_authentication_service_config_account_password_registration_email_required: false

# ----- клиенты / админка / push -----
matrix_client_element_enabled: true
matrix_element_admin_enabled: true
matrix_element_admin_hostname: ${SD_ADMIN}.${DOMAIN}
matrix_ketesa_enabled: true
ntfy_enabled: true
ntfy_hostname: ${SD_PUSH}.${DOMAIN}

# ----- звонки -----
matrix_rtc_enabled: true
matrix_element_call_enabled: true
matrix_element_call_hostname: ${SD_CALL}.${DOMAIN}
coturn_enabled: true
coturn_turn_external_ip_address: "${PUBLIC_IP}"
coturn_turn_static_auth_secret: "$(rnd 32)"
livekit_server_config_rtc_node_ip: "${PUBLIC_IP}"
livekit_server_configuration_extension_yaml: |
  room:
    max_participants: 10

# ----- БД: бэкап + компрессия -----
matrix_synapse_auto_compressor_enabled: true
postgres_backup_enabled: true
YAML

# фронт
if [ "$DEPLOY" = "B" ]; then
cat <<YAML

# ----- фронт: за внешним reverse-proxy (TLS на нём) -----
matrix_playbook_reverse_proxy_type: playbook-managed-traefik
matrix_playbook_ssl_enabled: true
traefik_config_entrypoint_web_secure_enabled: false
traefik_container_web_host_bind_port: '0.0.0.0:81'
traefik_config_entrypoint_web_forwardedHeaders_trustedIPs: ['${PROXY_IP}/32']
matrix_synapse_http_listener_resource_names: ["client","federation"]
matrix_federation_public_port: 443
matrix_synapse_federation_port_enabled: false
matrix_synapse_tls_federation_listener_enabled: false
YAML
fi

# AI
if [ "$EN_AI" = y ]; then
cat <<YAML

# ----- AI-ассистент (baibot). Рекомендуется ставить локальный шлюз llm-gateway.py (см. docs/EXTRAS.md). -----
matrix_bot_baibot_enabled: true
matrix_admin: "@admin:${DOMAIN}"
matrix_bot_baibot_config_user_password: "$(pw)"
matrix_bot_baibot_config_user_encryption_recovery_passphrase: "$(rnd 24)"
matrix_bot_baibot_config_persistence_session_encryption_key: "$(rnd 32)"
matrix_bot_baibot_config_persistence_config_encryption_key: "$(rnd 32)"
matrix_bot_baibot_config_user_name: "Помощник"
matrix_bot_baibot_config_agents_static_definitions_custom:
  - id: openrouter
    provider: openai
    config:
      base_url: https://openrouter.ai/api/v1   # или http://GATEWAY:8765/v1 при использовании llm-gateway.py
      api_key: "${OR_KEY}"
      text_generation:
        model_id: "google/gemma-4-31b-it:free"
        prompt: "Ты — дружелюбный универсальный ассистент. Отвечай по-русски, понимаешь фото."
matrix_bot_baibot_config_initial_global_config_handler_catch_all: static/openrouter
YAML
fi

[ "$EN_MAUBOT" = y ] && cat <<YAML

matrix_bot_maubot_enabled: true
matrix_bot_maubot_initial_password: "$(pw)"
matrix_bot_maubot_database_password: "$(rnd 20)"
matrix_bot_maubot_admins:
  admin: "$(rnd 16)"
YAML

[ "$EN_REMINDER" = y ] && cat <<YAML

matrix_bot_matrix_reminder_bot_enabled: true
matrix_bot_matrix_reminder_bot_database_password: "$(rnd 20)"
matrix_bot_matrix_reminder_bot_matrix_user_password: "$(pw)"
matrix_bot_matrix_reminder_bot_reminders_timezone: "Europe/Moscow"
YAML

[ "$EN_HONOROIT" = y ] && cat <<YAML

matrix_bot_honoroit_enabled: true
matrix_bot_honoroit_database_password: "$(rnd 20)"
matrix_bot_honoroit_password: "$(pw)"
matrix_bot_honoroit_no_encryption_warning: true
# matrix_bot_honoroit_roomid: "!ВАША_КОМНАТА:${DOMAIN}"   # создайте комнату, пригласите @honoroit, впишите id
YAML

[ "$EN_DRAUPNIR" = y ] && cat <<YAML

matrix_bot_draupnir_enabled: true
matrix_bot_draupnir_zero_touch_deploy: true
matrix_bot_draupnir_config_initialManager: "@admin:${DOMAIN}"
matrix_bot_draupnir_login_native: true
matrix_bot_draupnir_password: "$(pw)"
YAML

# мосты + общий E2EE для мостов
if [ "$EN_TG$EN_WA$EN_SIG$EN_DIS$EN_IG$EN_SMS" != "nnnnnn" ]; then
cat <<YAML

# ----- мосты (E2EE включён; права — только локальные юзеры) -----
matrix_bridges_encryption_enabled: true
matrix_bridges_encryption_default: true
YAML
[ "$EN_TG" = y ]  && printf 'matrix_mautrix_telegram_enabled: true\nmatrix_mautrix_telegram_api_id: %s\nmatrix_mautrix_telegram_api_hash: "%s"\nmatrix_mautrix_telegram_bridge_permissions:\n  "@admin:%s": admin\n  %s: user\n' "${TG_ID:-0}" "$TG_HASH" "$DOMAIN" "$DOMAIN"
for pair in "WA:whatsapp:$EN_WA" "SIG:signal:$EN_SIG" "DIS:discord:$EN_DIS" "IG:meta_instagram:$EN_IG" "SMS:gmessages:$EN_SMS"; do
  IFS=: read -r _ name en <<<"$pair"
  [ "$en" = y ] && printf 'matrix_mautrix_%s_enabled: true\nmatrix_mautrix_%s_bridge_permissions:\n  "@admin:%s": admin\n  %s: user\n' "$name" "$name" "$DOMAIN" "$DOMAIN"
done
fi

cat <<YAML

# новые пользователи авто-вступают в приветственную комнату
matrix_synapse_auto_join_rooms:
  - "#announcements:${DOMAIN}"
YAML
} > "$OUT"

say "Готово! Файл: $OUT"
cat <<NEXT

Дальше:
  1. Скопируй его в плейбук: inventory/host_vars/${SD_MATRIX}.${DOMAIN}/vars.yml
  2. DNS: апекс ${DOMAIN} → A на ${PUBLIC_IP:-IP}; поддомены ${SD_MATRIX}/${SD_ELEMENT}/${SD_ADMIN}/${SD_CALL}/${SD_PUSH} → CNAME на апекс.
  3. just install-all
  4. just register-user admin <пароль> yes
  5. Приглашения новым: на сервере  docker exec matrix-authentication-service mas-cli manage issue-user-registration-token
$([ "$DEPLOY" = B ] && echo "  6. На внешнем прокси: маршрут *.${DOMAIN} → http://ЭТОТ_СЕРВЕР:81 (см. examples/)")
  • Боты-плагины (переводчик/гифки/приветствие) и AI-шлюз — см. docs/EXTRAS.md
  • НЕ коммить $OUT в git (там секреты).
NEXT
