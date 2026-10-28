import time
import json
import hmac
import hashlib
import paho.mqtt.client as mqtt

CHEIE_SECRETA = b"cheia_secreta_proiect_5"

def genereaza_mesaj(device_id, seq):
    mesaj = {
        "device_id": device_id,
        "ts_device": int(time.time()),
        "seq": seq,
        "payload": "X" * 200
    }
    # Semnarea mesajului pentru integritate
    date_text = json.dumps(mesaj, sort_keys=True).encode('utf-8')
    mesaj["sig"] = hmac.new(CHEIE_SECRETA, date_text, hashlib.sha256).hexdigest()
    return json.dumps(mesaj)


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect("127.0.0.1", 1883)

# Pornește un fir de execuție în fundal pentru a menține conexiunea
client.loop_start()
lista_senzori = ["senzor_1","senzor_2","senzor_3","senzor_4"]
secventa = 1
while True:
   for senzori in lista_senzori:

    mesaj_final = genereaza_mesaj(senzori,secventa)
    rezultat = client.publish("senzori/trafic", mesaj_final)
    rezultat.wait_for_publish()

    print(f"Mesaj de le {senzori} cu {secventa} trimis!")
    secventa += 1
    time.sleep(2)