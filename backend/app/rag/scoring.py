from __future__ import annotations

from collections import Counter
from hashlib import blake2b
from math import log, sqrt
import re


DOMAIN_TERMS = (
    "宅抵贷", "进件材料", "材料清单", "借款人", "抵押人", "配偶", "身份证明",
    "婚姻证明", "房产材料", "合同影像", "面签材料", "征信授权", "同意抵押",
    "补件", "影像归属", "完整性", "生效日期", "清单版本", "直营渠道",
)


def tokenize(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    result = re.findall(r"[a-z0-9_-]+", normalized)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", normalized)
    for run in chinese_runs:
        result.extend(run[index:index + 2] for index in range(max(1, len(run) - 1)))
    result.extend(term for term in DOMAIN_TERMS if term in normalized)
    return result


def hashed_vector(tokens: list[str], dimensions: int = 256) -> list[float]:
    vector = [0.0] * dimensions
    for token, count in Counter(tokens).items():
        digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.0 + log(count))
    norm = sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    return max(0.0, sum(a * b for a, b in zip(left, right, strict=True)))


def bm25_scores(query_tokens: list[str], documents: list[list[str]]) -> list[float]:
    if not documents:
        return []
    document_count = len(documents)
    average_length = sum(len(document) for document in documents) / document_count or 1.0
    document_frequency = Counter(
        token for token in set(query_tokens) for document in documents if token in document
    )
    scores: list[float] = []
    for document in documents:
        frequencies = Counter(document)
        score = 0.0
        for token in set(query_tokens):
            frequency = frequencies[token]
            if not frequency:
                continue
            idf = log(
                1 + (document_count - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * len(document) / average_length
            )
            score += idf * frequency * 2.5 / denominator
        scores.append(score)
    maximum = max(scores) or 1.0
    return [score / maximum for score in scores]
