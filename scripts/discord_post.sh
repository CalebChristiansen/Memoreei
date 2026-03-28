#!/usr/bin/env bash
# Post a message to #project-live via Discord API
# Usage: discord_post.sh "message text"
# Token is cached to avoid repeated Bitwarden unlocks.

CHANNEL_ID="REDACTED_CHANNEL_ID"
MSG="$1"
TOKEN_CACHE="/tmp/.memoreei_elliot_token"

# Truncate to Discord's 2000 char limit
if [ ${#MSG} -gt 1900 ]; then
  MSG="${MSG:0:1900}...(truncated)"
fi

# Escape for JSON
MSG_JSON=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$MSG")

# Use cached token if available and valid (< 1 hour old)
get_token() {
  if [ -f "$TOKEN_CACHE" ] && [ "$(( $(date +%s) - $(stat -c %Y "$TOKEN_CACHE" 2>/dev/null || echo 0) ))" -lt 3600 ]; then
    cat "$TOKEN_CACHE"
    return
  fi
  source ~/.config/bw/credentials 2>/dev/null
  export BW_CLIENTID BW_CLIENTSECRET
  MASTER=$(cat ~/.config/bw/master_password 2>/dev/null)
  BW_SESSION=$(~/.local/bin/bw unlock "$MASTER" --raw 2>/dev/null)
  local tok
  tok=$(~/.local/bin/bw get password "discord elliot" --session "$BW_SESSION" 2>/dev/null)
  if [ -n "$tok" ]; then
    echo "$tok" > "$TOKEN_CACHE"
    chmod 600 "$TOKEN_CACHE"
  fi
  echo "$tok"
}

TOKEN=$(get_token)

RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "https://discord.com/api/v10/channels/$CHANNEL_ID/messages" \
  -H "Authorization: Bot $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"content\": $MSG_JSON}")

# If 401 (bad token), invalidate cache and retry once
if [ "$RESPONSE" = "401" ]; then
  rm -f "$TOKEN_CACHE"
  TOKEN=$(get_token)
  curl -s -X POST "https://discord.com/api/v10/channels/$CHANNEL_ID/messages" \
    -H "Authorization: Bot $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"content\": $MSG_JSON}" > /dev/null 2>&1
fi
