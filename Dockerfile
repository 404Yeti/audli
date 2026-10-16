FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends espeak && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY scripts scripts
COPY docs/postgres.sql docs/postgres.sql
COPY docs/postgres-002.sql docs/postgres-002.sql
COPY docs/postgres-003.sql docs/postgres-003.sql
COPY docs/postgres-004.sql docs/postgres-004.sql
COPY docs/postgres-005.sql docs/postgres-005.sql
COPY docs/accent-release-evidence /app/data/voice-evaluation/aud20-validation-20261010
COPY docs/aud38-release-evidence /app/data/voice-evaluation/aud38-approved
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
