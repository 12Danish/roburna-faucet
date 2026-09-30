FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system faucet \
    && useradd --system --gid faucet --home-dir /app faucet

COPY --chown=faucet:faucet alembic.ini ./
COPY --chown=faucet:faucet migrations ./migrations
COPY --chown=faucet:faucet app ./app
USER faucet

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
