"""Mars AI Agent Knowledge Base (marswalk-ai/knowledge): retrieval and offline answers for the PDF's example questions."""
from pathlib import Path

import pytest
from conftest import Env, make_settings, run

from marswalk_ai.services.knowledge import KnowledgeBase, _front_matter

KB_DIR = Path(__file__).resolve().parents[1] / "knowledge"

# PDF page 4: "User asks…" → the passage the agent should retrieve (file, a fact that must be in it)
PDF_QUESTIONS = [
    ("How long is a day on Mars?", "mars_kb_01_planetary_data", "24 h 39 min 35 s"),
    ("Why is water difficult to obtain?", "mars_kb_03_water", "unstable"),
    ("Can astronauts make oxygen on Mars?", "mars_kb_04_oxygen_moxie", "MOXIE"),
    ("Why can't Earth control everything in real time?", "mars_kb_06_communications_health", "autonomous"),
    ("Is Mars gravity dangerous?", "mars_kb_06_communications_health", "3.71"),
    ("Can we find water ice anywhere?", "mars_kb_03_water", "ice"),
    ("How do we protect astronauts from radiation?", "mars_kb_05_radiation", "shielding"),
    ("What is the atmosphere of Mars made of?", "mars_kb_01_planetary_data", "95.1%"),
]


@pytest.fixture(scope="module")
def kb():
    k = KnowledgeBase(str(KB_DIR))
    assert k.available, k.error
    return k


@pytest.mark.parametrize("question,file_stem,fact", PDF_QUESTIONS)
def test_pdf_questions_retrieve_the_right_passage(kb, question, file_stem, fact):
    hits = kb.search(question, 3)
    assert hits, question
    assert hits[0]["chunk_id"].startswith(file_stem), (question, hits[0]["chunk_id"])
    assert fact in hits[0]["text"]


def test_off_topic_question_retrieves_nothing(kb):
    assert kb.search("Tell me about the pyramids of Mars", 3) == []
    assert kb.search("What is the capital of France?", 3) == []


def test_every_document_keeps_its_provenance():
    files = sorted(KB_DIR.glob("mars_kb_*.md"))
    assert len(files) == 6
    for f in files:
        meta, body = _front_matter(f.read_text(encoding="utf-8"))
        assert meta.get("document_id", "").startswith("KB-MARS-"), f.name
        assert meta.get("source_url", "").startswith("https://"), f.name
        assert meta.get("title") and meta.get("organization"), f.name
        assert body.strip()


def test_several_knowledge_folders_can_be_combined(tmp_path):
    (tmp_path / "extra.md").write_text("---\ntitle: Extra\n---\n# Dust devils\nDust devils are whirlwinds seen by rovers.\n", encoding="utf-8")
    k = KnowledgeBase(f"{KB_DIR};{tmp_path};{tmp_path / 'missing'}")
    assert k.available and any(c["file"] == "extra.md" for c in k.chunks)
    assert k.search("dust devils whirlwinds", 1)[0]["chunk_id"].startswith("extra#")


def test_offline_assistant_answers_from_the_knowledge_base_with_citations():
    async def go():
        async with Env(make_settings(knowledge_dir=str(KB_DIR))) as env:
            s = env.session()
            r = await env.ask(s, "How long is a day on Mars?")
            assert r["status"] == "success" and "[DOC-1]" in r["message"] and "24 h 39 min 35 s" in r["message"]
            assert r["evidence"][0]["source_url"].startswith("https://nssdc.gsfc.nasa.gov")
            r = await env.ask(s, "Can astronauts make oxygen on Mars?")
            assert "122 grams" in r["message"]
            r = await env.ask(s, "Is Mars gravity dangerous?")
            assert "uncertain" in r["message"] or "0.38" in r["message"]
    run(go())


def test_mission_questions_still_use_the_mission_tools():
    async def go():
        async with Env(make_settings(knowledge_dir=str(KB_DIR))) as env:
            s = env.session()
            parse = env.orch.planner.parse
            assert parse("How long is a day on Mars?", s).name == "science_question"
            assert parse("What is the surface pressure on Mars?", s).name == "science_question"
            assert parse("How long will the EVA take?", s).name == "eva_estimate"
            assert parse("What is the radiation dose for this EVA?", s).name == "radiation"
            assert parse("What is the weather at the rover today?", s).name != "science_question"
            assert parse("Find me the best route to the delta", s).name == "route_find"
    run(go())
