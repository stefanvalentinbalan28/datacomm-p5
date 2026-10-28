import time
import json
import hmac
import hashlib
import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

CHEIE_SECRETA = b"cheia_secreta_proiect_5"

# 1. Configurarea conexiunii către baza de date InfluxDB
INFLUX_URL = "http://127.0.0.1:8086"
INFLUX_TOKEN = "token_secret_proiect5"
INFLUX_ORG = "proiect5"
INFLUX_BUCKET = "iot_data"

# Inițializăm clientul de bază de date
influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = influx_client.write_api(write_options=SYNCHRONOUS)


def verifica_mesaj(client, userdata, msg):
    payload_primit = msg.payload.decode('utf-8')
    mesaj = json.loads(payload_primit)

    semnatura_primita = mesaj.pop("sig", None)

    date_text = json.dumps(mesaj, sort_keys=True).encode('utf-8')
    semnatura_calculata = hmac.new(CHEIE_SECRETA, date_text, hashlib.sha256).hexdigest()

    if semnatura_primita != semnatura_calculata:
        print("❌ EROARE: Semnătură invalidă! Mesajul a fost alterat.")
        return

    timp_curent = int(time.time())
    if timp_curent - mesaj["ts_device"] > 5:
        print("❌ EROARE: Mesaj prea vechi (mai mult de 5 secunde).")
        return

    # 2. Dacă mesajul e valid, formăm înregistrarea pentru baza de date
    punct_date = Point("masuratori_senzori") \
        .tag("device_id", mesaj["device_id"]) \
        .field("secventa", mesaj["seq"]) \
        .field("dimensiune_bytes", len(mesaj["payload"])) \
        .time(time.time_ns(), WritePrecision.NS)

    # 3. Scriem datele efectiv în InfluxDB
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=punct_date)

    print(f"✅ SUCCES: Mesajul de la {mesaj['device_id']} a fost salvat în InfluxDB!")


# Conectarea la MQTT rămâne la fel
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_message = verifica_mesaj

mqtt_client.connect("127.0.0.1", 1883)
mqtt_client.subscribe("senzori/trafic")

print("Gateway conectat la MQTT și InfluxDB. Aștept date...")
mqtt_client.loop_forever()