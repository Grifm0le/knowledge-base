"""Разбор Markdown: front matter, заголовки, код, ссылки и текст для индекса."""

from __future__ import annotations

import logging
import re

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from kb.errors import DocumentValidationError
from kb.models import CodeBlock, Heading, Link, Metadata, ParsedDocument
from kb.normalize import normalize_date, normalize_string, normalize_tags, normalize_text

logger = logging.getLogger(__name__)

UNTITLED = "Без названия"

_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)^---[ \t]*(?:\n|\Z)", re.DOTALL | re.MULTILINE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

_markdown = MarkdownIt("commonmark").enable("table")
# Для показа пользователю: HTML из текста экранируется, ссылки вида javascript: отбрасываются.
_safe_markdown = MarkdownIt("commonmark", {"html": False}).enable("table")


def split_front_matter(text: str) -> tuple[dict[str, object], str, int]:
    """Отделяет YAML-шапку. Возвращает метаданные, тело и число строк шапки."""
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        return {}, text, 0
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise DocumentValidationError("invalid_front_matter", f"Ошибка YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise DocumentValidationError(
            "invalid_front_matter", "Шапка документа должна быть набором полей «ключ: значение»"
        )
    return data, text[match.end() :], match.group(0).count("\n")


def _inline_text(token: Token, links: list[Link]) -> str:
    """Текст строчного токена. Заодно собирает ссылки из него."""
    parts: list[str] = []
    href: str | None = None
    link_parts: list[str] = []
    for child in token.children or []:
        if child.type in ("text", "code_inline"):
            chunk = child.content
        elif child.type in ("softbreak", "hardbreak"):
            chunk = " "
        elif child.type == "image":
            chunk = child.content
        elif child.type == "link_open":
            href = str(child.attrGet("href") or "")
            link_parts = []
            continue
        elif child.type == "link_close":
            links.append(Link("".join(link_parts).strip(), href or ""))
            href = None
            continue
        else:
            continue
        parts.append(chunk)
        if href is not None:
            link_parts.append(chunk)
    return "".join(parts).strip()


def parse_document(raw: str, *, fallback_title: str | None = None) -> ParsedDocument:
    """Разбирает и проверяет документ. При нарушении правил — DocumentValidationError."""
    source = normalize_text(raw)
    front, body_source, offset = split_front_matter(source)
    if not body_source.strip():
        raise DocumentValidationError("empty_document", "В документе нет текста")

    headings: list[Heading] = []
    code_blocks: list[CodeBlock] = []
    links: list[Link] = []
    blocks: list[str] = []

    tokens = _markdown.parse(body_source)
    for position, token in enumerate(tokens):
        line = (token.map[0] if token.map else 0) + offset + 1
        if token.type == "heading_open":
            text = _inline_text(tokens[position + 1], links)
            headings.append(Heading(int(token.tag[1]), text, line))
        elif token.type == "inline":
            if tokens[position - 1].type != "heading_open":
                text = _inline_text(token, links)
                if text:
                    blocks.append(text)
        elif token.type == "fence":
            language = token.info.split()[0] if token.info.strip() else None
            code_blocks.append(CodeBlock(language, token.content, line))
        elif token.type == "code_block":
            code_blocks.append(CodeBlock(None, token.content, line))
        elif token.type == "html_block":
            text = " ".join(_HTML_TAG_RE.sub(" ", token.content).split())
            if text:
                blocks.append(text)

    title = normalize_string(front.get("title"), "title")
    if title is None:
        first_h1 = next((h.text for h in headings if h.level == 1 and h.text), None)
        title = first_h1 or fallback_title or UNTITLED

    metadata = Metadata(
        title=title,
        tags=normalize_tags(front.get("tags")),
        category=normalize_string(front.get("category"), "category"),
        author=normalize_string(front.get("author"), "author"),
        created=normalize_date(front.get("created")),
    )
    logger.debug(
        "Разобран документ %r: заголовков %d, блоков текста %d, блоков кода %d, ссылок %d",
        title,
        len(headings),
        len(blocks),
        len(code_blocks),
        len(links),
    )
    return ParsedDocument(
        metadata=metadata,
        body="\n\n".join(blocks),
        source=source,
        headings=tuple(headings),
        code_blocks=tuple(code_blocks),
        links=tuple(links),
    )


def render_html(text: str) -> str:
    """Безопасный HTML документа без шапки: сервис показывает его в браузере."""
    _, body, _ = split_front_matter(normalize_text(text))
    return str(_safe_markdown.render(body))
