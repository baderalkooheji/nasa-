# Marswalk AI knowledge base

The assistant answers Mars science questions only from the documents in this folder (and any other folder listed in
`KNOWLEDGE_DIR`, separated by `;`). Retrieval: `backend/marswalk_ai/services/knowledge.py` (TF-IDF, no model download).

* `source/Mars_AI_Agent_Knowledge_Base.pdf` – the original reference PDF.
* `mars_kb_01 … mars_kb_06` – its content split by topic, written from the PDF text (the PDF's tables do not extract cleanly),
  each with the PDF's source links for that topic. No facts were added.

## Adding a document

1. Create `something.md` with a header, then `#`/`##` sections (each section becomes a searchable passage):

   ```
   ---
   document_id: "KB-MARS-07"
   title: "Short title"
   source_url: "https://… (main source)"
   additional_sources: "https://… | https://…"
   organization: "NASA"
   document_type: "Curated summary"
   scientific_topic: "topic_name"
   reliability_category: "curated_from_agency_sources"
   ---
   # Title
   ## A section heading phrased like the question users ask
   Facts from the source, with their uncertainty.
   ```
2. Restart the AI server (port 8090).
3. Add the question and the expected file to `tests/test_mars_knowledge_base.py` and run `pytest`.
