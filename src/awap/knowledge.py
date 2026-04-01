"""Local knowledge and retrieval helpers."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any

from awap.domain import KnowledgeChunk

TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+")


def chunk_text(content: str, *, max_words: int = 80, overlap_words: int = 16) -> list[str]:
    words = content.split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(len(words), start + max_words)
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = max(end - overlap_words, start + 1)
    return chunks


def embed_text(text: str, *, dimensions: int = 64) -> list[float]:
    vector = [0.0] * dimensions
    tokens = tokenize(text)
    if not tokens:
        return vector
    for token, count in Counter(tokens).items():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for index in range(4):
            bucket = digest[index] % dimensions
            sign = 1.0 if digest[index + 4] % 2 == 0 else -1.0
            vector[bucket] += sign * float(count)
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=False))


def rerank_chunks(
    query: str,
    chunks: list[KnowledgeChunk],
    *,
    embedding_vectors: dict[str, list[float]],
    top_k: int = 5,
) -> list[KnowledgeChunk]:
    query_embedding = embed_text(query)
    query_tokens = set(tokenize(query))
    ranked: list[KnowledgeChunk] = []
    for chunk in chunks:
        embedding_score = cosine_similarity(query_embedding, embedding_vectors.get(chunk.id, []))
        lexical_overlap = len(query_tokens.intersection(tokenize(chunk.content)))
        score = embedding_score + (0.05 * lexical_overlap)
        ranked.append(chunk.model_copy(update={"score": score}))
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked[:top_k]


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]


def citation_for_chunk(title: str, chunk_index: int, metadata: dict[str, Any]) -> str:
    source = str(metadata.get("source") or "").strip()
    if source and source != title:
        return f"{title} [{source}]#chunk-{chunk_index + 1}"
    return f"{title}#chunk-{chunk_index + 1}"
