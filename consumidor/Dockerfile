# Imagen base liviana con Python
FROM python:3.12-slim

WORKDIR /app

# Primero las dependencias, para aprovechar la cache de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Despues el codigo
COPY consumidor.py .

# -u: salida sin buffer, para ver los logs en tiempo real
CMD ["python", "-u", "consumidor.py"]
