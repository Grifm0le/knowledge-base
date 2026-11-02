"""Сохраняет описание API в docs/openapi.json: python tools/export_openapi.py."""

from __future__ import annotations

import json
from pathlib import Path

from kb.api.app import create_app
from kb.config import Settings

TARGET = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def main() -> None:
    # Описание строится без запуска сервиса, поэтому ключ здесь ни на что не влияет.
    settings = Settings(_env_file=None, jwt_secret="x" * 32)
    schema = create_app(settings).openapi()
    TARGET.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Методов: {sum(len(item) for item in schema['paths'].values())}, файл: {TARGET.name}")


if __name__ == "__main__":
    main()
