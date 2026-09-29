# futu-tracker —— 零外部依赖（仅 Python 标准库），故镜像极小、无需 pip
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    FUTU_DB=/data/futu.db \
    FUTU_PORT=8300

WORKDIR /app
COPY app/ /app/app/
COPY scripts/ /app/scripts/
COPY tests/ /app/tests/

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8300

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,os;p=os.environ.get('FUTU_PORT','8300');urllib.request.urlopen(f'http://127.0.0.1:{p}/',timeout=3)"

CMD ["python", "-m", "app.web"]