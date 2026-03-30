#!/usr/bin/env bash
# Ralph Loop — autonomous task runner
# Spawns a fresh Claude Code session per task. Skips completed tasks on restart.
# Stops when: all tasks done, budget exhausted, deadline hit, or max failures reached.
#
# Required env (set by setup):
#   RALPH_PROJECT_DIR   — project root
#   RALPH_TASKS_FILE    — path to tasks.json
#   RALPH_LOG_DIR       — log directory
#   RALPH_LIVE_CHANNEL  — Discord channel ID for live output (optional)
#   RALPH_BOT_TOKEN     — Discord bot token for posting (optional)
#   RALPH_DEADLINE      — ISO timestamp deadline (optional, empty = no deadline)
#   RALPH_MAX_FAILURES  — consecutive failure limit (default: 3)
#   RALPH_CONTEXT_FILE  — file containing project context for task prompts (optional)

set -uo pipefail
trap 'echo "[$(date "+%H:%M:%S")] RUNNER CRASHED (line $LINENO, exit $?)" | tee -a "$RALPH_LOG_DIR/runner.log"' ERR

PROJECT_DIR="${RALPH_PROJECT_DIR:?RALPH_PROJECT_DIR not set}"
TASKS_FILE="${RALPH_TASKS_FILE:-$PROJECT_DIR/tasks.json}"
LOG_DIR="${RALPH_LOG_DIR:-$PROJECT_DIR/logs}"
LIVE_CHANNEL="${RALPH_LIVE_CHANNEL:-}"
BOT_TOKEN="${RALPH_BOT_TOKEN:-}"
DEADLINE="${RALPH_DEADLINE:-}"
MAX_CONSECUTIVE_FAILURES="${RALPH_MAX_FAILURES:-3}"
CONTEXT_FILE="${RALPH_CONTEXT_FILE:-}"

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

# ── helpers ──────────────────────────────────────────────────────────────────

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_DIR/runner.log"; }

discord_post() {
  [ -z "$LIVE_CHANNEL" ] || [ -z "$BOT_TOKEN" ] && return 0
  local msg="$1"
  [ ${#msg} -gt 1900 ] && msg="${msg:0:1900}...(truncated)"
  local msg_json
  msg_json=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$msg")
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "https://discord.com/api/v10/channels/$LIVE_CHANNEL/messages" \
    -H "Authorization: Bot $BOT_TOKEN" \
    -H "Content-Type: application/json" \
    -H "User-Agent: RalphLoop/1.0" \
    -d "{\"content\": $msg_json}")
  # Rate limit: if 429, sleep and retry once
  if [ "$code" = "429" ]; then
    sleep 5
    curl -s -X POST "https://discord.com/api/v10/channels/$LIVE_CHANNEL/messages" \
      -H "Authorization: Bot $BOT_TOKEN" \
      -H "Content-Type: application/json" \
      -H "User-Agent: RalphLoop/1.0" \
      -d "{\"content\": $msg_json}" > /dev/null 2>&1
  fi
}

past_deadline() {
  [ -z "$DEADLINE" ] && return 1
  local deadline_epoch now_epoch
  deadline_epoch=$(date -d "$DEADLINE" +%s 2>/dev/null || echo 9999999999)
  now_epoch=$(date +%s)
  [ "$now_epoch" -ge "$deadline_epoch" ]
}

task_count() { python3 -c "import json; print(len(json.load(open('$TASKS_FILE'))))"; }
task_field() { python3 -c "import json; t=json.load(open('$TASKS_FILE'))[$1]; print(t.get('$2',''))"; }

is_task_done() { [ -f "$LOG_DIR/${1}.done" ]; }
is_task_skipped() { [ -f "$LOG_DIR/${1}.skip" ]; }
mark_done() { date -Iseconds > "$LOG_DIR/${1}.done"; }

run_test() {
  local test_cmd="$1"
  [ -z "$test_cmd" ] && return 0
  bash -c "$test_cmd" >> "$LOG_DIR/tests.log" 2>&1
}

# ── main loop ────────────────────────────────────────────────────────────────

log "=== Ralph Loop starting ==="
log "Project: $PROJECT_DIR"
log "Tasks: $(task_count)"
[ -n "$DEADLINE" ] && log "Deadline: $DEADLINE" || log "Deadline: none"

# Load extra context if provided
extra_context=""
if [ -n "$CONTEXT_FILE" ] && [ -f "$CONTEXT_FILE" ]; then
  extra_context=$(cat "$CONTEXT_FILE")
  log "Loaded context from $CONTEXT_FILE"
fi

consecutive_failures=0
total=$(task_count)

for i in $(seq 0 $((total - 1))); do
  if past_deadline; then
    log "DEADLINE REACHED — stopping"
    discord_post "⏰ Ralph Loop deadline reached. Stopping."
    break
  fi

  task_id=$(task_field "$i" "id")
  task_name=$(task_field "$i" "name")
  task_prompt=$(task_field "$i" "prompt")
  task_test=$(task_field "$i" "test")
  task_retries=$(task_field "$i" "retries")
  task_retries=${task_retries:-2}

  # Skip completed or skipped tasks
  if is_task_done "$task_id"; then
    log "SKIP $task_id — already done"
    continue
  fi
  if is_task_skipped "$task_id"; then
    log "SKIP $task_id — agent marked skip"
    continue
  fi

  # Crash recovery guard
  if [ -f "$LOG_DIR/${task_id}.running" ]; then
    log "WARN $task_id — was running when runner last died. Retrying."
    discord_post "⚠️ **$task_name** — retrying after prior crash"
  fi
  date -Iseconds > "$LOG_DIR/${task_id}.running"

  log "START $task_id: $task_name"
  discord_post "🔨 **Task: $task_name** (starting)"

  success=false
  for attempt in $(seq 1 $((task_retries + 1))); do
    if past_deadline; then
      log "DEADLINE mid-task — stopping"
      break 2
    fi

    log "  attempt $attempt/$((task_retries + 1))"

    # Build prompt
    full_prompt="You are working on a project in $(pwd).
$extra_context

YOUR TASK: $task_prompt

VERIFICATION: After completing the task, run this test command to verify:
$task_test

If the test fails, fix the issue and try again. Do not move on until the test passes."

    discord_post "🤖 Claude Code running... (task: $task_id, attempt $attempt)"
    claude --permission-mode bypassPermissions --print "$full_prompt" \
      > "$LOG_DIR/${task_id}_attempt${attempt}.log" 2>&1 || true

    # Post tail to live channel
    tail_output=$(tail -20 "$LOG_DIR/${task_id}_attempt${attempt}.log" 2>/dev/null | head -20)
    [ -n "$tail_output" ] && discord_post "\`\`\`
${tail_output}
\`\`\`"

    if run_test "$task_test"; then
      log "  PASS ✅"
      mark_done "$task_id"
      rm -f "$LOG_DIR/${task_id}.running"
      success=true
      consecutive_failures=0
      break
    else
      log "  FAIL ❌ (attempt $attempt)"

      # On final retry, write escalation for heartbeat agent
      if [ "$attempt" -eq "$((task_retries + 1))" ]; then
        log "  ESCALATING to agent..."
        last_log=$(tail -80 "$LOG_DIR/${task_id}_attempt${attempt}.log" 2>/dev/null)
        test_output=$(bash -c "$task_test" 2>&1 || true)

        cat > "$LOG_DIR/${task_id}.escalate" <<ESCALATE
TASK FAILURE — needs agent diagnosis.

Task: $task_name (id: $task_id)
Test command: $task_test
Test output: $test_output

Last 80 lines of Claude Code output:
$last_log

Full log: $LOG_DIR/${task_id}_attempt${attempt}.log
Task definition: $TASKS_FILE (id: $task_id)

Fix the code directly. Re-run the test: $task_test
If fixed: touch $LOG_DIR/${task_id}.done
If unfixable: write reason to $LOG_DIR/${task_id}.skip
ESCALATE

        discord_post "🔧 **$task_name** failed — escalating to agent"

        # Wait for agent fix (up to 5 min)
        waited=0
        while [ $waited -lt 300 ]; do
          [ -f "$LOG_DIR/${task_id}.done" ] || [ -f "$LOG_DIR/${task_id}.skip" ] && break
          sleep 15
          waited=$((waited + 15))
          past_deadline && break 2
        done

        if [ -f "$LOG_DIR/${task_id}.done" ]; then
          log "  AGENT FIX ✅"
          success=true
          consecutive_failures=0
          rm -f "$LOG_DIR/${task_id}.escalate" "$LOG_DIR/${task_id}.running"
        elif [ -f "$LOG_DIR/${task_id}.skip" ]; then
          log "  AGENT SKIPPED — $(cat "$LOG_DIR/${task_id}.skip")"
          rm -f "$LOG_DIR/${task_id}.escalate" "$LOG_DIR/${task_id}.running"
        else
          log "  AGENT TIMEOUT — no fix in 5 minutes"
        fi
      fi
    fi
  done

  if [ "$success" = true ]; then
    log "DONE $task_id ✅"
    discord_post "✅ **$task_name** — passed"
  else
    log "FAILED $task_id ❌"
    discord_post "❌ **$task_name** — failed after all attempts"
    consecutive_failures=$((consecutive_failures + 1))

    if [ "$consecutive_failures" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
      log "TOO MANY CONSECUTIVE FAILURES ($consecutive_failures) — stopping"
      discord_post "🛑 Ralph Loop stopped: $MAX_CONSECUTIVE_FAILURES consecutive failures"
      break
    fi
  fi
done

# ── final status ─────────────────────────────────────────────────────────────

done_count=$(ls "$LOG_DIR"/*.done 2>/dev/null | wc -l)
log "=== Ralph Loop finished ==="
log "Completed: $done_count / $total tasks"

# Generate PROGRESS.md
{
  echo "# Build Progress"
  echo ""
  echo "Last updated: $(date '+%Y-%m-%d %H:%M %Z')"
  echo ""
  echo "| Task | Status |"
  echo "|------|--------|"
  for i in $(seq 0 $((total - 1))); do
    tid=$(task_field "$i" "id")
    tname=$(task_field "$i" "name")
    if is_task_done "$tid"; then
      echo "| $tname | ✅ Done |"
    elif is_task_skipped "$tid"; then
      echo "| $tname | ⏭️ Skipped |"
    else
      echo "| $tname | ❌ Not completed |"
    fi
  done
} > "$PROJECT_DIR/PROGRESS.md"

# Final commit
git add -A 2>/dev/null || true
git diff --cached --quiet 2>/dev/null || {
  git commit -m "chore: ralph loop progress update" 2>/dev/null || true
  git push 2>/dev/null || true
}

discord_post "🏁 Ralph Loop finished: $done_count/$total tasks complete"
log "Done."
