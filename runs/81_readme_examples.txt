$ POST /auth/register
{"message":"Если адрес можно использовать, на него отправлено письмо"}
HTTP 202
$ POST /auth/verify-email
HTTP 204

$ POST /documents/import
{
  "created": [
    "postgresql-indexes.md",
    "redis-cache.md",
    "bm25-ranking.md"
  ],
  "updated": [],
  "unchanged": [],
  "failed": []
}

$ GET /search?q=индексы&limit=1
x-cache: MISS
{
  "total": 2,
  "scorer": "bm25",
  "sort": "relevance",
  "limit": 1,
  "offset": 0,
  "hits": [
    {
      "id": "<id>",
      "title": "Индексы в PostgreSQL",
      "score": 0.912977,
      "snippet": "<mark>Индекс</mark> ускоряет поиск строк, но замедляет запись и занимает место на диске. Прежде чем добавлять <mark>индекс</mark>, стоит посмотреть план запроса через EXPLAIN ANALYZE.  B-tree подходит для сравнений и сортировки, он создаётся по умолчанию.  GIN нужен…",
      "tags": [
        "postgresql",
        "базы-данных",
        "производительность"
      ],
      "category": "Базы данных",
      "created": "2026-09-14"
    }
  ]
}

$ GET /search повторно: заголовок кэша
x-cache: HIT

$ GET /search?q=кэш&scorer=hybrid&w_title=10&hybrid_bm25=0.5&hybrid_tfidf=0.5&tags=redis&limit=1
{'total': 1, 'scorer': 'hybrid'} ['Кэширование в Redis']

$ PUT /documents/{id} с expected_version
HTTP 200
$ PUT повторно с той же версией: конфликт
{
  "error": {
    "code": "version_conflict",
    "message": "Ожидалась версия 1, текущая 2"
  }
}

$ GET /documents/{id}/versions
[(2, 'Кэширование в Redis'), (1, 'Кэширование в Redis')]

$ GET /stats
{
  "documents": 3,
  "versions": 4,
  "content_bytes": 5819,
  "indexed_documents": 3,
  "indexed_terms": 179,
  "cache_hits": 3,
  "cache_misses": 23,
  "top_tags": [
    {
      "name": "производительность",
      "count": 2
    },
    {
      "name": "postgresql",
      "count": 1
    },
    {
      "name": "базы-данных",
      "count": 1
    },
    {
      "name": "redis",
      "count": 1
    },
    {
      "name": "кэш",
      "count": 1
    },
    {
      "name": "поиск",
      "count": 1
    },
    {
      "name": "алгоритмы",
      "count": 1
    },
    {
      "name": "ранжирование",
      "count": 1
    }
  ],
  "categories": [
    {
      "name": "Базы данных",
      "count": 2
    },
    {
      "name": "Алгоритмы",
      "count": 1
    }
  ]
}

$ GET /health
{
  "status": "ok",
  "database": "ok",
  "cache": "ok"
}

Учётная запись примера удалена: HTTP 204
