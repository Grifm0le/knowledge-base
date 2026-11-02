---
title: Индексы в PostgreSQL
tags: [postgresql, базы-данных, производительность]
category: Базы данных
author: Балашов Е. Р.
created: 2026-09-14
---

# Индексы в PostgreSQL

Индекс ускоряет поиск строк, но замедляет запись и занимает место на диске. Прежде чем добавлять
индекс, стоит посмотреть план запроса через `EXPLAIN ANALYZE`.

## Какие бывают индексы

- B-tree подходит для сравнений и сортировки, он создаётся по умолчанию.
- GIN нужен для полнотекстового поиска и массивов.
- BRIN занимает мало места и работает на больших таблицах, где данные лежат по порядку.

## Полнотекстовый поиск

Для поиска по тексту документ приводится к типу `tsvector`, а запрос к типу `tsquery`. Функция
`ts_rank` считает релевантность найденных документов.

```sql
CREATE INDEX idx_docs_search ON documents USING GIN (to_tsvector('russian', body));

SELECT title, ts_rank(to_tsvector('russian', body), query) AS rank
FROM documents, plainto_tsquery('russian', 'индексы поиск') AS query
WHERE to_tsvector('russian', body) @@ query
ORDER BY rank DESC;
```

Подробности есть в [документации PostgreSQL](https://www.postgresql.org/docs/current/textsearch.html).
