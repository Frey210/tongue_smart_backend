FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && mkdir -p /data \
    && chown 65532:65532 /data

USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "tongue_smart.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
