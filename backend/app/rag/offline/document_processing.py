"""OFFLINE source parsing and structure-aware Parent/Child chunk construction."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
import re
import unicodedata

from .chunking import (
    ChunkConfig,
    TokenSpanTokenizer,
    TokenWindow,
    UnicodeTokenSpanTokenizer,
    token_windows,
)


class OCRRequiredError(ValueError):
    """Raised when a PDF has no usable text layer and needs an explicit OCR job."""


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    source_id: str
    source_version: str
    title: str
    source_url: str
    mime_type: str
    content: bytes
    content_start: str | None = None
    content_end: str | None = None

    @property
    def checksum(self) -> str:
        return sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class ParentSection:
    parent_chunk_id: str
    heading: str
    text: str
    source_id: str
    source_version: str
    source_checksum: str
    source_url: str
    source_title: str
    parse_version: str = "structure-semantic-context-v3"


@dataclass(frozen=True, slots=True)
class SourceChunk:
    child_chunk_id: str
    parent_chunk_id: str
    text: str
    contextual_text: str
    semantic_unit_id: str
    semantic_title: str
    semantic_type: str
    split_strategy: str
    retrieval_aliases: tuple[str, ...]
    ordinal: int
    start_char: int
    end_char: int
    token_count: int
    overlap_tokens: int
    tokenizer: str
    chunking_version: str


@dataclass(frozen=True, slots=True)
class SemanticUnit:
    semantic_unit_id: str
    title: str
    text: str
    unit_type: str
    start_char: int
    end_char: int


class _VisibleTextParser(HTMLParser):
    _BLOCKED = {"script", "style", "noscript", "svg"}
    _BLOCKS = {"p", "li", "tr", "div", "section", "article", "br"}

    def __init__(self) -> None:
        super().__init__()
        self._blocked = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag in self._BLOCKED:
            self._blocked += 1
        if not self._blocked and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif not self._blocked and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCKED and self._blocked:
            self._blocked -= 1
        elif not self._blocked and tag in self._BLOCKS | {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._blocked and data.strip():
            self.parts.append(re.sub(r"\s+", " ", data.strip()))


def extract_text(artifact: SourceArtifact) -> str:
    """Prefer embedded text; never start OCR implicitly or hide its provenance."""

    mime = artifact.mime_type.lower().split(";", 1)[0]
    if mime in {"text/plain", "text/markdown"}:
        return artifact.content.decode("utf-8").strip()
    if mime in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleTextParser()
        parser.feed(artifact.content.decode("utf-8", errors="replace"))
        return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
    if mime == "application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - optional ingestion dependency
            raise RuntimeError("PDF extraction requires pypdf; OCR remains a separate stage") from exc
        pages = []
        for index, page in enumerate(PdfReader(BytesIO(artifact.content)).pages, start=1):
            page_text = (page.extract_text() or "").strip()
            if page_text:
                pages.append(f"## 第{index}页\n{page_text}")
        text = "\n\n".join(pages)
        if not text.strip():
            raise OCRRequiredError(
                f"{artifact.source_id}@{artifact.source_version} has no usable PDF text layer"
            )
        return text
    raise ValueError(f"unsupported source mime type: {artifact.mime_type}")


def normalize_document_text(text: str) -> str:
    """Canonicalise layout noise without rewriting the source's meaning."""

    normalized = unicodedata.normalize("NFKC", text).replace("\u00a0", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n[ \t]+", "\n", normalized)
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def scope_document_text(artifact: SourceArtifact, text: str) -> str:
    """Select the registered article body while keeping the raw snapshot intact."""

    scoped = text
    if artifact.content_start:
        marker = normalize_document_text(artifact.content_start)
        start = scoped.find(marker)
        if start < 0:
            raise ValueError(f"content_start marker not found for {artifact.source_id}: {marker}")
        scoped = scoped[start:]
    if artifact.content_end:
        marker = normalize_document_text(artifact.content_end)
        end = scoped.find(marker)
        if end < 0:
            raise ValueError(f"content_end marker not found for {artifact.source_id}: {marker}")
        scoped = scoped[:end]
    return scoped.strip()


_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+.+")
_ARTICLE_HEADING = re.compile(r"^第[一二三四五六七八九十百0-9]+[章节条]\s*.+")
_NUMBERED_HEADING = re.compile(r"^[一二三四五六七八九十]+、.+")
_KNOWN_SECTION_HEADINGS = {"办理所需材料", "申请材料", "办理材料", "温馨提示"}


def _is_heading(line: str) -> bool:
    """Recognise structure without treating numbered checklist rows as titles."""

    if line in _KNOWN_SECTION_HEADINGS:
        return True
    if _MARKDOWN_HEADING.match(line):
        return True
    if _ARTICLE_HEADING.match(line):
        return len(line) <= 120
    if _NUMBERED_HEADING.match(line):
        return len(line) <= 80 and not line.endswith(("。", "；", ";"))
    return False


def split_parent_sections(artifact: SourceArtifact, text: str) -> list[ParentSection]:
    """Split on headings first and assign stable, version-aware parent IDs."""

    sections: list[tuple[str, list[str]]] = []
    heading = "正文"
    body: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _is_heading(line):
            if body:
                sections.append((heading, body))
            heading, body = line.lstrip("# "), []
        else:
            body.append(line)
    if body:
        sections.append((heading, body))
    if not sections and text.strip():
        sections.append(("正文", [text.strip()]))

    output: list[ParentSection] = []
    for index, (title, lines) in enumerate(sections, start=1):
        section_text = "\n".join(lines)
        identity = f"{artifact.source_id}|{artifact.source_version}|{artifact.checksum}|{index}|{title}"
        output.append(ParentSection(
            parent_chunk_id=f"PARENT-{sha256(identity.encode()).hexdigest()[:16].upper()}",
            heading=title,
            text=section_text,
            source_id=artifact.source_id,
            source_version=artifact.source_version,
            source_checksum=artifact.checksum,
            source_url=artifact.source_url,
            source_title=artifact.title,
        ))
    return output


_LIST_ITEM = re.compile(
    r"^(?:(?:[\(（]?[0-9一二三四五六七八九十]+[\)）\.、])|(?:[-*•]))\s*"
)
_COMPACT_TABLE_ITEM = re.compile(r"^[0-9]{1,2}(?![年月日])(?=[\u3400-\u9fff])")
_SENTENCE = re.compile(r".+?(?:[。！？；]|$)", re.DOTALL)
_ALIAS_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("婚姻", ("婚姻证明", "婚姻状况材料", "结婚证", "离婚证", "单身声明")),
    ("身份证", ("身份证件", "身份证明", "居民身份证")),
    ("户口", ("户口簿", "户籍证明", "居民户口簿")),
    ("产权", ("不动产权证", "房屋所有权证", "产权证明", "抵押物权属证明")),
    ("首付", ("首付款证明", "首付款凭证", "首期款收据")),
    ("营业执照", ("经营资格文件", "经营证照", "主体登记证明")),
    ("经营流水", ("银行结算账户明细", "完税凭证", "经营情况证明")),
    ("购房合同", ("买卖合同", "网签合同", "贷款用途证明")),
)


def _aliases(text: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for trigger, terms in _ALIAS_GROUPS:
        if trigger in text or any(term in text for term in terms):
            aliases.extend(terms)
    return tuple(dict.fromkeys(aliases))


def _semantic_prose_groups(
    text: str,
    *,
    tokenizer: TokenSpanTokenizer,
    target_tokens: int,
) -> list[str]:
    """Pack complete sentences; token windows are reserved for one huge sentence."""

    sentences = [match.group(0).strip() for match in _SENTENCE.finditer(text) if match.group(0).strip()]
    if not sentences:
        return [text]
    groups: list[str] = []
    buffer: list[str] = []
    size = 0
    for sentence in sentences:
        sentence_size = len(tokenizer.offsets(sentence))
        if buffer and size + sentence_size > target_tokens:
            groups.append("".join(buffer))
            buffer, size = [], 0
        buffer.append(sentence)
        size += sentence_size
    if buffer:
        groups.append("".join(buffer))
    return groups


def split_semantic_units(
    parent: ParentSection,
    *,
    tokenizer: TokenSpanTokenizer,
    target_tokens: int = 256,
) -> list[SemanticUnit]:
    """Split a section into checklist clauses or sentence-complete prose groups."""

    raw_units: list[tuple[str, str]] = []
    prose: list[str] = []
    current_item: list[str] = []

    def flush_prose() -> None:
        if prose:
            joined = "\n".join(prose).strip()
            for group in _semantic_prose_groups(
                joined,
                tokenizer=tokenizer,
                target_tokens=target_tokens,
            ):
                raw_units.append(("PROSE", group))
            prose.clear()

    def flush_item() -> None:
        if current_item:
            raw_units.append(("ATOMIC_CLAUSE", "\n".join(current_item).strip()))
            current_item.clear()

    for line in (item.strip() for item in parent.text.splitlines()):
        if not line:
            continue
        starts_item = bool(_LIST_ITEM.match(line) or _COMPACT_TABLE_ITEM.match(line))
        if starts_item:
            flush_prose()
            flush_item()
            current_item.append(line)
        elif current_item:
            current_item.append(line)
        else:
            prose.append(line)
    flush_item()
    flush_prose()

    if not raw_units and parent.text.strip():
        raw_units.append(("PROSE", parent.text.strip()))
    output: list[SemanticUnit] = []
    cursor = 0
    for ordinal, (unit_type, unit_text) in enumerate(raw_units, start=1):
        start = parent.text.find(unit_text, cursor)
        if start < 0:
            start = cursor
        end = start + len(unit_text)
        cursor = end
        first_line = unit_text.splitlines()[0]
        title = first_line[:60] if unit_type == "ATOMIC_CLAUSE" else parent.heading
        identity = f"{parent.parent_chunk_id}|semantic-v1|{ordinal}|{unit_type}|{unit_text}"
        output.append(SemanticUnit(
            semantic_unit_id=f"UNIT-{sha256(identity.encode()).hexdigest()[:16].upper()}",
            title=title,
            text=unit_text,
            unit_type=unit_type,
            start_char=start,
            end_char=end,
        ))
    return output


def split_source_chunks(
    parent: ParentSection,
    *,
    config: ChunkConfig | None = None,
    tokenizer: TokenSpanTokenizer | None = None,
) -> list[SourceChunk]:
    """Sub-split a structural parent using token windows with explicit overlap."""

    resolved_config = config or ChunkConfig()
    resolved_tokenizer = tokenizer or UnicodeTokenSpanTokenizer()
    output: list[SourceChunk] = []
    semantic_units = split_semantic_units(
        parent,
        tokenizer=resolved_tokenizer,
        target_tokens=min(256, resolved_config.max_tokens),
    )
    ordinal = 0
    for unit in semantic_units:
        unit_token_count = len(resolved_tokenizer.offsets(unit.text))
        windows = (
            token_windows(unit.text, config=resolved_config, tokenizer=resolved_tokenizer)
            if unit_token_count > resolved_config.max_tokens
            else []
        )
        if not windows:
            windows = [TokenWindow(
                text=unit.text,
                start_char=0,
                end_char=len(unit.text),
                token_count=unit_token_count,
                overlap_with_previous=0,
            )]
        split_strategy = "TOKEN_WINDOW_FALLBACK" if len(windows) > 1 else "SEMANTIC_UNIT"
        aliases = _aliases(unit.text)
        for window in windows:
            ordinal += 1
            start_char = unit.start_char + window.start_char
            end_char = unit.start_char + window.end_char
            identity = (
                f"{unit.semantic_unit_id}|{resolved_config.chunking_version}|"
                f"{start_char}|{end_char}|{window.text}"
            )
            alias_context = f"；检索同义词：{'、'.join(aliases)}" if aliases else ""
            context = (
                f"文档：{parent.source_title}；章节：{parent.heading}；"
                f"语义单元：{unit.title}{alias_context}\n{window.text}"
            )
            output.append(SourceChunk(
                child_chunk_id=f"CHILD-{sha256(identity.encode()).hexdigest()[:16].upper()}",
                parent_chunk_id=parent.parent_chunk_id,
                text=window.text,
                contextual_text=context,
                semantic_unit_id=unit.semantic_unit_id,
                semantic_title=unit.title,
                semantic_type=unit.unit_type,
                split_strategy=split_strategy,
                retrieval_aliases=aliases,
                ordinal=ordinal,
                start_char=start_char,
                end_char=end_char,
                token_count=window.token_count,
                overlap_tokens=window.overlap_with_previous,
                tokenizer=resolved_tokenizer.name,
                chunking_version=resolved_config.chunking_version,
            ))
    return output


def process_source(
    artifact: SourceArtifact,
    *,
    config: ChunkConfig | None = None,
    tokenizer: TokenSpanTokenizer | None = None,
) -> list[tuple[ParentSection, list[SourceChunk]]]:
    text = scope_document_text(artifact, normalize_document_text(extract_text(artifact)))
    return [
        (parent, split_source_chunks(parent, config=config, tokenizer=tokenizer))
        for parent in split_parent_sections(artifact, text)
    ]
