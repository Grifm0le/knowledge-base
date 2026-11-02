"""Обработка текста: токенизация, стоп-слова и стемминг для русского и английского."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock

import snowballstemmer

STEM_CACHE_SIZE = 200_000

_WORD_RE = re.compile(r"[^\W_]+")
_CYRILLIC_RE = re.compile(r"[а-я]")

_RUSSIAN = snowballstemmer.stemmer("russian")
_ENGLISH = snowballstemmer.stemmer("english")
# Стеммеры Snowball хранят состояние в объекте, поэтому один объект нельзя вызывать из двух потоков.
_stemmer_lock = Lock()

STOP_WORDS = frozenset(
    # Длинный список читается строкой, а не литералом на сотню строк.
    """
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее
    мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был
    него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней
    для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того
    потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем
    всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего
    них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя
    такой им более всегда конечно всю между
    a an and are as at be but by for if in into is it no not of on or such that the their then
    there these they this to was will with
    """.split()  # noqa: SIM905
)


@dataclass(frozen=True, slots=True)
class Token:
    """Термин индекса и положение исходного слова в тексте."""

    term: str
    start: int
    end: int


def normalize_word(word: str) -> str:
    """Приводит слово к единому виду: регистр и «ё» не должны влиять на поиск."""
    return word.casefold().replace("ё", "е")


@lru_cache(maxsize=STEM_CACHE_SIZE)
def stem(word: str) -> str:
    """Основа слова. Стеммер выбирается по алфавиту, числа остаются как есть.

    Слова в текстах часто повторяются, поэтому результат кэшируется: стемминг
    занимал основную часть времени индексации.
    """
    if word.isdigit():
        return word
    stemmer = _RUSSIAN if _CYRILLIC_RE.search(word) else _ENGLISH
    with _stemmer_lock:
        return stemmer.stemWord(word)


def tokenize(text: str) -> Iterator[Token]:
    """Разбивает текст на термины, сохраняя позиции слов для подсветки."""
    for match in _WORD_RE.finditer(text):
        word = normalize_word(match.group())
        if word not in STOP_WORDS:
            yield Token(stem(word), match.start(), match.end())


def terms(text: str) -> list[str]:
    """Только термины, без позиций: для индексации и разбора запроса."""
    normalized = (normalize_word(word) for word in _WORD_RE.findall(text))
    return [stem(word) for word in normalized if word not in STOP_WORDS]
