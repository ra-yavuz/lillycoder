# lillycoder dev sandbox.
#
# Builds a small image that has lillycoder installed and a few useful
# things (git, ripgrep, node) so the agent can do real work. The compose
# file mounts WORKINGDIR/ from the host as /workspace and starts you in
# there. This image is for development; end users install via apt.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates jq ripgrep file less wget \
        nodejs npm build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        httpx \
        prompt_toolkit \
        rich \
        pydantic

# The repo source is mounted at /opt/lillycoder via docker-compose so
# changes hot-reload. The pip install -e . is done at container start
# by the entrypoint, not at build time.

WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

CMD ["bash"]
