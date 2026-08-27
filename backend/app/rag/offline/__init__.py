"""OFFLINE RAG: crawl, parse, chunk, validate and build index inputs."""

from .chunking import ChunkConfig, HuggingFaceTokenSpanTokenizer, UnicodeTokenSpanTokenizer
from .contextualization import (
    ContextRequest,
    DeterministicContextualizer,
    JsonContextCache,
    QwenVllmContextualizer,
    GatewayContextualizer,
    configured_contextualizer,
    load_contextualization_prompt,
)
from .document_processing import (
    OCRRequiredError,
    ParentSection,
    SourceArtifact,
    SourceChunk,
    SemanticUnit,
    extract_text,
    normalize_document_text,
    scope_document_text,
    process_source,
    split_parent_sections,
    split_semantic_units,
    split_source_chunks,
)
from .source_registry import SourceSpec, load_source_registry

__all__ = [
    "ChunkConfig",
    "ContextRequest",
    "DeterministicContextualizer",
    "HuggingFaceTokenSpanTokenizer",
    "JsonContextCache",
    "OCRRequiredError",
    "ParentSection",
    "QwenVllmContextualizer",
    "GatewayContextualizer",
    "SourceArtifact",
    "SourceChunk",
    "SemanticUnit",
    "SourceSpec",
    "UnicodeTokenSpanTokenizer",
    "extract_text",
    "configured_contextualizer",
    "load_contextualization_prompt",
    "normalize_document_text",
    "scope_document_text",
    "load_source_registry",
    "process_source",
    "split_parent_sections",
    "split_semantic_units",
    "split_source_chunks",
]
