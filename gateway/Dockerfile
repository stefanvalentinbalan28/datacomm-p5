# Containerul gateway-ului. Codul vine prin bind-mount (vezi compose/docker-compose.yml),
# ca sa poti edita fara rebuild; aici stau doar bibliotecile.
FROM python:3.12-slim

# Aceleasi versiuni ca in requirements.txt
RUN pip install --no-cache-dir \
      paho-mqtt==2.2.0 pynacl==1.6.2 influxdb-client==1.50.0 web3==8.0.0

WORKDIR /app
CMD ["python", "-u", "gateway/gateway.py", "anchoring"]
