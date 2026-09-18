FROM python:3.14.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY constraints.txt pyproject.toml README.md LICENSE ./
COPY bing_webmaster_ai_cli_mcp ./bing_webmaster_ai_cli_mcp

RUN PIP_CONSTRAINT=constraints.txt python -m pip install --no-cache-dir --constraint constraints.txt . \
    && groupadd --system app \
    && useradd --system --gid app --create-home app

USER app

ENTRYPOINT ["bing-webmaster-ai-cli-mcp"]
