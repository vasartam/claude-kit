#!/bin/bash
# StopFailure: ход оборвался на сетевой ошибке API («Connection lost mid-response» и т.п.).
# Хук StopFailure неблокирующий и сам ход продолжить не может, поэтому в фоне ждём,
# пока агент в панели herdr станет idle, и отправляем ему «продолжай».
# Предохранитель: не больше MAX_RETRIES автопродолжений за WINDOW секунд на сессию.
# Вне herdr (нет HERDR_PANE_ID или самого herdr) — сразу выход 0.

MAX_RETRIES="${API_AUTOCONTINUE_MAX:-5}"
WINDOW="${API_AUTOCONTINUE_WINDOW:-600}"
DELAY="${API_AUTOCONTINUE_DELAY:-5}"
PROMPT="Предыдущий ответ оборвался из-за сетевой ошибки API. Продолжай с того места, где остановился."

input="$(cat)"
[ -n "${HERDR_PANE_ID:-}" ] || exit 0
herdr="$(command -v herdr 2>/dev/null)" || herdr="$HOME/.local/bin/herdr"
[ -x "$herdr" ] || exit 0

session="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id","nosession"))' 2>/dev/null)"
state_dir="${TMPDIR:-/tmp}/claude-api-autocontinue"
mkdir -p "$state_dir"
log="$state_dir/${session:-nosession}.log"

now="$(date +%s)"
recent=0
if [ -f "$log" ]; then
  recent="$(awk -v t="$((now - WINDOW))" '$1 >= t' "$log" | wc -l | tr -d ' ')"
fi
if [ "$recent" -ge "$MAX_RETRIES" ]; then
  echo "api-error-autocontinue: лимит $MAX_RETRIES за ${WINDOW}s исчерпан, жду человека" >&2
  exit 0
fi
echo "$now" >>"$log"

pane="$HERDR_PANE_ID"
(
  "$herdr" agent wait "$pane" --until idle --until done --timeout 60000 >/dev/null 2>&1
  sleep "$DELAY"
  "$herdr" agent prompt "$pane" "$PROMPT" >/dev/null 2>&1
) </dev/null >/dev/null 2>&1 &
disown 2>/dev/null
exit 0
