# Build stage
FROM python:3.14-slim AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Runtime stage
FROM python:3.14-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/mithr4ndir/k8s-argocd"
LABEL org.opencontainers.image.description="AlertManager to Discord themed-embed proxy"
LABEL org.opencontainers.image.licenses="MIT"

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN groupadd -g 10001 appuser \
    && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin appuser

WORKDIR /app
COPY proxy.py /app/proxy.py
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 9095

CMD ["python", "/app/proxy.py"]
