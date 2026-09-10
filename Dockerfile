# BloodNet API — single container serving match-svc + swarm-svc behind the
# main.py gateway (see main.py for why this is one container instead of
# one per subsystem: infra/terraform/main.tf provisions one Cloud Run
# service, "bloodnet-api", and this image is built to match it).
#
# No application code is changed to build this image — only added to
# (main.py, requirements.txt) and one packaging bug fixed in pyproject.toml
# (see that file's [tool.setuptools] section).

# Use the full Debian-based Python image instead of `slim` so `libgomp` is
# already present for LightGBM at runtime without requiring a networked
# `apt-get install` during Cloud Build.
FROM python:3.11

WORKDIR /app

# Dependencies first so this layer is cached across source-only changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code. Each line is its own COPY so unrelated changes (e.g.
# ml/ model retraining) don't bust the cache for unrelated source, and so
# nothing outside these directories (docs/, web source, tests_e2e/, etc.) ends
# up in the runtime image.
COPY contracts/ ./contracts/
COPY services/ ./services/
COPY sim/ ./sim/
COPY ml/ ./ml/
COPY migrations/ ./migrations/
COPY scripts/bootstrap_sop_rag.py ./scripts/bootstrap_sop_rag.py
COPY scripts/start_api.sh ./scripts/start_api.sh
COPY main.py ./main.py
# The React UI is built before the image build and served as a release artifact.
COPY web/app/dist/ ./web/app/dist/

# Cloud Run injects $PORT (defaults to 8080); infra/terraform's
# `bloodnet-api` Cloud Run service is fixed at container_port = 8080, so
# default to the same value here for local/container parity.
ENV PORT=8080 \
    BLOODNET_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

# Run as a non-root user.
RUN useradd --create-home --uid 10001 bloodnet \
    && chown -R bloodnet:bloodnet /app
USER bloodnet

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8080') + '/health', timeout=2)" || exit 1

# Run migrations before starting the application.
# If migrations fail, the container will exit with non-zero code and fail deployment.
# If BLOODNET_DATABASE_URL is not set, migrations will error and prevent accidental local usage.
CMD ["/bin/sh", "/app/scripts/start_api.sh"]
