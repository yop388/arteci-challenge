# ==========================================
# STAGE 1 : Builder
# ==========================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Installation des dépendances système requises pour compiler certains packages si nécessaire
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copie uniquement le fichier des dépendances
COPY requirements.txt .

# Installation des dépendances dans un dossier local pour isolation facile
RUN pip install --no-cache-dir --user -r requirements.txt

# ==========================================
# STAGE 2 : Runner (Image finale légère)
# ==========================================
FROM python:3.11-slim AS runner

WORKDIR /app

# Récupération des packages installés depuis le builder
COPY --from=builder /root/.local /root/.local
COPY api/src /app/api/src

# Mise à jour du PATH pour que Python trouve les packages dans /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Port exposé par FastAPI
EXPOSE 8000

# Commande de démarrage de l'API de production
CMD ["uvicorn", "api.src.main:app", "--host", "0.0.0.0", "--port", "8000"]