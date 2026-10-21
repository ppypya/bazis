FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY tests ./tests
COPY scripts ./scripts
RUN pip install --no-deps . && useradd --create-home payments
USER payments

CMD ["uvicorn", "config.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
