# TURN TLS за внешним reverse proxy

Если HTTP/HTTPS завершается на отдельном reverse proxy, это само по себе
не даёт сертификат TURN endpoint. При прямом пробросе TURN TLS порта на
Matrix сервер его внутренний Traefik должен выдавать доверенный сертификат
для `livekit_server_config_turn_domain`. Иначе часть клиентов будет работать
по UDP, а TLS fallback у клиентов за ограниченным NAT не подключится.

Для Cloudflare DNS-01 добавьте в **приватный** inventory vars:

```yaml
traefik_config_certificatesResolvers_acme_enabled: true
traefik_config_certificatesResolvers_acme_name: cloudflare
traefik_config_certificatesResolvers_acme_dnsChallenge_enabled: true
traefik_config_certificatesResolvers_acme_dnsChallenge_provider: cloudflare
traefik_config_certificatesResolvers_acme_httpChallenge_enabled: false
traefik_config_certificatesResolvers_acme_dnsChallenge_resolvers: ['1.1.1.1:53', '1.0.0.1:53']
livekit_server_container_labels_turn_traefik_tls_certResolver: cloudflare
traefik_environment_variables: |
  CF_DNS_API_TOKEN=REPLACE_WITH_PRIVATE_DNS_TOKEN
```

Token должен иметь `Zone:Read` и `DNS:Edit` для нужной зоны. Не коммитьте
реальный token. ACME state хранится в SSL каталоге Traefik; сохраняйте его
при повторной установке, чтобы не выпускать новый сертификат на каждом запуске.

TURN использует ALPN `stun.turn` и `stun.nat-discovery`. В актуальном upstream
playbook выделенные TLS options настроены автоматически. Для старых ролей,
которые ещё не поддерживают их, возможен совместимый override:

```yaml
traefik_provider_configuration_extension_yaml: |
  tls:
    options:
      livekit-turn:
        alpnProtocols: ['stun.turn', 'stun.nat-discovery', 'h2', 'http/1.1', 'acme-tls/1']
livekit_server_container_labels_additional_labels_custom:
  - 'traefik.tcp.routers.matrix-livekit-server-turn.tls.options=livekit-turn@file'
```

Если эти переменные уже используются, объедините содержимое: не создавайте
дубликаты YAML keys и не заменяйте имеющиеся дополнительные labels.

Проверяйте фактический TURN hostname/port из конфигурации. Например:

```sh
openssl s_client -connect matrix.example.com:5350 -servername matrix.example.com -alpn stun.turn -verify_return_error -verify_hostname matrix.example.com </dev/null
```

Должны проходить проверка цепочки/hostname и ALPN. Отдельно проверьте звонок
между двумя устройствами, одно из которых использует мобильную сеть:
TLS handshake не подтверждает полный аудиотракт.

Источники: [Lego Cloudflare provider](https://go-acme.github.io/lego/dns/cloudflare/),
[лимиты Let's Encrypt](https://letsencrypt.org/docs/rate-limits/).
