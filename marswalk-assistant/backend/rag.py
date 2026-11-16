"""
Marswalk AI Assistant - RAG Knowledge System
Document ingestion (PDF, TXT, Markdown, JSON) and retrieval with citations.

Retrieval:
  * ChromaDB + sentence-transformers embeddings when both are installed (optional, heavy: pulls PyTorch).
  * Otherwise BM25 keyword ranking over chunks stored in SQLite: no model download, deterministic, and good for a
    small curated corpus. Corpus-wide words ("mars") and question words ("what") carry no relevance signal.

Markdown documents may start with front matter (title, source_url, organization, …); it is kept with every chunk
so answers can cite the original source. Markdown is chunked by section heading; long sections are split with overlap.
Re-ingesting a changed file replaces its previous version.
"""
import hashlib
import json
import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from models import Document, DocumentChunk, SessionLocal

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

SUPPORTED = {".pdf", ".txt", ".md", ".json"}
_WORD = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
_STOP = set("""a an and are as at be by for from has have in is it its of on or that the this to was were with which these those their
there than then into over under can may not no will would should could about between during also such more most other what why how
when where which who does do did tell me you we us our your mars martian find anywhere get make use know any some
please give show explain describe""".split())


# everyday question words → the words the documents use (a question word counts as matched if either form is present)
_SYN = {"long": "length", "big": "diameter", "large": "diameter", "far": "distance", "cold": "temperature", "hot": "temperature",
        "warm": "temperature", "air": "atmosphere", "breathe": "oxygen", "breath": "oxygen", "weather": "meteorological",
        "dangerous": "hazard", "danger": "hazard", "safe": "safety", "talk": "communication", "message": "communication",
        "drink": "water", "heavy": "gravity", "weight": "gravity", "old": "age", "made": "composition"}


def _stem(t: str) -> str:
    """Light suffix stripping so 'measures' / 'measured' / 'measurement' meet (no NLP library needed)."""
    if len(t) > 5 and not t[0].isdigit():
        for suf in ("ements", "ement", "ments", "ment", "ings", "ing", "edly", "ies", "ed", "es", "s"):
            if t.endswith(suf) and len(t) - len(suf) >= 4 and not t.endswith("ss"):
                return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t


def _tokens(text: str) -> List[str]:
    return [_stem(t) for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1]


def _front_matter(text: str):
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


class KnowledgeBase:
    def __init__(self):
        self.embedding_model = None
        self.chroma_client = None
        self.collection = None
        self._initialized = False
        self._use_fallback = not (CHROMADB_AVAILABLE and SENTENCE_TRANSFORMERS_AVAILABLE)
        self._index = None                               # BM25 index (rebuilt after ingestion)

    async def initialize(self):
        if self._initialized:
            return
        if not self._use_fallback:
            try:
                self.embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL, device=settings.EMBEDDING_DEVICE)
                self.chroma_client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR,
                                                               settings=ChromaSettings(anonymized_telemetry=False))
                self.collection = self.chroma_client.get_or_create_collection(name=settings.CHROMA_COLLECTION_NAME,
                                                                              metadata={"hnsw:space": "cosine"})
                logger.info("Knowledge base: ChromaDB + %s", settings.EMBEDDING_MODEL)
            except Exception as e:
                logger.warning("ChromaDB initialisation failed, using BM25 over SQLite: %s", e)
                self._use_fallback = True
        if self._use_fallback:
            logger.info("Knowledge base: BM25 keyword retrieval over SQLite")
        self._initialized = True

    @property
    def mode(self) -> str:
        return "bm25-sqlite" if self._use_fallback else "chromadb"

    # ------------------------------------------------------------------ chunking
    def _chunk_text(self, text: str) -> List[str]:
        size, overlap = settings.CHUNK_SIZE, settings.CHUNK_OVERLAP
        out, start = [], 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                cut = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
                if cut > start + size * 0.5:
                    end = cut + 1
            out.append(text[start:end].strip())
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
        return [c for c in out if c]

    def _chunks_for(self, path: Path, body: str) -> List[Dict[str, str]]:
        """Markdown: one chunk per section (split further when long). Other formats: plain overlapping chunks."""
        if path.suffix.lower() != ".md":
            return [{"section": "", "text": c} for c in self._chunk_text(body)]
        sections, heading, buf = [], "", []
        for line in body.splitlines():
            if line.startswith("#"):
                if "\n".join(buf).strip():
                    sections.append((heading, "\n".join(buf).strip()))
                heading, buf = line.lstrip("#").strip(), []
            else:
                buf.append(line)
        if "\n".join(buf).strip():
            sections.append((heading, "\n".join(buf).strip()))
        return [{"section": h, "text": c} for h, t in sections for c in self._chunk_text(t)]

    # ------------------------------------------------------------------ reading
    def _read_file(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            try:
                import pypdf
            except ImportError:
                raise ValueError("pypdf is not installed: pip install pypdf")
            return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(str(path)).pages)
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return json.dumps(data, indent=1, ensure_ascii=False)
        return path.read_text(encoding="utf-8-sig")

    # ------------------------------------------------------------------ ingestion
    async def ingest_document(self, filepath: str, title: str = None, source_type: str = None, metadata: Dict = None) -> Document:
        await self.initialize()
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        raw = self._read_file(path)
        if not raw.strip():
            raise ValueError("Empty file content")
        content_hash = hashlib.sha256(raw.encode()).hexdigest()
        fm, body = _front_matter(raw) if path.suffix.lower() == ".md" else ({}, raw)
        meta = {**fm, **(metadata or {})}

        db = SessionLocal()
        try:
            existing = db.query(Document).filter(Document.content_hash == content_hash).first()
            if existing:
                return existing
            for old in db.query(Document).filter(Document.filepath == str(path.resolve())).all():   # changed file → replace
                self._delete_vectors(old)
                db.delete(old)
            db.commit()

            doc = Document(filename=path.name, filepath=str(path.resolve()), content_hash=content_hash,
                           title=title or meta.get("title") or path.stem.replace("_", " "),
                           source_type=source_type or path.suffix.lower().lstrip(".") or "txt", meta=meta, is_processed=False)
            db.add(doc)
            db.commit()
            db.refresh(doc)

            chunks = self._chunks_for(path, body)
            ids, docs_, metas = [], [], []
            for i, ch in enumerate(chunks):
                cmeta = {"document_id": doc.id, "chunk_index": i, "filename": doc.filename, "title": doc.title,
                         "section": ch["section"], "source_type": doc.source_type,
                         **{k: meta[k] for k in ("source_url", "organization", "document_type", "reliability_category") if meta.get(k)}}
                cid = f"{doc.id}_chunk_{i}"
                db.add(DocumentChunk(document_id=doc.id, chunk_index=i, content=ch["text"], embedding_id=cid, meta=cmeta))
                ids.append(cid); docs_.append(f"{ch['section']}\n{ch['text']}"); metas.append(cmeta)
            if not self._use_fallback and self.collection and ids:
                try:
                    self.collection.add(ids=ids, embeddings=[self.embedding_model.encode(t).tolist() for t in docs_],
                                        metadatas=metas, documents=docs_)
                except Exception as e:
                    logger.warning("ChromaDB add failed, using BM25: %s", e)
                    self._use_fallback = True
            doc.chunk_count = len(chunks)
            doc.is_processed = True
            db.commit()
            db.refresh(doc)
            self._index = None
            logger.info("Ingested %s (%d chunks)", doc.filename, len(chunks))
            return doc
        finally:
            db.close()

    async def ingest_directory(self, directory: str = None, pattern: str = "**/*") -> List[Document]:
        dir_path = Path(directory or settings.DOCUMENTS_DIR)
        if not dir_path.exists():
            logger.warning("Directory not found: %s", dir_path)
            return []
        # documents whose file was removed from the folder leave the index too
        db = SessionLocal()
        try:
            root = str(dir_path.resolve())
            for d in db.query(Document).all():
                if d.filepath.startswith(root) and not Path(d.filepath).exists():
                    self._delete_vectors(d)
                    db.delete(d)
                    self._index = None
                    logger.info("Removed %s from the knowledge base (file deleted)", d.filename)
            db.commit()
        finally:
            db.close()
        out = []
        for f in sorted(dir_path.glob(pattern)):
            if f.suffix.lower() in SUPPORTED and f.name.lower() != "readme.md":
                try:
                    out.append(await self.ingest_document(str(f)))
                except Exception as e:
                    logger.error("Failed to ingest %s: %s", f, e)
        return out

    # ------------------------------------------------------------------ retrieval
    def _build_index(self):
        db = SessionLocal()
        try:
            rows = db.query(DocumentChunk).join(Document).all()
            docs = [(c, Counter(_tokens(f"{(c.meta or {}).get('title', '')} {(c.meta or {}).get('section', '')} {c.content}"))) for c in rows]
        finally:
            db.close()
        n = len(docs) or 1
        df = Counter(t for _, tf in docs for t in tf)
        avgdl = sum(sum(tf.values()) for _, tf in docs) / n
        self._index = {"docs": [(c.id, c.content, dict(c.meta or {}), c.embedding_id, tf, sum(tf.values())) for c, tf in docs],
                       "idf": {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}, "avgdl": avgdl or 1}

    def _bm25(self, query: str, top_k: int, filt: Optional[Dict]) -> List[Dict[str, Any]]:
        if self._index is None:
            self._build_index()
        q = _tokens(query)
        if not q:
            return []
        idf, avgdl, k1, b = self._index["idf"], self._index["avgdl"], 1.4, 0.75
        scored = []
        unknown_idf = max(idf.values(), default=1.0)
        syn = {_stem(k): _stem(v) for k, v in _SYN.items()}
        # a question word the corpus lacks but whose synonym it has is weighted like the synonym
        q_weight = sum(idf.get(t, idf.get(syn.get(t), unknown_idf)) for t in set(q)) or 1
        for cid, content, meta, eid, tf, dl in self._index["docs"]:
            if filt and any(meta.get(k) != v for k, v in filt.items()):
                continue
            # coverage: the matched words must carry at least half of the question's information (idf weight)
            hit = {t: (t if t in tf else syn.get(t) if syn.get(t) in tf else None) for t in set(q)}
            if sum(idf.get(t, idf.get(syn.get(t), unknown_idf)) for t, h in hit.items() if h) < 0.5 * q_weight:
                continue
            s = sum(idf.get(h, 0) * tf[h] * (k1 + 1) / (tf[h] + k1 * (1 - b + b * dl / avgdl)) for t, h in hit.items() if h)
            if s > 0:
                scored.append((s, cid, content, meta, eid))
        if not scored:
            return []
        scored.sort(key=lambda x: -x[0])
        top = scored[0][0]
        # reference score: every query word present once in an average-length chunk (BM25 term score ≈ idf).
        # Words that never occur in the corpus count at the highest idf: a question about things the documents never
        # mention ("pyramids of Giza") is not covered just because one of its words appears somewhere.
        max_possible = q_weight
        out, per_doc = [], Counter()
        for s, cid, content, meta, eid in scored:
            # relevance gate: weak matches (one common word out of several) are not evidence
            if s < 0.3 * max_possible or s < 0.35 * top:
                break
            if per_doc[meta.get("document_id")] >= 2:          # keep results diverse
                continue
            per_doc[meta.get("document_id")] += 1
            out.append({"id": eid or f"chunk_{cid}", "content": content, "metadata": meta,
                        "distance": round(1 - s / max_possible, 4), "relevance": round(s / max_possible, 4)})
            if len(out) >= top_k:
                break
        return out

    async def search(self, query: str, top_k: int = 5, filter: Dict = None) -> List[Dict[str, Any]]:
        await self.initialize()
        if self._use_fallback:
            return self._bm25(query, top_k, filter)
        try:
            res = self.collection.query(query_embeddings=[self.embedding_model.encode(query).tolist()], n_results=top_k,
                                        where=filter or None, include=["documents", "metadatas", "distances"])
            return [{"id": res["ids"][0][i], "content": res["documents"][0][i], "metadata": res["metadatas"][0][i],
                     "distance": res["distances"][0][i], "relevance": 1 - res["distances"][0][i]}
                    for i in range(len(res["ids"][0])) if 1 - res["distances"][0][i] > 0.25]
        except Exception as e:
            logger.warning("ChromaDB search failed, using BM25: %s", e)
            self._use_fallback = True
            return self._bm25(query, top_k, filter)

    # ------------------------------------------------------------------ management
    async def get_document(self, document_id: int) -> Optional[Document]:
        db = SessionLocal()
        try:
            return db.query(Document).filter(Document.id == document_id).first()
        finally:
            db.close()

    async def list_documents(self, limit: int = 50) -> List[Document]:
        db = SessionLocal()
        try:
            return db.query(Document).order_by(Document.created_at.desc()).limit(limit).all()
        finally:
            db.close()

    def _delete_vectors(self, doc: Document):
        if not self._use_fallback and self.collection and doc.chunk_count:
            try:
                self.collection.delete(ids=[f"{doc.id}_chunk_{i}" for i in range(doc.chunk_count)])
            except Exception:
                pass

    async def delete_document(self, document_id: int) -> bool:
        await self.initialize()
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc:
                return False
            self._delete_vectors(doc)
            db.delete(doc)
            db.commit()
            self._index = None
            return True
        finally:
            db.close()


knowledge_base = KnowledgeBase()
