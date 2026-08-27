"""Token-window primitives used only while building the offline corpus.

Production builds use the BGE-M3 tokenizer.  The deterministic Unicode
tokenizer exists for tests and the dependency-light interview demo; its name is
written to the build manifest so it can never be presented as BGE tokenization.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    max_tokens: int = 384
    overlap_tokens: int = 48
    chunking_version: str = "structure-semantic-context-v3"

    def __post_init__(self) -> None:
        if self.max_tokens < 32:
            raise ValueError("max_tokens must be at least 32")
        if self.overlap_tokens < 1:
            raise ValueError("overlap_tokens must be positive")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        ratio = self.overlap_tokens / self.max_tokens
        if not 0.10 <= ratio <= 0.20:
            raise ValueError("overlap must stay within the 10%-20% RAG policy")


class TokenSpanTokenizer(Protocol):
    name: str

    def offsets(self, text: str) -> list[tuple[int, int]]: ...


_TOKEN = re.compile(
    r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:[._:/-][A-Za-z0-9]+)*|[^\s]",
    re.UNICODE,
)


class UnicodeTokenSpanTokenizer:
    """Deterministic local tokenizer for tests and offline demo builds."""

    name = "unicode-cjk-token-span-v1"

    def offsets(self, text: str) -> list[tuple[int, int]]:
        return [(match.start(), match.end()) for match in _TOKEN.finditer(text)]


class HuggingFaceTokenSpanTokenizer:
    """Fast-tokenizer adapter used by real BGE-M3 corpus builds."""

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        self.name = model_name
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover - optional production dependency
            raise RuntimeError(
                "Real offline chunking requires transformers from requirements-integrations.txt"
            ) from exc
        self._tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
        if not self._tokenizer.is_fast:
            raise RuntimeError(f"{model_name} must expose token offset mappings")

    def offsets(self, text: str) -> list[tuple[int, int]]:
        encoded = self._tokenizer(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            truncation=False,
        )
        return [(int(start), int(end)) for start, end in encoded["offset_mapping"] if end > start]


@dataclass(frozen=True, slots=True)
class TokenWindow:
    text: str
    start_char: int
    end_char: int
    token_count: int
    overlap_with_previous: int


def token_windows(
    text: str,
    *,
    config: ChunkConfig,
    tokenizer: TokenSpanTokenizer,
) -> list[TokenWindow]:
    """Create exact token windows while retaining 10%-20% boundary overlap."""

    offsets = tokenizer.offsets(text)
    if not offsets:
        return []
    output: list[TokenWindow] = []
    start_token = 0
    while start_token < len(offsets):
        end_token = min(len(offsets), start_token + config.max_tokens)
        start_char = offsets[start_token][0]
        end_char = offsets[end_token - 1][1]
        output.append(TokenWindow(
            text=text[start_char:end_char].strip(),
            start_char=start_char,
            end_char=end_char,
            token_count=end_token - start_token,
            overlap_with_previous=(0 if not output else min(config.overlap_tokens, end_token - start_token)),
        ))
        if end_token == len(offsets):
            break
        start_token = end_token - config.overlap_tokens
    return output
