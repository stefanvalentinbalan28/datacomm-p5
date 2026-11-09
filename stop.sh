#!/usr/bin/env bash
# Opreste o rulare pornita cu startinf.sh.
# Nu opreste infrastructura: pentru aia -> cd compose && docker compose down
cd "$(dirname "$0")"

echo "1/3 opresc senzorii"
(cd compose && docker compose stop edge)

echo "2/3 astept 5 s sa se confirme ultimele batch-uri pe lant"
sleep 5

echo "3/3 opresc gateway-ul"
(cd compose && docker compose stop gateway)       # stop_signal: SIGINT -> CSV-urile se inchid curat
# gateway pornit vechi-stil, direct pe host
pkill -INT -f "gateway/gateway.py" 2>/dev/null && sleep 3

echo
echo "oprit curat. Ultimul log:"
ls -t results/*.csv 2>/dev/null | head -1
