#!/usr/bin/env bash
# Porneste tot proiectul end-to-end si deschide graficul.
#   ./start.sh                   2 rulari de 120 s (~6 min)
#   RUN_SECONDS=60 ./start.sh    mai scurt, pentru demo (~3 min)
set -e
cd "$(dirname "$0")"
PY="$HOME/venv-p5/bin/python"

echo "== 1/4 infrastructura =="
(cd compose && docker compose up -d)

echo "== 2/4 aptept lantul =="
for _ in $(seq 1 60); do
  bn=$(curl -s --max-time 2 -X POST http://127.0.0.1:8545 \
       -H 'Content-Type: application/json' \
       --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
       | sed -n 's/.*"result":"\(0x[0-9a-f]*\)".*/\1/p')
  if [ -n "$bn" ] && [ "$bn" != "0x0" ]; then echo "  ok, bloc $bn"; break; fi
  printf '.'; sleep 2
done
if [ -z "$bn" ] || [ "$bn" = "0x0" ]; then
  echo "  lantul nu produce blocuri:"
  echo "  (cd compose && docker compose logs besu-node1 | tail -30)"
  exit 1
fi

echo "== 3/4 contractul =="
"$PY" chain/deploy.py

echo "== 4/4 rularile + graficul =="
"$PY" run_experiments.py --quick

png="$(ls -td results/experiments_* | head -1)/latency.png"
echo
echo "grafic:   $png"
echo "grafana:  http://localhost:3000"
echo "influxdb: http://localhost:8086   (admin / parolasecreta)"
explorer.exe "$(wslpath -w "$png")" 2>/dev/null || true
