# Образ сервиса. Стадия runtime запускается в compose.yaml, стадия test проверяет сборку:
#   docker build --target test -t knowledge-base-test . && docker run --rm knowledge-base-test
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

FROM base AS build
WORKDIR /build
RUN python -m venv /opt/kb
COPY pyproject.toml README.md ./
COPY src ./src
RUN /opt/kb/bin/pip install .

FROM build AS test
RUN /opt/kb/bin/pip install ".[test]"
COPY alembic.ini ./
COPY migrations ./migrations
COPY samples ./samples
COPY tests ./tests
ENV PATH=/opt/kb/bin:$PATH
CMD ["pytest", "-q", "--no-header", "-p", "no:cacheprovider"]

FROM base AS runtime
# Сервис работает не от root: при взломе процесс не получит прав на систему внутри контейнера.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin kb
COPY --from=build /opt/kb /opt/kb
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH=/opt/kb/bin:$PATH
USER kb
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)"]
# Журнал обращений отключён: сервис пишет свой журнал, где нет строки запроса с текстом поиска.
# Рабочий процесс один: поисковые индексы хранятся в памяти процесса.
CMD ["uvicorn", "--factory", "kb.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log", "--no-server-header"]
