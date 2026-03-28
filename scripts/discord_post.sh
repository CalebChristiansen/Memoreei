#!/usr/bin/env bash
# Post a message to #projects-live via Discord API
# Usage: discord_post.sh "message text"

CHANNEL_ID="REDACTED_CHANNEL_ID"
MSG="$1"

# Truncate to Discord's 2000 char limit
if [ ${#MSG} -gt 1900 ]; then
  MSG="${MSG:0:1900}...(truncated)"
fi

# Escape for JSON
MSG_JSON=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$MSG")

source ~/.config/bw/credentials 2>/dev/null
export BW_CLIENTID BW_CLIENTSECRET
MASTER=$(cat ~/.config/bw/master_password 2>/dev/null)
BW_SESSION=$(~/.local/bin/bw unlock "$MASTER" --raw 2>/dev/null)
TOKEN=$(~/.local/bin/bw get password "discord elliot" --session "$BW_SESSION" 2>/dev/null)

curl -s -X POST "https://discord.com/api/v10/channels/$CHANNEL_ID/messages" \
  -H "Authorization: Bot $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"content\": $MSG_JSON}" > /dev/null 2>&1
