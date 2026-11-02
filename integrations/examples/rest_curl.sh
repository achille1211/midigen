#!/usr/bin/env bash
# Runnable example: drive the REST service with curl.  Start it first:  midigen-service --port 8765
set -euo pipefail
URL=${MIDIGEN_URL:-http://127.0.0.1:8765}
OUT=${1:-out}; mkdir -p "$OUT"
curl -fsS "$URL/health"; echo
curl -fsS -X POST "$URL/generate?format=midi" -H 'Content-Type: application/json' \
     -d '{"genre":"house","key":"A","mode":"minor","bars":16,"seed":7}' -o "$OUT/house.mid"
echo "wrote $OUT/house.mid"
curl -fsS -X POST "$URL/generate" -H 'Content-Type: application/json' -d '{"genre":"jazz","bars":8}' > "$OUT/jazz.json"
python3 - "$OUT/jazz.json" > "$OUT/edit.json" <<'PY'
import json, sys
print(json.dumps({"spec": json.load(open(sys.argv[1]))["spec"], "instruction": "faster and add strings"}))
PY
curl -fsS -X POST "$URL/edit?format=midi" -H 'Content-Type: application/json' -d @"$OUT/edit.json" -o "$OUT/jazz_edited.mid"
echo "wrote $OUT/jazz_edited.mid"
