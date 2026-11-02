# Личная база знаний

Сервис принимает документы в формате Markdown, анализирует их, оценивает релевантность и ищет по
ним с учётом русской и английской морфологии. Проект выполняется по кейсу «Личная база знаний»
программы VK Education Projects.

Готовы ядро (импорт, разбор, индексация, скоринг, поиск), сервис с REST API, хранение документов с
версиями в PostgreSQL, кэш выдачи в Redis, пользователи с раздельными данными, совместный доступ к
документам и веб-интерфейс. Сервис упакован в контейнеры.

| Документ | Что в нём |
|---|---|
| [docs/deploy.md](docs/deploy.md) | Развёртывание, импорт данных, резервное копирование |
| [docs/architecture.md](docs/architecture.md) | Устройство сервиса и защита данных |
| [docs/scoring.md](docs/scoring.md) | Алгоритмы ранжирования и настройка весов |
| [docs/openapi.json](docs/openapi.json) | Контракт API |

## Быстрый запуск в Docker

```bash
sh tools/init_env.sh
sh tools/make_cert.sh
docker compose --profile demo up -d --build
```

Сервис открывается на `https://localhost:8443`, письма со ссылками для подтверждения видны на
`http://127.0.0.1:8025`, документация методов лежит на `https://localhost:8443/docs`.

## Запуск без Docker

Нужны Python 3.10 или новее, PostgreSQL и, по желанию, Redis.

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
```

Настройки читаются из переменных окружения или файла `.env`, образец лежит в `.env.example`. Для работы
без TLS нужно указать `KB_COOKIE_SECURE=false`. Рабочий процесс должен быть один: индексы хранятся в
памяти процесса.

```bash
alembic upgrade head
uvicorn --factory kb.api.app:create_app --port 8000
```

## API

Методы лежат под `/api/v1`. Все методы работы с данными требуют заголовок
`Authorization: Bearer <токен>`, токен выдаёт `POST /api/v1/auth/login` после подтверждения адреса.

```bash
curl -k -H "Authorization: Bearer $TOKEN" -F "files=@samples/postgresql-indexes.md" \
  https://127.0.0.1:8443/api/v1/documents/import
curl -k -H "Authorization: Bearer $TOKEN" \
  "https://127.0.0.1:8443/api/v1/search?q=индексы&scorer=pgfts"
```

## Командная строка

Командная строка работает с каталогом файлов и не требует ни базы данных, ни Redis.

```bash
kb check samples
kb stats samples
kb search samples "индексы для поиска"
kb search samples "кэш" --scorer tfidf --tag производительность --sort created --limit 5
kb -v search samples "кэш"
```

Шапка документа необязательна. Без неё название берётся из первого заголовка или имени файла.

```markdown
---
title: Индексы в PostgreSQL
tags: [postgresql, производительность]
category: Базы данных
author: Автор
created: 2026-09-14
---
```

## Методы скоринга

| Имя | Метод |
|---|---|
| `bm25` | Okapi BM25 с параметрами `k1` и `b` |
| `tfidf` | TF-IDF с логарифмической частотой и нормализацией на длину |
| `hybrid` | Взвешенная сумма нормализованных оценок других методов |
| `pgfts` | Полнотекстовый поиск PostgreSQL с индексом GIN |

Формулы и настройка весов описаны в [docs/scoring.md](docs/scoring.md). Совпадения в полях весят
по-разному: название 3, подзаголовки 2, теги 2, текст 1, код 0,5. Веса
задаются параметром `field_weights`. Новый метод подключается так:

```python
from kb.scoring import Scorer, register_scorer

@register_scorer
class MyScorer(Scorer):
    name = "my-method"

    def score(self, query_terms, index):
        ...
```

## Структура

| Модуль | Назначение |
|---|---|
| `kb/normalize.py` | Проверка и нормализация текста и метаданных |
| `kb/parser.py` | Разбор Markdown и YAML-шапки |
| `kb/analysis.py` | Показатели документа, оглавление и замечания |
| `kb/textproc.py` | Токенизация, стоп-слова и стемминг |
| `kb/index.py` | Обратный индекс |
| `kb/scoring.py` | Методы скоринга и их реестр |
| `kb/highlight.py` | Фрагменты выдачи с подсветкой |
| `kb/search.py` | Поиск, фильтры, сортировка и постраничная выдача |
| `kb/importer.py` | Импорт каталога |
| `kb/cli.py` | Командная строка |
| `kb/config.py` | Настройки из окружения |
| `kb/storage/` | Таблицы и репозиторий документов с версиями |
| `kb/cache.py` | Кэш выдачи: Redis, память, пустой кэш |
| `kb/service.py` | Сервис: согласует хранилище, индексы пользователей и кэш, проверяет доступ |
| `kb/security.py` | Хэши паролей, токены доступа и разовые токены |
| `kb/auth.py` | Регистрация, вход, восстановление пароля |
| `kb/ratelimit.py` | Ограничение числа попыток |
| `kb/mailer.py` | Отправка писем |
| `kb/api/` | REST API: методы, схемы, обработка ошибок, журнал запросов |
| `kb/web/` | Веб-интерфейс без сборки: страница, скрипт и стили |

## Проверка

```bash
pytest --cov=kb --cov-report=term-missing
ruff check src tests tools
mypy
python tools/bench.py --docs 2000
python tools/bench_service.py
python tools/bench_scale.py
python tools/ui_smoke.py
python tools/api_smoke.py
python tools/security_smoke.py
python tools/bench_load.py
```

Тесты на настоящих PostgreSQL и Redis включаются переменными `KB_TEST_DATABASE_URL` и
`KB_TEST_REDIS_URL`, без них они пропускаются. Схемы пересобираются командами
`python tools/diagrams.py` и `python tools/diagrams_service.py`.

В каталоге `runs/` лежит вывод реальных прогонов тестов, линтера, отладчика и замеров. Замеры
выполнены на синтетическом корпусе из `tools/bench.py`: словарь в нём меньше, чем в настоящих
текстах, поэтому выигрыш от кэша стемминга на реальных документах будет ниже.
