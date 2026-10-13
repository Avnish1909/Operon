"""Company knowledge base: SOPs and policies the operator learns how the company works from.

Documents are Markdown with a small front-matter block (title, summary, tags). They
are chunked by section and searched with BM25 — small, dependency-free and good
enough for a handbook. A production deployment would swap in the company's
document store and an embedding index behind the same two calls: search() and read().
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

_STOP = set("""a an and are as at be by for from has have how i if in is it its of on or our
that the their then there these this to was we what when where which who will with you your
do does not no can may must should""".split())


def tokenize(text: str) -> list[str]:
    tokens = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in _STOP:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(word) > 4 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
        tokens.append(word)
    return tokens


@dataclass
class Document:
    id: str
    title: str
    summary: str
    tags: list[str]
    body: str


@dataclass
class Chunk:
    doc_id: str
    heading: str
    text: str
    tokens: list[str]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75) -> None:
        self.docs, self.k1, self.b = docs, k1, b
        self.avgdl = sum(map(len, docs)) / max(len(docs), 1)
        df: Counter = Counter()
        for doc in docs:
            df.update(set(doc))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for doc, tf in zip(self.docs, self.tf):
            score = 0.0
            for term in query:
                if term not in tf:
                    continue
                freq = tf[term]
                score += self.idf[term] * freq * (self.k1 + 1) / (
                    freq + self.k1 * (1 - self.b + self.b * len(doc) / self.avgdl))
            out.append(score)
        return out


class KnowledgeBase:
    def __init__(self, company_dir: Path, url_map: dict[str, str] | None = None) -> None:
        self.docs: dict[str, Document] = {}
        self.chunks: list[Chunk] = []
        for path in sorted((company_dir / "handbook").glob("*.md")):
            doc = self._load(path, url_map or {})
            self.docs[doc.id] = doc
            self.chunks.extend(self._chunk(doc))
        self.index = BM25([c.tokens for c in self.chunks])

    @staticmethod
    def _load(path: Path, url_map: dict[str, str]) -> Document:
        raw = path.read_text(encoding="utf-8")
        meta, body = {}, raw
        if raw.startswith("---"):
            _, front, body = raw.split("---", 2)
            meta = yaml.safe_load(front) or {}
        for key, url in url_map.items():
            body = body.replace("{" + key + "}", url)
        return Document(id=path.stem, title=meta.get("title", path.stem), summary=meta.get("summary", ""),
                        tags=list(meta.get("tags", [])), body=body.strip())

    @staticmethod
    def _chunk(doc: Document) -> list[Chunk]:
        header_tokens = tokenize(" ".join([doc.title, doc.summary, " ".join(doc.tags)]))
        sections = re.split(r"\n(?=## )", doc.body)
        chunks = []
        for section in sections:
            if len([line for line in section.strip().splitlines() if line.strip()]) <= 1:
                continue  # a bare heading (e.g. the document title) carries no content of its own
            heading = section.splitlines()[0].lstrip("# ").strip()
            # Title/tags are repeated into every chunk so a section is findable by what the document is about.
            chunks.append(Chunk(doc.id, heading, section.strip(), tokenize(section) + header_tokens * 2))
        return chunks

    def catalog(self) -> list[dict]:
        return [{"doc_id": d.id, "title": d.title, "summary": d.summary} for d in self.docs.values()]

    def search(self, query: str, k: int = 5) -> list[dict]:
        scores = self.index.scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        results = []
        for i in ranked[:k]:
            if scores[i] <= 0:
                break
            chunk = self.chunks[i]
            results.append({
                "doc_id": chunk.doc_id, "title": self.docs[chunk.doc_id].title, "section": chunk.heading,
                "score": round(scores[i], 2), "text": chunk.text,
            })
        return results

    def briefing(self, query: str, k: int = 4, per_doc: int = 2) -> list[dict]:
        """Top sections for a request, spread across documents (used to prime the operator)."""
        picked: list[dict] = []
        counts: dict[str, int] = {}
        for hit in self.search(query, k=k * 3):
            if counts.get(hit["doc_id"], 0) < per_doc:
                picked.append(hit)
                counts[hit["doc_id"]] = counts.get(hit["doc_id"], 0) + 1
            if len(picked) == k:
                break
        return picked

    def read(self, doc_id: str) -> Document:
        if doc_id not in self.docs:
            close = [d for d in self.docs if doc_id.lower() in d.lower()]
            hint = f" Did you mean: {', '.join(close)}?" if close else f" Available: {', '.join(self.docs)}"
            raise KeyError(f"No document '{doc_id}'.{hint}")
        return self.docs[doc_id]
