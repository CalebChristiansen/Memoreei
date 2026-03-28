#!/usr/bin/env bash
# Memoreei Ralph Loop — autonomous task runner
# Each task gets a fresh Claude Code call with clean context.
# Stops when: all tasks done, deadline hit, or max failures reached.

set -euo pipefail

PROJECT_DIR="/home/fi/.openclaw/workspace-elliot/projects/memoreei"
TASKS_FILE="$PROJECT_DIR/tasks.json"
PROGRESS_FILE="$PROJECT_DIR/PROGRESS.md"
LOG_DIR="$PROJECT_DIR/logs"
DEADLINE="2026-03-28T15:25:00"  # 3:25 PM PDT
MAX_CONSECUTIVE_FAILURES=3
NOTIFY_CMD="openclaw system event --text"

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

# --- helpers ---

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_DIR/runner.log"; }

past_deadline() {
  local now deadline_epoch now_epoch
  deadline_epoch=$(date -d "$DEADLINE" +%s 2>/dev/null || echo 9999999999)
  now_epoch=$(date +%s)
  [ "$now_epoch" -ge "$deadline_epoch" ]
}

notify() {
  $NOTIFY_CMD "$1" --mode now 2>/dev/null || true
}

task_count() { python3 -c "import json; print(len(json.load(open('$TASKS_FILE'))))"; }
task_field() { python3 -c "import json; t=json.load(open('$TASKS_FILE'))[$1]; print(t.get('$2',''))"; }

is_task_done() {
  local task_id="$1"
  [ -f "$LOG_DIR/${task_id}.done" ]
}

mark_done() {
  local task_id="$1"
  echo "$(date -Iseconds)" > "$LOG_DIR/${task_id}.done"
}

run_test() {
  local test_cmd="$1"
  if [ -z "$test_cmd" ]; then
    return 0
  fi
  bash -c "$test_cmd" >> "$LOG_DIR/tests.log" 2>&1
}

# --- main loop ---

log "=== Ralph Loop starting ==="
log "Tasks: $(task_count)"
log "Deadline: $DEADLINE"

consecutive_failures=0
total=$(task_count)

for i in $(seq 0 $((total - 1))); do
  # Check deadline before each task
  if past_deadline; then
    log "DEADLINE REACHED — stopping"
    notify "⏰ Memoreei build deadline reached. Stopping task runner."
    break
  fi

  task_id=$(task_field "$i" "id")
  task_name=$(task_field "$i" "name")
  task_prompt=$(task_field "$i" "prompt")
  task_test=$(task_field "$i" "test")
  task_retries=$(task_field "$i" "retries")
  task_retries=${task_retries:-2}

  # Skip if already done
  if is_task_done "$task_id"; then
    log "SKIP $task_id — already done"
    continue
  fi

  log "START $task_id: $task_name"
  notify "🔨 Starting task: $task_name"

  success=false
  for attempt in $(seq 1 $((task_retries + 1))); do
    if past_deadline; then
      log "DEADLINE REACHED mid-task — stopping"
      notify "⏰ Deadline reached during: $task_name"
      break 2  # break out of both loops
    fi

    log "  attempt $attempt/$((task_retries + 1))"

    # Build the prompt with context
    full_prompt="You are working on the Memoreei project in $(pwd).
Virtual env is at .venv/bin/python. The .env file has DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID set.

YOUR TASK: $task_prompt

VERIFICATION: After completing the task, run this test command to verify:
$task_test

If the test fails, fix the issue and try again. Do not move on until the test passes."

    # Run Claude Code with clean context
    claude --permission-mode bypassPermissions --print "$full_prompt" \
      > "$LOG_DIR/${task_id}_attempt${attempt}.log" 2>&1 || true

    # Verify with test
    if run_test "$task_test"; then
      log "  PASS ✅"
      mark_done "$task_id"
      success=true
      consecutive_failures=0
      break
    else
      log "  FAIL ❌ (attempt $attempt)"

      # On final retry failure, escalate to agent for diagnosis
      if [ "$attempt" -eq "$((task_retries + 1))" ]; then
        log "  ESCALATING to agent for diagnosis..."
        last_log=$(cat "$LOG_DIR/${task_id}_attempt${attempt}.log" 2>/dev/null | tail -80)
        test_output=$(bash -c "$task_test" 2>&1 || true)

        escalation_msg="TASK FAILURE — needs agent diagnosis.

Task: $task_name (id: $task_id)
Test command: $task_test
Test output: $test_output

Last 80 lines of Claude Code output:
$last_log

Read the full log at: $LOG_DIR/${task_id}_attempt${attempt}.log
Read the task definition in tasks.json (task id: $task_id).

Diagnose the failure. Fix the code directly if you can. Then re-run the test:
$task_test

If you fix it, create the marker file: touch $LOG_DIR/${task_id}.done
If you can't fix it, explain why in $LOG_DIR/${task_id}.skip"

        # Write escalation to a file the heartbeat can pick up
        echo "$escalation_msg" > "$LOG_DIR/${task_id}.escalate"
        notify "🔧 Task '$task_name' failed — escalating to agent for fix"

        # Wait for agent to fix (up to 5 minutes)
        waited=0
        while [ $waited -lt 300 ]; do
          if [ -f "$LOG_DIR/${task_id}.done" ] || [ -f "$LOG_DIR/${task_id}.skip" ]; then
            break
          fi
          sleep 15
          waited=$((waited + 15))

          # Check deadline while waiting
          if past_deadline; then
            log "  DEADLINE while waiting for agent fix"
            break 2
          fi
        done

        if [ -f "$LOG_DIR/${task_id}.done" ]; then
          log "  AGENT FIX ✅"
          success=true
          consecutive_failures=0
          rm -f "$LOG_DIR/${task_id}.escalate"
        elif [ -f "$LOG_DIR/${task_id}.skip" ]; then
          log "  AGENT SKIPPED — $(cat "$LOG_DIR/${task_id}.skip")"
          rm -f "$LOG_DIR/${task_id}.escalate"
        else
          log "  AGENT TIMEOUT — no fix in 5 minutes"
        fi
      fi
    fi
  done

  if [ "$success" = true ]; then
    log "DONE $task_id ✅"
    notify "✅ Completed: $task_name"
  else
    log "FAILED $task_id ❌"
    notify "❌ Failed: $task_name — moving on"
    consecutive_failures=$((consecutive_failures + 1))

    if [ "$consecutive_failures" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
      log "TOO MANY CONSECUTIVE FAILURES ($consecutive_failures) — stopping"
      notify "🛑 Memoreei build stopped: $MAX_CONSECUTIVE_FAILURES consecutive failures"
      break
    fi
  fi
done

# --- final status ---

done_count=$(ls "$LOG_DIR"/*.done 2>/dev/null | wc -l)
log "=== Ralph Loop finished ==="
log "Completed: $done_count / $total tasks"

# Update PROGRESS.md
{
  echo "# Memoreei — Build Progress"
  echo ""
  echo "Last updated: $(date '+%Y-%m-%d %H:%M %Z')"
  echo ""
  echo "## Task Runner Results"
  echo ""
  echo "| Task | Status |"
  echo "|------|--------|"
  for i in $(seq 0 $((total - 1))); do
    tid=$(task_field "$i" "id")
    tname=$(task_field "$i" "name")
    if is_task_done "$tid"; then
      echo "| $tname | ✅ Done |"
    else
      echo "| $tname | ❌ Not completed |"
    fi
  done
} > "$PROGRESS_FILE"

# Final commit of any loose changes
cd "$PROJECT_DIR"
git add -A 2>/dev/null || true
git diff --cached --quiet 2>/dev/null || {
  git commit -m "chore: task runner progress update" 2>/dev/null || true
  git push origin main 2>/dev/null || true
}

notify "🏁 Memoreei Ralph Loop finished: $done_count/$total tasks complete"
log "Done."
