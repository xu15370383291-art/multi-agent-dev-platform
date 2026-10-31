FROM agent-company-executor:m3
USER root
COPY m4/runner.py /opt/company/runner.py
LABEL company.progress-watchdog="2"
USER 1000:1000
