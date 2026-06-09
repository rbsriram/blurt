"""Request/response models.

Validation rejects empty, whitespace-only, and null-byte content with 422, but
never mutates what gets stored: trailing spaces, tabs, and newlines are
preserved verbatim. Oversize content is handled in the route (413), not here, so
the distinction between "too big" and "malformed" stays meaningful.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _validate_content(v: str) -> str:
    if "\x00" in v:
        raise ValueError("content may not contain null bytes")
    if not v.strip():
        raise ValueError("content may not be empty or whitespace-only")
    return v


class EntryCreate(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def _check(cls, v: str) -> str:
        return _validate_content(v)


class EntryUpdate(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def _check(cls, v: str) -> str:
        return _validate_content(v)


class CheckboxToggle(BaseModel):
    # The 0-based ordinal of the checkbox within the note, counted top-to-bottom.
    index: int = Field(ge=0)
    checked: bool


class SuggestRequest(BaseModel):
    text: str = ""


class QueryRequest(BaseModel):
    query: str

    @field_validator("query")
    @classmethod
    def _check(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query may not be empty")
        return v


class SynthesizeRequest(BaseModel):
    query: str
    entry_ids: list[int] = []


class NotesDirRequest(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def _check(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path may not be empty")
        return v


class SecretCreate(BaseModel):
    label: str   # the visible, searchable description ("gmail password")
    value: str   # the secret itself; encrypted at rest, never indexed or mirrored

    @field_validator("label")
    @classmethod
    def _check_label(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("label may not be empty")
        return v

    @field_validator("value")
    @classmethod
    def _check_value(cls, v: str) -> str:
        if not v:
            raise ValueError("secret value may not be empty")
        return v


class DateFormatRequest(BaseModel):
    order: str  # "DMY" (day-first) or "MDY" (month-first)

    @field_validator("order")
    @classmethod
    def _check(cls, v: str) -> str:
        if v not in ("DMY", "MDY"):
            raise ValueError("order must be 'DMY' or 'MDY'")
        return v
