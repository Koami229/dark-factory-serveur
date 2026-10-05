# Stage 1 — installation des dépendances (root)
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# Stage 2 — image runtime autonome (aucun accès réseau requis au démarrage)
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:${PATH}" \
    PYTHONPATH="/usr/local/lib/python3.11/site-packages"

RUN useradd --create-home --uid 1000 --shell /bin/bash appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY database.py models.py main.py index.html ./

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Image auto-suffisante : SQLite fichier local, pas d'appel réseau.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
