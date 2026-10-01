"""Bounded learning content paired with the unchanged source text and reading."""

import json
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ShortText = Annotated[str, Field(min_length=1, max_length=200)]
MAX_LEARNING_BYTES = 512 * 1024


class LearningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str) and any(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in value):
            raise ValueError("Learning text contains an unsupported control character")
        return value


class GlossaryEntry(LearningModel):
    term: str = Field(min_length=1, max_length=80)
    reading: ShortText
    meaning: str = Field(min_length=1, max_length=1000)


class LearningCheck(LearningModel):
    question: str = Field(min_length=1, max_length=1000)
    choices: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(min_length=2, max_length=5)
    answer_index: int = Field(ge=0, strict=True)
    explanation: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def valid_answer(self):
        if self.answer_index >= len(self.choices):
            raise ValueError("Answer index must identify an existing choice")
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("Check choices must be distinct")
        return self


class LearningUnit(LearningModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    title: ShortText
    original: str = Field(min_length=1, max_length=12000)
    pinyin: str = Field(min_length=1, max_length=24000)
    opening_question: str = Field(min_length=1, max_length=1000)
    glossary: list[GlossaryEntry] = Field(min_length=1, max_length=16)
    translation: str = Field(min_length=1, max_length=12000)
    summary: str = Field(min_length=1, max_length=2000)
    everyday_example: str = Field(min_length=1, max_length=4000)
    check: LearningCheck

    @model_validator(mode="after")
    def glossary_comes_from_unit(self):
        original = source_characters(self.original)
        if not original or not pinyin_syllables(self.pinyin):
            raise ValueError("A learning unit needs source text and pinyin")
        if len(original) != len(pinyin_syllables(self.pinyin)):
            raise ValueError("Each source character needs its corresponding pinyin syllable")
        terms = [source_characters(entry.term) for entry in self.glossary]
        if any(not term or term not in original for term in terms):
            raise ValueError("Glossary terms must occur in the learning unit")
        if len(set(terms)) != len(terms):
            raise ValueError("Glossary terms must be distinct within a unit")
        return self


class BiteLearning(LearningModel):
    version: Literal[1]
    source_note: str = Field(min_length=1, max_length=4000)
    units: list[LearningUnit] = Field(min_length=1, max_length=64)

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Learning version must be the integer 1")
        return value

    @model_validator(mode="after")
    def bounded_distinct_units(self):
        if len({unit.id for unit in self.units}) != len(self.units):
            raise ValueError("Learning unit IDs must be unique within a bite")
        if len(self.model_dump_json().encode("utf-8")) > MAX_LEARNING_BYTES:
            raise ValueError("Learning content exceeds the byte limit")
        return self


def source_characters(value):
    """Ignore only layout and punctuation, retaining every source character."""
    return "".join(char for char in unicodedata.normalize("NFC", value)
                   if not char.isspace() and not unicodedata.category(char).startswith("P"))


def pinyin_syllables(value):
    """Preserve syllable boundaries and tone marks; tolerate case and punctuation."""
    words, current = [], []
    for char in unicodedata.normalize("NFC", value).casefold():
        if char.isspace() or unicodedata.category(char).startswith("P"):
            if current:
                words.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        words.append("".join(current))
    return words


def decode_learning(value):
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)):
        value = json.loads(value)
    return value


def validate_learning(value, original, pinyin):
    """Validate both structure and lossless source/reading segmentation."""
    if value is None:
        return None
    learning = BiteLearning.model_validate(decode_learning(value))
    if "".join(source_characters(unit.original) for unit in learning.units) != source_characters(original):
        raise ValueError("Learning units do not preserve the bite source text")
    if [part for unit in learning.units for part in pinyin_syllables(unit.pinyin)] != pinyin_syllables(pinyin):
        raise ValueError("Learning units do not preserve the bite pinyin sequence")
    return learning.model_dump(mode="json")
