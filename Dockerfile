# Personal Social Publishing OS - container image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/
COPY integrations/ integrations/
COPY scripts/ scripts/
COPY config/ config/
COPY prompts/ prompts/
COPY migrations/ migrations/

RUN pip install -e ".[discord,postgres]"

# Default: run the interactive Discord bot.
# Override command for pipeline stages, e.g.:
#   docker compose run --rm engine python scripts/run_pipeline.py --stage research
CMD ["python", "scripts/run_discord_bot.py"]
