FROM python:3.12-slim

ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="weblog-analysis" \
      org.opencontainers.image.description="Analyse von Apache/nginx-Access-Logs mit Vhost-Feld: Wochen, Netze, Bots, Scanner" \
      org.opencontainers.image.source="https://github.com/skoelle/weblog-analysis" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data
VOLUME ["/app/data"]
EXPOSE 8095

CMD ["python", "-m", "app"]
