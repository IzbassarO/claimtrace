#!/usr/bin/env bash
# Screenshots of the real UI served by scripts/replay_server.py (start it first, port 8791).
# Usage: .design/claimtrace/final/shoot.sh [static|stream]
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT=.design/claimtrace/final
B=http://127.0.0.1:8791
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
shot() { # name url width height
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check \
    --user-data-dir="$PWD/.cache/${PROFILE:-chrome}" --window-size="$3,$4" --virtual-time-budget=4000 \
    --screenshot="$OUT/$1.png" "$2" 2>/dev/null
}
if [[ "${1:-static}" == static ]]; then
  shot home-light "$B/dev/show/home?theme=light" 1280 1000
  shot home-dark "$B/dev/show/home?theme=dark" 1280 1000
  shot home-mobile "$B/dev/frame?src=/dev/show/home%3Ftheme%3Dlight&w=390&h=1500" 390 1500
  shot report-sample-light "$B/dev/show/sample?theme=light&open=1" 1280 2200
  shot report-final-light "$B/dev/show/final?theme=light&open=1" 1280 4200
  shot report-final-dark "$B/dev/show/final?theme=dark&open=1" 1280 4200
  shot report-mobile-390 "$B/dev/frame?src=/dev/show/final%3Ftheme%3Dlight&w=390&h=5000" 390 5000
  shot report-empty "$B/dev/show/empty?theme=light" 1280 1000
  shot long-cases "$B/dev/show/long?theme=light&open=1" 1280 2000
  shot long-cases-mobile "$B/dev/frame?src=/dev/show/long%3Ftheme%3Dlight%26open%3D1&w=390&h=3600" 390 3600
  shot refs-200 "$B/dev/show/refs200?theme=light" 1280 3000
else
  # streaming: one replayed job (~150 s), polled in a fresh browser at 5, 30, 120 s and after it ends
  ID=$(curl -s -F text=replay "$B/api/jobs" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  T0=$SECONDS
  for t in 5 30 120 158; do
    while (( SECONDS - T0 < t )); do sleep 0.2; done
    name="stream-${t}s"; [[ $t == 158 ]] && name=stream-finished
    PROFILE=chrome-stream shot "$name" "$B/dev/show/home?theme=light&job=$ID" 1280 2400
  done
fi
