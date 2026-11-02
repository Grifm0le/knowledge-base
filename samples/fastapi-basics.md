---
title: FastAPI Basics
tags: [python, api, backend]
category: Development
author: Balashov E.
created: 2026-10-20
---

# FastAPI Basics

FastAPI builds REST APIs from type hints and generates OpenAPI documentation automatically. Request
bodies are validated by Pydantic models, so handlers receive clean data.

## Dependency injection

The `Depends` function provides shared resources such as database sessions. Dependencies are easy to
replace in tests, which keeps integration tests fast.

```python
@app.get("/search")
def search(q: str, engine: SearchEngine = Depends(get_engine)) -> SearchResponse:
    return engine.search(SearchQuery(text=q))
```

The interactive documentation is available at `/docs` after the server starts.
