FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /sgc

# Dependências de SO para o WeasyPrint gerar PDFs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV FLASK_HOST=0.0.0.0 \
    FLASK_DEBUG=false \
    GUNICORN_WORKERS=3 \
    GUNICORN_TIMEOUT=60

EXPOSE 5000

# Gunicorn como WSGI de produção (substitui o dev server do Flask).
CMD ["sh", "-c", "gunicorn --chdir app --bind 0.0.0.0:5000 --workers ${GUNICORN_WORKERS} --timeout ${GUNICORN_TIMEOUT} --access-logfile - --error-logfile - main:app"]
