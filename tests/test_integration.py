"""Интеграционные проверки: импорт каталога, индексация и поиск вместе."""

from pathlib import Path

import pytest

from kb.cli import main
from kb.importer import import_directory
from kb.search import SearchEngine, SearchFilters, SearchQuery

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def top(engine: SearchEngine, text: str, **kwargs) -> str:
    return engine.search(SearchQuery(text, **kwargs)).hits[0].doc_id


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("индексы в базе данных", "postgresql-indexes.md"),
        ("насыщение частоты термина", "bm25-ranking.md"),
        ("устаревшие данные кэша", "redis-cache.md"),
        ("healthcheck контейнера", "docker-compose-notes.md"),
        ("найти коммит с ошибкой", "git-workflow.md"),
        ("покрытие кода тестами", "python-testing.md"),
        ("dependency injection", "fastapi-basics.md"),
    ],
)
@pytest.mark.parametrize("scorer", ["bm25", "tfidf", "hybrid"])
def test_relevant_document_is_first(samples_engine, text, expected, scorer):
    assert top(samples_engine, text, scorer=scorer) == expected


def test_sample_document_without_metadata_gets_title_from_heading(samples_engine):
    document = samples_engine.get_document("ideas-without-metadata.md")
    assert document.metadata.title == "Идеи для базы знаний"
    assert document.metadata.tags == ()


def test_filter_by_tag_on_samples(samples_engine):
    result = samples_engine.search(SearchQuery(filters=SearchFilters(tags=("производительность",))))
    assert {hit.doc_id for hit in result.hits} == {"postgresql-indexes.md", "redis-cache.md"}


def test_import_skips_bad_files_and_keeps_good_ones(tmp_path):
    (tmp_path / "good.md").write_text("# Хороший\n\nТекст про поиск.", encoding="utf-8")
    (tmp_path / "empty.md").write_text("   ", encoding="utf-8")
    (tmp_path / "cp1251.md").write_bytes("# Плохая кодировка".encode("cp1251"))
    (tmp_path / "yaml.md").write_text("---\ntags: [\n---\nТекст", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("не Markdown", encoding="utf-8")
    sub = tmp_path / "папка"
    sub.mkdir()
    (sub / "nested.md").write_text("Вложенный документ про поиск.", encoding="utf-8")

    engine = SearchEngine()
    report = import_directory(engine, tmp_path)

    assert sorted(report.imported) == ["good.md", "папка/nested.md"]
    assert {f.path: f.code for f in report.failed} == {
        "empty.md": "empty_document",
        "cp1251.md": "invalid_encoding",
        "yaml.md": "invalid_front_matter",
    }
    assert engine.get_document("папка/nested.md").metadata.title == "nested"
    assert engine.search(SearchQuery("поиск")).total == 2


def test_import_records_unreadable_file(tmp_path, monkeypatch):
    path = tmp_path / "a.md"
    path.write_text("Текст", encoding="utf-8")

    def fail(self):
        raise PermissionError("нет доступа")

    monkeypatch.setattr(Path, "read_bytes", fail)
    report = import_directory(SearchEngine(), tmp_path)
    assert [(f.path, f.code) for f in report.failed] == [("a.md", "read_error")]


def test_cli_search(capsys):
    code = main(["search", str(SAMPLES), "индексы", "--scorer", "tfidf", "--limit", "1"])
    output = capsys.readouterr().out
    assert code == 0
    assert "Индексы в PostgreSQL" in output
    assert "<mark>" in output


def test_cli_search_with_filters(capsys):
    code = main(
        [
            "search", str(SAMPLES), "--tag", "Git", "--category", "инструменты", "--author",
            "балашов е. р.", "--from", "2026-01-01", "--to", "2026-12-31", "--sort", "created",
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "Найдено: 1" in output


def test_cli_check_and_stats(capsys):
    assert main(["check", str(SAMPLES)]) == 0
    assert "пропущен уровень заголовка" in capsys.readouterr().out
    assert main(["stats", str(SAMPLES)]) == 0
    assert "Документов: 8" in capsys.readouterr().out


def test_cli_check_reports_rejected_files(tmp_path, capsys):
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    assert main(["check", str(tmp_path)]) == 1
    assert "[empty_document]" in capsys.readouterr().err


def test_cli_reports_application_errors(capsys):
    assert main(["search", str(SAMPLES), "поиск", "--limit", "0"]) == 2
    assert "limit" in capsys.readouterr().err
