"""Knowledge base: ingestion of the real Marswalk documents and retrieval accuracy on known questions."""
import pytest

from rag import knowledge_base

pytestmark = pytest.mark.asyncio

QUESTIONS = [
    ("How long is a day on Mars?", "mars_kb_01_planetary_data.md", "24 h 39 min 35 s"),
    ("Can astronauts make oxygen on Mars?", "mars_kb_04_oxygen_moxie.md", "MOXIE"),
    ("Why can't Earth control everything in real time?", "mars_kb_06_communications_health.md", "autonomous"),
    ("How do we protect astronauts from radiation?", "mars_kb_05_radiation.md", "shielding"),
    ("What is the atmosphere of Mars made of?", "mars_kb_01_planetary_data.md", "95.1%"),
    ("How do I start the route journey?", "marswalk_app_guide.md", "Start journey"),
    ("How do I save a place to the mission log?", "marswalk_app_guide.md", "Save selected location"),
    ("What instrument measures weather on Perseverance?", "04_meda_instrument_manfredi2021.md", "MEDA"),
    ("Can we find water ice anywhere?", "mars_kb_03_water.md", "ice"),
    ("Why is water difficult to obtain?", "mars_kb_03_water.md", "unstable"),
    ("Is Mars gravity dangerous?", "mars_kb_06_communications_health.md", "3.71"),
]


@pytest.fixture(scope="module", autouse=True)
async def ingested():
    from models import init_db
    init_db()
    docs = await knowledge_base.ingest_directory()
    assert len(docs) >= 15
    return docs


@pytest.mark.parametrize("q,filename,fact", QUESTIONS)
async def test_questions_retrieve_the_right_document(q, filename, fact):
    hits = await knowledge_base.search(q, 3)
    assert hits, q
    assert hits[0]["metadata"]["filename"] == filename, (q, [h["metadata"]["filename"] for h in hits])
    assert fact in hits[0]["content"]


async def test_off_topic_returns_nothing():
    assert await knowledge_base.search("Tell me about the pyramids of Giza", 3) == []
    assert await knowledge_base.search("What is the capital of France?", 3) == []
    assert await knowledge_base.search("Who won the football world cup?", 3) == []


async def test_citations_keep_source_links():
    hits = await knowledge_base.search("MOXIE oxygen", 1)
    assert hits[0]["metadata"]["source_url"].startswith("https://www.nasa.gov")


async def test_reingesting_the_same_file_does_not_duplicate():
    before = len(await knowledge_base.list_documents(500))
    await knowledge_base.ingest_directory()
    assert len(await knowledge_base.list_documents(500)) == before
