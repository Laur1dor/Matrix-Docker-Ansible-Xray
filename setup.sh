#!/usr/bin/env bash
# ============================================================================
#  setup.sh — turn examples/vars.sample.yml into a ready-to-edit vars.yml for
#  YOUR domain, auto-generating every random secret. API keys / login passwords
#  that a human must supply are left as CHANGEME_ and listed at the end.
#
#  Usage:
#    ./setup.sh --domain example.com --public-ip 1.2.3.4 --traefik-ip 192.168.1.105 \
#               [--out /path/to/vars.yml]
#
#  Then: review the output, fill the remaining CHANGEME_ values, copy it to
#    inventory/host_vars/<domain>/vars.yml  in your matrix-docker-ansible-deploy clone.
# ============================================================================
set -euo pipefail

DOMAIN="" PUBLIC_IP="" TRAEFIK_IP="" OUT=""
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMPLE="$SELF_DIR/examples/vars.sample.yml"

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)     DOMAIN="$2"; shift 2;;
    --public-ip)  PUBLIC_IP="$2"; shift 2;;
    --traefik-ip) TRAEFIK_IP="$2"; shift 2;;
    --out)        OUT="$2"; shift 2;;
    -h|--help)    grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[ -n "$DOMAIN" ]     || { echo "error: --domain is required" >&2; exit 2; }
[ -n "$PUBLIC_IP" ]  || { echo "error: --public-ip is required" >&2; exit 2; }
[ -n "$TRAEFIK_IP" ] || { echo "error: --traefik-ip is required" >&2; exit 2; }
[ -f "$SAMPLE" ]     || { echo "error: $SAMPLE not found" >&2; exit 2; }
command -v openssl >/dev/null || { echo "error: openssl required" >&2; exit 2; }
OUT="${OUT:-$SELF_DIR/vars.yml}"

if [ -e "$OUT" ]; then
  read -r -p "$OUT exists. Overwrite? [y/N] " a; [ "$a" = "y" ] || { echo aborted; exit 1; }
fi

echo ">> domain=$DOMAIN  public-ip=$PUBLIC_IP  traefik-ip=$TRAEFIK_IP"
echo ">> generating $OUT ..."

# Process line by line so every rand-secret placeholder gets a UNIQUE value.
: > "$OUT"
while IFS= read -r line || [ -n "$line" ]; do
  # placeholders -> fresh secrets (per occurrence)
  while [[ "$line" == *CHANGEME_openssl_rand_hex_32* ]]; do
    line="${line/CHANGEME_openssl_rand_hex_32/$(openssl rand -hex 32)}"; done
  while [[ "$line" == *CHANGEME_openssl_rand_hex_24* ]]; do
    line="${line/CHANGEME_openssl_rand_hex_24/$(openssl rand -hex 24)}"; done
  while [[ "$line" == *CHANGEME_openssl_rand_hex_20* ]]; do
    line="${line/CHANGEME_openssl_rand_hex_20/$(openssl rand -hex 20)}"; done
  while [[ "$line" == *CHANGEME_strong_password* ]]; do
    line="${line/CHANGEME_strong_password/$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-28)}"; done
  printf '%s\n' "$line" >> "$OUT"
done < "$SAMPLE"

# domain / IPs
sed -i \
  -e "s/example\.com/${DOMAIN}/g" \
  -e "s/YOUR_PUBLIC_IP/${PUBLIC_IP}/g" \
  -e "s/TRAEFIK_LAN_IP/${TRAEFIK_IP}/g" \
  "$OUT"

echo ">> done."
echo
echo "Remaining values you MUST fill in $OUT (API keys / IDs / a created room):"
grep -nE 'CHANGEME_|!CHANGEME_|^matrix_mautrix_telegram_api_id: "0000000"' "$OUT" || echo "  (none — all auto-filled)"
echo
echo "Notes:"
echo "  - Landing page HTML lives in vars.yml (matrix_static_files_file_index_html_template);"
echo "    links were domain-swapped automatically. Edit the branding text there if you like."
echo "  - Telegram api_id/api_hash: get from https://my.telegram.org"
echo "  - Groq key: https://console.groq.com   OpenRouter key: https://openrouter.ai/keys"
echo "  - honoroit_roomid: create a private room as admin, invite @honoroit:${DOMAIN}, paste its id."
echo "  - NEVER commit this vars.yml (the .gitignore already blocks it)."
