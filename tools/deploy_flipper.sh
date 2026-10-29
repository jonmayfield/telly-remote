#!/bin/sh
# Rebuild the Telly Remote Flipper app against the patched 1.5.1 firmware tree
# (.fw151, see flipper/firmware/README.md), then close the running copy and
# launch the new one. Stop server.py first; it shares the Flipper's USB port.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FW="$ROOT/.fw151"
mkdir -p "$FW/applications_user/telly_remote"
cp "$ROOT/flipper/telly_remote/application.fam" "$ROOT/flipper/telly_remote/telly_remote.c" \
   "$FW/applications_user/telly_remote/"
cd "$ROOT"
python3 -I tools/flipcli.py "input send back press" "input send back long" "input send back release" >/dev/null
sleep 2
cd "$FW"
./fbt COMPACT=1 DEBUG=0 launch APPSRC=applications_user/telly_remote
