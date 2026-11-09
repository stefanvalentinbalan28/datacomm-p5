#!/usr/bin/env bash
# Toata aplicatia in Docker, nesfarsit, pana la Ctrl+C. Nu are nevoie de nimic instalat
# pe host in afara de Docker.
#   ./startinf.sh                      50 senzori, mod anchoring
#   DEVICES=200 ./startinf.sh          200 (atentie: coada creste pana faci B1+B4)
#   MODE=nochain ./startinf.sh         fara lant, ca referinta
cd "$(dirname "$0")"
DEVICES="${DEVICES:-50}"
MODE="${MODE:-anchoring}"
export MODE
DC="docker compose --profile app"

trap 'echo; (cd compose && docker compose stop edge gateway); exit' INT

echo "1/4 infrastructura"
(cd compose && docker compose up -d --build)

echo "2/4 astept InfluxDB si lantul"
ready=0
for _ in $(seq 1 60); do
  influx_ok=0
  curl -s --max-time 2 http://127.0.0.1:8086/health | grep -q pass && influx_ok=1
  bn=$(curl -s --max-time 2 -X POST http://127.0.0.1:8545 \
       -H 'Content-Type: application/json' \
       --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
       | sed -n 's/.*"result":"\(0x[0-9a-f]*\)".*/\1/p')
  if [ "$influx_ok" = 1 ] && [ -n "$bn" ] && [ "$bn" != "0x0" ]; then
    echo "  ok, InfluxDB gata, lantul la blocul $bn"
    ready=1
    break
  fi
  printf '.'
  sleep 2
done
if [ "$ready" != 1 ]; then
  echo
  echo "  nu s-au ridicat in 2 minute. Verifica:"
  echo "  (cd compose && docker compose ps; docker compose logs --tail 30 influxdb besu-node1)"
  exit 1
fi

# Lantul porneste de la zero la fiecare up (fara volume de date), deci adresa din
# chain/deployment.json e veche: redesfasuram mereu.
if [ "$MODE" = "anchoring" ]; then
  echo "3/4 contractul"
  (cd compose && $DC run --rm --no-deps gateway python chain/deploy.py) || exit 1
else
  echo "3/4 contractul: sarit (MODE=nochain)"
fi

echo "4/4 gateway + senzori ($DEVICES)"
(cd compose && $DC up -d --force-recreate gateway) || exit 1
sleep 4
if [ "$(cd compose && docker compose ps -q --status running gateway)" = "" ]; then
  echo "  gateway-ul a murit la pornire:"
  (cd compose && docker compose logs --tail 30 gateway)
  exit 1
fi

# --force-recreate: senzori proasperi, pornind dupa gateway, cu seed-ul curent
(cd compose && SEED=1 DEVICES="$DEVICES" docker compose up -d --force-recreate edge)

echo
echo "--- ruleaza. Ctrl+C ca sa opresti (sau ./stop.sh din alt terminal) ---"
echo "    grafana: http://localhost:3000/d/p5-live/p5-iot-live  (Last 15 minutes)"
echo
(cd compose && docker compose logs -f gateway)
