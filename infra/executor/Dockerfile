FROM public.ecr.aws/docker/library/node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
RUN apt-get update && apt-get install -y --no-install-recommends git python3 \
    && rm -rf /var/lib/apt/lists/*
COPY toolchain/package*.json /opt/toolchain/
RUN cd /opt/toolchain && npm ci --ignore-scripts --no-audit --no-fund \
    --registry=https://registry.npmjs.org && chmod -R a-w /opt/toolchain
COPY runner.py /opt/company/runner.py
ENV HOME=/workspace TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
WORKDIR /workspace
CMD ["sleep", "infinity"]
