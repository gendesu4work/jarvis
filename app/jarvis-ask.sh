#!/bin/bash
# Called by the Windows voice bridge. The request text arrives in $JARVIS_PROMPT
# (never parsed as a command). Continues the latest Jarvis conversation, or starts one.
cd "$(cat "$HOME/.jarvis/workspace")" || exit 1
H="$HOME/.local/bin/hermes"
err=$(mktemp)
out=$("$H" -z "$JARVIS_PROMPT" --resume latest 2>"$err"); rc=$?
if [ "$rc" -ne 0 ] && grep -qi "no session" "$err"; then
  rm -f "$err"
  exec "$H" -z "$JARVIS_PROMPT"
fi
printf '%s' "$out"
cat "$err" >&2; rm -f "$err"
exit "$rc"
