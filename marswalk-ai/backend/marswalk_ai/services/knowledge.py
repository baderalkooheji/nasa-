"""Scientific document retrieval (the "R" in RAG) for the assistant.

Reads Markdown documents with YAML-like front matter (the format used by the
Scientific Intelligence Engine's data/documents/), splits them into overlapping
chunks by section, and ranks chunks with TF-IDF cosine similarity (numpy only).

Why lexical instead of embeddings here: the corpus is small (a few documents),
it runs offline with no model download, and it is deterministic for tests.
When the engine exposes a retrieval endpoint, register that capability with a
higher priority and this one becomes the fallback.

Retrieval returns text and citations. It never produces measurements.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

_WORD = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
_STOP = set("""a an and are as at be by for from has have in is it its of on or that the this to was were with
which these those their there than then into over under can may not no will would should could about between
during also such more most other""".split())
# words that appear across the whole Mars corpus carry no relevance signal ("Mars" alone must not match every document)
_STOP |= {"mars", "martian"}
# question words: a heading such as "What MOXIE does not show" must not match every "what …?" question
_STOP |= {"what", "why", "how", "when", "where", "which", "who", "does", "do", "did", "tell", "me", "you", "we", "us", "our", "your"}


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1]


def _front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, text[end + 4:]


def _chunks(body: str, size: int = 900, overlap: int = 150) -> list[tuple[str, str]]:
    """(section_heading, chunk_text) pairs; long sections are split with overlap."""
    out, heading, buf = [], "", []
    for line in body.splitlines():
        if line.startswith("#"):
            if buf:
                out.append((heading, "\n".join(buf).strip()))
            heading, buf = line.lstrip("#").strip(), []
        else:
            buf.append(line)
    if buf:
        out.append((heading, "\n".join(buf).strip()))
    res = []
    for h, t in out:
        if not t:
            continue
        i = 0
        while i < len(t):
            res.append((h, t[i:i + size]))
            if i + size >= len(t):
                break
            i += size - overlap
    return res


class KnowledgeBase:
    def __init__(self, directory: str | None):
        """directory: one folder, or several separated by ";" (e.g. the Mars AI Agent Knowledge Base + the engine's documents)."""
        self.directory = directory
        self.chunks: list[dict] = []
        self.error: str | None = None
        if not directory:
            self.error = "No knowledge directory configured (set KNOWLEDGE_DIR)."
            return
        dirs = [Path(p.strip()) for p in directory.split(";") if p.strip()]
        missing = [str(d) for d in dirs if not d.is_dir()]
        found = [d for d in dirs if d.is_dir()]
        if not found:
            self.error = f"Knowledge directory not found: {directory}"
            return
        self.missing_dirs = missing                     # reported, but the folders that exist are still used
        for d in found:
            for f in sorted(d.glob("*.md")):
                if f.name.lower() == "readme.md":
                    continue
                meta, body = _front_matter(f.read_text(encoding="utf-8", errors="replace"))
                for j, (heading, text) in enumerate(_chunks(body)):
                    self.chunks.append({"chunk_id": f"{f.stem}#{j}", "file": f.name, "section": heading, "text": text, **meta})
        if not self.chunks:
            self.error = f"No Markdown documents found in {directory}"
            return
        docs = [_tokens(f"{c.get('title', '')} {c['section']} {c['text']} {c.get('scientific_topic', '')}") for c in self.chunks]
        vocab = sorted({t for d in docs for t in d})
        self._index = {t: i for i, t in enumerate(vocab)}
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self._idf = np.array([math.log((1 + n) / (1 + df[t])) + 1 for t in vocab])
        m = np.zeros((n, len(vocab)))
        for i, d in enumerate(docs):
            for t, c in Counter(d).items():
                m[i, self._index[t]] = 1 + math.log(c)
        m *= self._idf
        self._matrix = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)

    @property
    def available(self) -> bool:
        return not self.error

    def documents(self) -> list[dict]:
        seen = {}
        for c in self.chunks:
            seen.setdefault(c["file"], {k: c.get(k) for k in ("file", "document_id", "title", "source_url", "organization",
                                                               "document_type", "scientific_topic", "reliability_category")})
        return list(seen.values())

    def search(self, query: str, top_k: int = 4, topic: str | None = None, min_score: float = 0.08) -> list[dict]:
        if not self.available:
            return []
        q = np.zeros(len(self._index))
        for t, c in Counter(_tokens(query)).items():
            if t in self._index:
                q[self._index[t]] = 1 + math.log(c)
        q *= self._idf
        norm = np.linalg.norm(q)
        if norm == 0:
            return []
        scores = self._matrix @ (q / norm)
        order = np.argsort(-scores)
        out, files = [], Counter()
        for i in order:
            c = self.chunks[int(i)]
            if scores[i] < min_score or len(out) >= top_k:
                break
            if topic and c.get("scientific_topic") != topic:
                continue
            if files[c["file"]] >= 2:          # keep results diverse
                continue
            files[c["file"]] += 1
            out.append({
                "citation_key": f"DOC-{len(out) + 1}",
                "chunk_id": c["chunk_id"], "title": c.get("title", c["file"]), "section": c["section"],
                "source_url": c.get("source_url"), "organization": c.get("organization"),
                "document_type": c.get("document_type"), "reliability_category": c.get("reliability_category"),
                "score": round(float(scores[i]), 3), "text": c["text"][:700],
                "additional_sources": [u.strip() for u in (c.get("additional_sources") or "").split("|") if u.strip()],
            })
        return out
