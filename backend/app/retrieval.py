"""Local retrieval. A missing local embedding model never causes a network download."""
from collections import Counter
from functools import lru_cache
import math
import os
from pathlib import Path
import re

# Remove only common query scaffolding, not domain terms such as 签证/居留/出境.
_QUERY_FILLER = re.compile(r"请问|请告诉我|如何|怎么|什么|哪些|是否|可以|应当|有关|相关|规定|法律|法规|根据|的|了|吗|呢|和|与")


def tokenize(text: str, query: bool = False) -> list[str]:
    text = text.lower()
    if query:
        text = _QUERY_FILLER.sub(" ", text)
    result = re.findall(r"[a-z0-9][a-z0-9_-]*", text)
    for word in re.findall(r"[\u3400-\u9fff]+", text):
        if len(word) == 1:
            continue
        result.extend(word[index:index + 2] for index in range(len(word) - 1))
        if len(word) <= 12:
            result.append(word)
    return result


@lru_cache(maxsize=2)
def _load_local_model(directory: str):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(directory, local_files_only=True, trust_remote_code=False)


def _semantic_model():
    configured = os.environ.get("NEXT_NIA_EMBEDDING_MODEL_DIR", "").strip()
    if not configured:
        return None, "本地 BM25 与字词精确匹配融合检索；未配置本地语义模型。"
    directory = Path(configured).expanduser()
    if not directory.is_absolute() or not directory.is_dir():
        return None, "本地语义模型目录无效，当前使用词法检索；未联网下载模型。"
    try:
        return _load_local_model(str(directory.resolve())), "本地 BM25、字词精确匹配与本地语义向量融合检索。"
    except Exception:
        # Model errors can include local paths. Keep the public response generic.
        return None, "本地语义模型未能加载，当前使用词法检索；请检查可选依赖和完整模型文件。"


def retrieval_status() -> dict:
    model, message = _semantic_model()
    return {"mode": "hybrid" if model is not None else "lexical", "message": message}


def rank_sources(query: str, sources: list[dict], limit: int = 8) -> tuple[list[dict], dict]:
    model, message = _semantic_model()
    status = {"mode": "hybrid" if model is not None else "lexical", "message": message}
    query_terms = set(tokenize(query, query=True))
    if not sources or not query_terms:
        return [], status
    contents = [f"{item['title']} {item['article']} {item['text']}" for item in sources]
    counters = [Counter(tokenize(content)) for content in contents]
    lengths = [sum(counts.values()) for counts in counters]
    average = max(sum(lengths) / len(lengths), 1)
    frequencies = Counter(term for counts in counters for term in query_terms if term in counts)
    bm25, exact = {}, {}
    normalized_query = re.sub(r"\s+", "", _QUERY_FILLER.sub("", query.lower()))
    for index, (counts, length, content) in enumerate(zip(counters, lengths, contents)):
        common = query_terms.intersection(counts)
        if not common:
            continue
        # One coincidental bigram in a longer query is not enough evidence.
        if len(query_terms) >= 5 and len(common) < 2:
            continue
        score = 0.0
        for term in common:
            frequency = counts[term]
            inverse = math.log(1 + (len(sources) - frequencies[term] + .5) / (frequencies[term] + .5))
            score += inverse * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average))
        bm25[index] = score
        exact[index] = len(common) / len(query_terms)
        if normalized_query and normalized_query in re.sub(r"\s+", "", content.lower()):
            exact[index] += 2
    rankings = [sorted(bm25, key=bm25.get, reverse=True), sorted(exact, key=exact.get, reverse=True)]
    if model is not None:
        try:
            # Embeddings are intentionally request-local; no document cache can
            # survive an environment change or retain archived/private material.
            vectors = model.encode([query, *contents], normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
            similarities = vectors[1:] @ vectors[0]
            dense = {index: float(score) for index, score in enumerate(similarities) if float(score) >= .60}
            rankings.append(sorted(dense, key=dense.get, reverse=True))
        except Exception:
            status = {"mode": "lexical", "message": "本地语义模型推理失败，本次已使用 BM25 与字词精确匹配；未调用外部接口。"}
    combined: dict[int, float] = {}
    for ranking in rankings:
        for position, index in enumerate(ranking):
            combined[index] = combined.get(index, 0.0) + 1.0 / (60 + position + 1)
    ordered = sorted(combined, key=lambda index: (-combined[index], sources[index]["id"]))[:limit]
    return [{**sources[index], "score": round(combined[index], 6)} for index in ordered], status
