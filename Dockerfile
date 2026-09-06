# Use official Python 3.10 Debian Bookworm image
FROM python:3.10-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

EXPOSE 8000

CMD ["sh", "-c", "python manage.py wait_for_db && python manage.py migrate && python -c \"import librosa, numpy as np; librosa.feature.rms(y=np.zeros(16000, dtype=np.float32)); librosa.effects.split(np.zeros(16000, dtype=np.float32)); print('librosa/numba warm-up complete')\" && gunicorn audoack_ai.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120"]
