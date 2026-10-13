#!/usr/bin/env bash
# routing-lab host-side battery executor.
#
# Polls queue/<32hex>.json for the oldest "pending" request (created by the
# lab-api container, which has NO docker access), runs the battery with the
# compose file selected by EXECUTOR_COMPOSE_FILE (default: the dev stack),
# streams per-scenario results into queue/<id>.events.jsonl (the lab-api
# replays/tails that file over SSE) and always tears the stack down afterwards.
#
# Usage:
#   ./deploy/executor.sh           # poll loop every EXECUTOR_POLL_S (default 5)
#   ./deploy/executor.sh --once    # single pass; exit 0 = nothing to do or run
#                                  # finished green, 1 = the run failed
#
# EXECUTOR_COMPOSE_FILE picks the stack the battery runs against (relative
# paths resolve against the repo root); default deploy/docker-compose.dev.yml,
# the prod cron install sets deploy/docker-compose.lab.yml (see deploy/cron/).
# A missing file is a hard error before anything else happens.
#
# Safe to run from anywhere (repo root resolved from the script path; every
# compose path is absolute) and from cron. Single instance enforced via flock
# on queue/.executor.lock. On startup, under the lock, any run left in
# "running" by a dead executor (SIGKILL/OOM/reboot) is recovered: error event +
# failed status + best-effort compose down for orphaned containers.
# Logs to queue/executor.log (and stdout) and self-caps that file at ~5 MB
# (keeps the last ~1 MB); evicting old queue records and OS-level logrotate
# are deployment-phase items. Battery hard timeout:
# EXECUTOR_BATTERY_TIMEOUT_S (default 300 s).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
QUEUE_DIR="$REPO_ROOT/queue"
REPORTS_DIR="$REPO_ROOT/reports"
LOG_FILE="$QUEUE_DIR/executor.log"
LOCK_FILE="$QUEUE_DIR/.executor.lock"

POLL_S="${EXECUTOR_POLL_S:-5}"
BATTERY_TIMEOUT_S="${EXECUTOR_BATTERY_TIMEOUT_S:-300}"

# Compose file for the battery, resolved against the repo root; prod points
# EXECUTOR_COMPOSE_FILE at deploy/docker-compose.lab.yml (see deploy/cron/).
COMPOSE_FILE="$REPO_ROOT/${EXECUTOR_COMPOSE_FILE:-deploy/docker-compose.dev.yml}"
if [[ ! -f "$COMPOSE_FILE" ]]; then
    printf 'ERROR: compose file not found: %s\n' "$COMPOSE_FILE" >&2
    printf 'hint: set EXECUTOR_COMPOSE_FILE (default deploy/docker-compose.dev.yml)\n' >&2
    exit 2
fi

# Absolute paths: the executor may run from cron/anywhere, never assume cwd.
COMPOSE=(docker compose --env-file "$REPO_ROOT/deploy/pinned-versions.env" -f "$COMPOSE_FILE")
# "[PASS] scenario 3 quick-failover (1.96s)" / "[FAIL] ..." lines from runner.
SCENARIO_RE='^\[(PASS|FAIL)\] scenario ([0-9]+) ([a-z0-9-]+) \(([0-9.]+)s\)$'
# Final "report: /app/reports/<report-id>.json" line of the runner console
# summary: the primary source for the finished run's report id.
REPORT_RE='^report: .*/([0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9-]{0,31})\.json$'

# log() self-cap: above LOG_MAX_BYTES the log is truncated to its last
# LOG_KEEP_BYTES so a long-lived cron executor cannot fill the disk.
LOG_MAX_BYTES=$((5 * 1024 * 1024))
LOG_KEEP_BYTES=$((1024 * 1024))

RUN_ACTIVE=0

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log() {
    if [[ -f "$LOG_FILE" ]]; then
        local size
        size="$(stat -c %s "$LOG_FILE" 2>/dev/null || printf '0')"
        if [[ "$size" =~ ^[0-9]+$ ]] && ((size > LOG_MAX_BYTES)); then
            if tail -c "$LOG_KEEP_BYTES" "$LOG_FILE" >"$LOG_FILE.tmp" &&
                mv -f "$LOG_FILE.tmp" "$LOG_FILE"; then
                printf 'executor: %s log capped to last %s bytes\n' \
                    "$(now_iso)" "$LOG_KEEP_BYTES" >>"$LOG_FILE"
            else
                rm -f "$LOG_FILE.tmp"
                printf 'WARN: could not cap %s\n' "$LOG_FILE" >&2
            fi
        fi
    fi
    printf '%s executor: %s\n' "$(now_iso)" "$*" | tee -a "$LOG_FILE"
}

# append_event <id> <jq args...>  -- writes one event line for the run.
append_event() {
    local id="$1"
    shift
    if ! jq -cn --arg ts "$(now_iso)" "$@" >>"$QUEUE_DIR/$id.events.jsonl"; then
        log "WARN: could not append event for run $id"
    fi
}

set_status() {
    local id="$1" status="$2" path="$QUEUE_DIR/$1.json"
    # tmp+rename works even though the file was created by the container user
    # (the queue directory itself is owned by the host user).
    if ! jq -c --arg s "$status" '.status=$s' "$path" >"$path.tmp"; then
        log "ERROR: could not rewrite $path (status=$status)"
        return 1
    fi
    mv -f "$path.tmp" "$path"
}

newest_report() {
    local f
    f="$(ls -1t "$REPORTS_DIR"/*.json 2>/dev/null | head -n 1)"
    [[ -n "$f" ]] && printf '%s' "$f"
}

# Echo the id of the oldest pending request, if any.
find_pending() {
    local f base st ts best="" best_ts=""
    for f in "$QUEUE_DIR"/*.json; do
        [[ -e "$f" ]] || continue
        base="$(basename "$f")"
        [[ "$base" =~ ^[0-9a-f]{32}\.json$ ]] || continue
        st="$(jq -r '.status // empty' "$f" 2>/dev/null)"
        [[ "$st" == "pending" ]] || continue
        ts="$(jq -r '.requested_at // empty' "$f" 2>/dev/null)"
        # ISO-8601 Z timestamps sort lexicographically = chronologically.
        if [[ -z "$best" || "$ts" < "$best_ts" ]]; then
            best="${base%.json}"
            best_ts="$ts"
        fi
    done
    [[ -n "$best" ]] && printf '%s' "$best"
}

# Recover runs left in "running" by a dead executor (SIGKILL/OOM/reboot
# between the running write and a terminal status). Only safe with the flock
# held: that guarantees no other executor is mid-run. Appends an error event,
# flips the entry to failed and best-effort tears down any orphaned
# `docker compose run` container the dead executor left behind.
sweep_stale_running() {
    local f base st id stale=0
    for f in "$QUEUE_DIR"/*.json; do
        [[ -e "$f" ]] || continue
        base="$(basename "$f")"
        [[ "$base" =~ ^[0-9a-f]{32}\.json$ ]] || continue
        st="$(jq -r '.status // empty' "$f" 2>/dev/null)"
        [[ "$st" == "running" ]] || continue
        id="${base%.json}"
        stale=1
        append_event "$id" --arg stage error \
            --arg message "stale_running: previous executor died mid-run" \
            '{ts:$ts,stage:$stage,message:$message}'
        set_status "$id" failed
        log "recovered stale running run $id (marked failed)"
    done
    if ((stale)); then
        log "stale-run sweep: best-effort compose down for orphaned containers"
        LAB_PROFILE=quick "${COMPOSE[@]}" down >>"$LOG_FILE" 2>&1 ||
            log "WARN: stale-run sweep compose down failed"
    fi
}

cleanup() {
    if [[ "$RUN_ACTIVE" != "1" ]]; then
        return 0
    fi
    RUN_ACTIVE=0
    log "tearing down lab stack (docker compose down)"
    LAB_PROFILE=quick "${COMPOSE[@]}" down >>"$LOG_FILE" 2>&1 ||
        log "WARN: docker compose down failed"
}

fail_run() { # <id> <classification> <detail> [report_id]
    local id="$1" classification="$2" detail="$3" report_id="${4:-}"
    log "run $id FAILED: $classification ($detail)"
    if [[ -n "$report_id" ]]; then
        append_event "$id" --arg stage error --arg message "$classification: $detail" \
            --arg report_id "$report_id" \
            '{ts:$ts,stage:$stage,message:$message,report_id:$report_id}'
    else
        append_event "$id" --arg stage error --arg message "$classification: $detail" \
            '{ts:$ts,stage:$stage,message:$message}'
    fi
    set_status "$id" failed
}

process_run() { # <id>  -> 0 done, 1 failed
    local id="$1"
    local meta="$QUEUE_DIR/$id.json"
    local profile
    profile="$(jq -r '.profile // ""' "$meta" 2>/dev/null)"
    log "picked up run $id (profile=${profile:-unknown})"
    set_status "$id" running

    if [[ "$profile" != "quick" ]]; then
        fail_run "$id" "unsupported_profile" \
            "only 'quick' is accepted by the local executor (got '${profile:-unknown}')"
        return 1
    fi

    append_event "$id" --arg stage boot '{ts:$ts,stage:$stage}'

    local before_report after_report rc report_id=""
    before_report="$(newest_report)"

    RUN_ACTIVE=1

    LAB_PROFILE=quick "${COMPOSE[@]}" up -d --build >>"$LOG_FILE" 2>&1
    rc=$?
    if ((rc != 0)); then
        cleanup
        fail_run "$id" "compose_up_failed" "docker compose up -d --build exited $rc"
        return 1
    fi

    # Profile-gated images are not rebuilt by `up --build`; build explicitly.
    LAB_PROFILE=quick "${COMPOSE[@]}" --profile run build runner >>"$LOG_FILE" 2>&1
    rc=$?
    if ((rc != 0)); then
        cleanup
        fail_run "$id" "runner_build_failed" "docker compose --profile run build runner exited $rc"
        return 1
    fi

    # Stream the battery; emit one "scenario" event per PASS/FAIL line and
    # capture the runner's own "report: <path>" line (primary source for the
    # new report id; the while-loop body runs in a subshell, so the id is
    # staged through a temp file). PYTHONUNBUFFERED keeps the runner's stdout
    # line-buffered so events arrive live over SSE. "-T" + stdin from
    # /dev/null keep `compose run` from holding the attach open on a stdin
    # that never EOFs (cron/CI/script contexts). Hard timeout for the battery.
    local line streamed_report_file streamed_report_id=""
    streamed_report_file="$(mktemp 2>/dev/null)" || streamed_report_file=""
    LAB_PROFILE=quick timeout --signal=TERM --kill-after=30 "$BATTERY_TIMEOUT_S" \
        "${COMPOSE[@]}" --profile run run --rm -T -e PYTHONUNBUFFERED=1 runner \
        </dev/null 2>&1 |
        while IFS= read -r line; do
            printf '%s battery: %s\n' "$(now_iso)" "$line" >>"$LOG_FILE"
            if [[ "$line" =~ $SCENARIO_RE ]]; then
                append_event "$id" \
                    --argjson scenario "${BASH_REMATCH[2]}" \
                    --arg name "${BASH_REMATCH[3]}" \
                    --arg status "${BASH_REMATCH[1],,}" \
                    '{ts:$ts,stage:"scenario",scenario:$scenario,name:$name,status:$status}'
            elif [[ -n "$streamed_report_file" && "$line" =~ $REPORT_RE ]]; then
                printf '%s' "${BASH_REMATCH[1]}" >"$streamed_report_file"
            fi
        done
    rc=${PIPESTATUS[0]}
    if [[ -n "$streamed_report_file" ]]; then
        streamed_report_id="$(cat "$streamed_report_file" 2>/dev/null)"
        rm -f "$streamed_report_file"
    fi

    # Primary: the runner's own report line; fallback: newest-file diff.
    after_report="$(newest_report)"
    if [[ -n "$streamed_report_id" ]]; then
        report_id="$streamed_report_id"
    elif [[ -n "$after_report" && "$after_report" != "$before_report" ]]; then
        report_id="$(basename "$after_report" .json)"
    fi

    if ((rc == 124)); then
        log "battery timed out after ${BATTERY_TIMEOUT_S}s; killing runner"
        "${COMPOSE[@]}" --profile run kill runner >>"$LOG_FILE" 2>&1 || true
        cleanup
        fail_run "$id" "timeout" "battery exceeded ${BATTERY_TIMEOUT_S}s" "$report_id"
        return 1
    fi

    if ((rc != 0)); then
        cleanup
        if [[ -n "$report_id" ]]; then
            fail_run "$id" "battery_failed" "docker compose run runner exited $rc" "$report_id"
        else
            fail_run "$id" "report_missing" "docker compose run runner exited $rc with no new report"
        fi
        return 1
    fi

    if [[ -z "$report_id" ]]; then
        cleanup
        fail_run "$id" "report_missing" "runner exited 0 but no new reports/<run_id>.json appeared"
        return 1
    fi

    append_event "$id" --arg stage teardown '{ts:$ts,stage:$stage}'
    append_event "$id" --arg stage done --arg report_id "$report_id" \
        '{ts:$ts,stage:$stage,report_id:$report_id}'
    set_status "$id" done
    log "run $id done; report=$report_id"
    cleanup
    return 0
}

run_once() {
    local id
    id="$(find_pending)"
    if [[ -z "$id" ]]; then
        log "nothing to do"
        return 0
    fi
    process_run "$id"
}

main() {
    case "${1:-}" in
        "" | "--once") ;;
        *)
            printf 'usage: %s [--once]\n' "$0" >&2
            exit 2
            ;;
    esac

    mkdir -p "$QUEUE_DIR" "$REPORTS_DIR"

    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log "another executor instance holds the lock; exiting"
        exit 0
    fi

    # We own the lock: no other executor can be mid-run, so any "running"
    # entry is stale (its executor died before writing a terminal status).
    sweep_stale_running

    if [[ "${1:-}" == "--once" ]]; then
        run_once
        exit $?
    fi

    log "executor loop started (poll ${POLL_S}s, battery timeout ${BATTERY_TIMEOUT_S}s)"
    while true; do
        run_once || true
        sleep "$POLL_S"
    done
}

trap 'exit 130' INT TERM
trap 'cleanup' EXIT

main "$@"
