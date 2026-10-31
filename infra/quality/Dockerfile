ARG EXECUTOR_BASE=agent-company-executor:m1
FROM ${EXECUTOR_BASE}
USER root
RUN cp /opt/company/runner.py /opt/company/execution_runner.py
RUN apt-get update && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*
COPY package*.json /opt/quality/
RUN cd /opt/quality && npm ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org \
    && chmod -R a-w /opt/quality
COPY browser.cjs /opt/quality/browser.cjs
COPY preflight.cjs /opt/quality/preflight.cjs
COPY --from=agent-company-executor:m1 /opt/company/runner.py /opt/company/execution_runner.py
COPY quality_runner.py /opt/company/runner.py
USER 1000:1000
