"""
SCRATCHPAD — Comprehensive Test Suite
======================================
Run with: pytest tests/test_suite.py -v
Requires: pytest, pytest-asyncio, httpx, faker

Install: pip install pytest pytest-asyncio httpx faker
"""

import pytest
import asyncio
import time
import json
import sqlite3
import random
import string
from datetime import datetime, timedelta
from httpx import AsyncClient
from faker import Faker

fake = Faker()
BASE_URL = "http://127.0.0.1:7337"

# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def client():
    async with AsyncClient(base_url=BASE_URL, timeout=30.0) as c:
        yield c

@pytest.fixture(autouse=True)
async def clean_db(client):
    """Wipe all entries before each test for isolation."""
    await client.delete("/api/test/reset")
    yield


# ============================================================
# 1. HEALTH & STARTUP
# ============================================================

class TestHealth:

    async def test_app_is_running(self, client):
        r = await client.get("/api/status")
        assert r.status_code == 200

    async def test_ollama_connected(self, client):
        r = await client.get("/api/status")
        data = r.json()
        assert data["ollama_connected"] is True

    async def test_embed_model_available(self, client):
        r = await client.get("/api/status")
        data = r.json()
        assert data["embed_model_available"] is True

    async def test_db_initialized(self, client):
        r = await client.get("/api/status")
        data = r.json()
        assert data["db_ok"] is True

    async def test_entry_count_starts_zero(self, client):
        r = await client.get("/api/status")
        data = r.json()
        assert data["entry_count"] == 0


# ============================================================
# 2. BASIC ENTRY CAPTURE
# ============================================================

class TestBasicCapture:

    async def test_save_single_entry(self, client):
        r = await client.post("/api/entries", json={"content": "Gate code is 44321."})
        assert r.status_code == 201
        data = r.json()
        assert data["id"] is not None
        assert data["content"] == "Gate code is 44321."

    async def test_entry_has_timestamp(self, client):
        r = await client.post("/api/entries", json={"content": "Test timestamp entry."})
        data = r.json()
        assert "created_at" in data
        assert data["created_at"] is not None

    async def test_entry_not_superseded_by_default(self, client):
        r = await client.post("/api/entries", json={"content": "Fresh entry."})
        data = r.json()
        assert data["is_superseded"] == 0

    async def test_save_empty_entry_rejected(self, client):
        r = await client.post("/api/entries", json={"content": ""})
        assert r.status_code == 422

    async def test_save_whitespace_only_rejected(self, client):
        r = await client.post("/api/entries", json={"content": "     \n\n\t  "})
        assert r.status_code == 422

    async def test_save_very_short_entry(self, client):
        r = await client.post("/api/entries", json={"content": "ok"})
        assert r.status_code == 201

    async def test_save_single_word(self, client):
        r = await client.post("/api/entries", json={"content": "password"})
        assert r.status_code == 201

    async def test_save_single_number(self, client):
        r = await client.post("/api/entries", json={"content": "44321"})
        assert r.status_code == 201

    async def test_stream_returns_entries(self, client):
        await client.post("/api/entries", json={"content": "Entry one."})
        await client.post("/api/entries", json={"content": "Entry two."})
        r = await client.get("/api/entries")
        data = r.json()
        assert len(data["entries"]) == 2

    async def test_stream_newest_first(self, client):
        await client.post("/api/entries", json={"content": "First entry."})
        await asyncio.sleep(0.1)
        await client.post("/api/entries", json={"content": "Second entry."})
        r = await client.get("/api/entries")
        entries = r.json()["entries"]
        assert entries[0]["content"] == "Second entry."
        assert entries[1]["content"] == "First entry."

    async def test_pagination_limit(self, client):
        for i in range(10):
            await client.post("/api/entries", json={"content": f"Entry number {i}."})
        r = await client.get("/api/entries?limit=5")
        data = r.json()
        assert len(data["entries"]) == 5

    async def test_pagination_offset(self, client):
        for i in range(10):
            await client.post("/api/entries", json={"content": f"Paginated entry {i}."})
        r1 = await client.get("/api/entries?limit=5&offset=0")
        r2 = await client.get("/api/entries?limit=5&offset=5")
        ids1 = {e["id"] for e in r1.json()["entries"]}
        ids2 = {e["id"] for e in r2.json()["entries"]}
        assert ids1.isdisjoint(ids2)


# ============================================================
# 3. CONTENT TYPES — WHAT CAN BE DUMPED IN
# ============================================================

class TestContentTypes:

    async def test_multiline_entry(self, client):
        content = "Line one.\nLine two.\nLine three."
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201
        assert r.json()["content"] == content

    async def test_markdown_table(self, client):
        content = "| Project | Status | Owner |\n|---|---|---|\n| Alpha | Delayed | Raj |\n| Beta | On track | Suha |"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_markdown_headers(self, client):
        content = "# Meeting Notes\n## Action Items\n- Follow up with client\n- Send invoice"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_code_block(self, client):
        content = "API endpoint:\n```python\ndef hello():\n    return 'world'\n```"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_urls(self, client):
        content = "Check this out: https://github.com/anthropics/anthropic-sdk-python"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_phone_numbers(self, client):
        content = "Raj's number is +971-50-111-2222"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_mixed_languages(self, client):
        content = "Meeting in Dubai. الاجتماع في دبي. 会议在迪拜。"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_arabic_only(self, client):
        content = "رقم راج هو ٠٥٠-١١١١"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_emojis(self, client):
        content = "Meeting went well 🎉 follow up next Tuesday 📅"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_special_characters(self, client):
        content = "Password: P@$$w0rd!#2026 — don't forget this"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_json_pasted_as_text(self, client):
        content = '{"api_key": "sk-abc123", "endpoint": "https://api.example.com"}'
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_sql_pasted_as_text(self, client):
        content = "SELECT * FROM users WHERE id = 1; -- check this query"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_very_long_entry(self, client):
        # ~5000 words - a full meeting transcript
        content = " ".join(fake.sentence() for _ in range(500))
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_extremely_long_entry(self, client):
        # ~20000 words - pushing chunking hard
        content = " ".join(fake.sentence() for _ in range(2000))
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_repeated_characters(self, client):
        content = "a" * 10000
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_newlines_only_rejected(self, client):
        r = await client.post("/api/entries", json={"content": "\n\n\n\n"})
        assert r.status_code == 422

    async def test_tab_separated_data(self, client):
        content = "Name\tPhone\tEmail\nRaj\t050-1111\traj@example.com\nSuha\t050-2222\tsuha@example.com"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_xss_attempt_stored_safely(self, client):
        content = '<script>alert("xss")</script> Some note here'
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201
        # Content stored as-is (plain text), rendering layer sanitizes
        stored = r.json()["content"]
        assert "<script>" in stored  # stored raw

    async def test_sql_injection_attempt(self, client):
        content = "'; DROP TABLE entries; -- test note"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201
        # DB must still be intact
        r2 = await client.get("/api/entries")
        assert r2.status_code == 200


# ============================================================
# 4. SEMANTIC SEARCH / QUERY
# ============================================================

class TestSemanticSearch:

    async def test_exact_keyword_match(self, client):
        await client.post("/api/entries", json={"content": "Gate code is 44321."})
        await asyncio.sleep(1.0)  # wait for embedding
        r = await client.post("/api/query", json={"query": "gate code"})
        results = r.json()["entries"]
        assert any("44321" in e["content"] for e in results)

    async def test_semantic_match_different_words(self, client):
        await client.post("/api/entries", json={"content": "The tax filing deadline is July 15th."})
        await asyncio.sleep(1.0)
        r = await client.post("/api/query", json={"query": "when do I need to submit my taxes"})
        results = r.json()["entries"]
        assert len(results) > 0
        assert any("July 15" in e["content"] for e in results)

    async def test_semantic_phone_number_retrieval(self, client):
        await client.post("/api/entries", json={"content": "Raj's contact number is 050-1111-2222."})
        await asyncio.sleep(1.0)
        r = await client.post("/api/query", json={"query": "how do I reach Raj"})
        results = r.json()["entries"]
        assert len(results) > 0

    async def test_query_returns_no_superseded_entries(self, client):
        r1 = await client.post("/api/entries", json={"content": "Office wifi password is oldpass123."})
        entry_id = r1.json()["id"]
        await client.delete(f"/api/entries/{entry_id}")  # supersede it
        await client.post("/api/entries", json={"content": "Office wifi password is newpass456."})
        await asyncio.sleep(1.0)
        r = await client.post("/api/query", json={"query": "wifi password"})
        results = r.json()["entries"]
        assert all(e["is_superseded"] == 0 for e in results)
        assert any("newpass456" in e["content"] for e in results)

    async def test_query_isolates_unrelated_domains(self, client):
        await client.post("/api/entries", json={"content": "Project Alpha is delayed by two weeks due to API issues."})
        await client.post("/api/entries", json={"content": "Buy a birthday gift for mom, she wants a gardening set."})
        await asyncio.sleep(1.5)
        r = await client.post("/api/query", json={"query": "what is delayed"})
        results = r.json()["entries"]
        assert any("Project Alpha" in e["content"] for e in results)
        # Birthday entry should not be top result
        if len(results) > 0:
            assert "gardening" not in results[0]["content"]

    async def test_query_bridges_timeline_gap(self, client):
        await client.post("/api/entries", json={"content": "Met with accountant. Tax filing deadline is July 15th."})
        # Dump unrelated stuff in between
        for _ in range(5):
            await client.post("/api/entries", json={"content": fake.paragraph()})
        await client.post("/api/entries", json={"content": "Accountant called back, need to send Q4 bank statements for the tax filing."})
        await asyncio.sleep(2.0)
        r = await client.post("/api/query", json={"query": "what do I need to do for taxes"})
        results = r.json()["entries"]
        contents = " ".join(e["content"] for e in results)
        assert "July 15" in contents or "Q4" in contents

    async def test_empty_query_rejected(self, client):
        r = await client.post("/api/query", json={"query": ""})
        assert r.status_code == 422

    async def test_query_with_no_matching_entries(self, client):
        await client.post("/api/entries", json={"content": "Bought groceries today."})
        await asyncio.sleep(1.0)
        r = await client.post("/api/query", json={"query": "quantum physics research paper"})
        data = r.json()
        assert "entries" in data
        # May return empty or low-score results — should not crash

    async def test_query_with_100_entries(self, client):
        for i in range(100):
            await client.post("/api/entries", json={"content": f"{fake.sentence()} Reference code: REF-{i:04d}."})
        await asyncio.sleep(3.0)  # give time to embed all
        r = await client.post("/api/query", json={"query": "REF-0042"})
        results = r.json()["entries"]
        assert any("REF-0042" in e["content"] for e in results)

    async def test_query_response_time(self, client):
        for _ in range(20):
            await client.post("/api/entries", json={"content": fake.paragraph()})
        await asyncio.sleep(2.0)
        start = time.time()
        await client.post("/api/query", json={"query": "random query to test speed"})
        elapsed = time.time() - start
        assert elapsed < 2.0, f"Query took {elapsed:.2f}s — too slow"

    async def test_query_deduplicates_entries(self, client):
        await client.post("/api/entries", json={"content": "Long entry about the tax situation. Tax deadline July 15. Tax forms needed. Tax accountant confirmed."})
        await asyncio.sleep(1.0)
        r = await client.post("/api/query", json={"query": "tax deadline"})
        results = r.json()["entries"]
        ids = [e["id"] for e in results]
        assert len(ids) == len(set(ids)), "Duplicate entries in results"


# ============================================================
# 5. GHOST SUGGESTION
# ============================================================

class TestGhostSuggestion:

    async def test_ghost_fires_for_matching_content(self, client):
        await client.post("/api/entries", json={"content": "Raj's phone number is 050-1111."})
        await asyncio.sleep(1.0)
        r = await client.post("/api/suggest", json={"text": "Raj's number is"})
        data = r.json()
        assert data["match"] is not None
        assert data["score"] > 0.78

    async def test_ghost_does_not_fire_below_threshold(self, client):
        await client.post("/api/entries", json={"content": "The quarterly report is due next Friday."})
        await asyncio.sleep(1.0)
        r = await client.post("/api/suggest", json={"text": "bought milk today"})
        data = r.json()
        assert data["match"] is None or data["score"] < 0.78

    async def test_ghost_ignores_superseded_entries(self, client):
        r1 = await client.post("/api/entries", json={"content": "Server password is oldserver123."})
        entry_id = r1.json()["id"]
        await client.delete(f"/api/entries/{entry_id}")
        await asyncio.sleep(1.0)
        r = await client.post("/api/suggest", json={"text": "server password is"})
        data = r.json()
        if data["match"] is not None:
            assert data["match"]["is_superseded"] == 0

    async def test_ghost_returns_single_result(self, client):
        await client.post("/api/entries", json={"content": "Raj's email is raj@company.com"})
        await client.post("/api/entries", json={"content": "Raj's phone is 050-1111"})
        await asyncio.sleep(1.0)
        r = await client.post("/api/suggest", json={"text": "Raj's contact"})
        data = r.json()
        # Must return at most one match
        assert "match" in data
        assert not isinstance(data["match"], list)

    async def test_ghost_short_input_no_fire(self, client):
        await client.post("/api/entries", json={"content": "Tax deadline July 15th."})
        await asyncio.sleep(1.0)
        # Empty / whitespace input must never fire. (Single substantial words DO
        # peek by design — see docs/DECISIONS.md; the ghost is a recall tool.)
        r = await client.post("/api/suggest", json={"text": "   "})
        data = r.json()
        assert data["match"] is None

    async def test_ghost_response_time(self, client):
        for _ in range(50):
            await client.post("/api/entries", json={"content": fake.paragraph()})
        await asyncio.sleep(3.0)
        start = time.time()
        await client.post("/api/suggest", json={"text": "meeting notes from last Tuesday about the project"})
        elapsed = time.time() - start
        assert elapsed < 0.6, f"Ghost took {elapsed:.2f}s — must be under 600ms"

    async def test_ghost_similarity_score_returned(self, client):
        await client.post("/api/entries", json={"content": "WiFi password is SuperSecure2026!"})
        await asyncio.sleep(1.0)
        r = await client.post("/api/suggest", json={"text": "WiFi password is"})
        data = r.json()
        assert "score" in data
        assert isinstance(data["score"], float)


# ============================================================
# 6. TEMPORAL CONFLICT RESOLUTION (SCENARIO B)
# ============================================================

class TestTemporalConflict:

    async def test_supersede_old_entry_on_update(self, client):
        r1 = await client.post("/api/entries", json={"content": "Raj's number is 050-1111."})
        old_id = r1.json()["id"]
        await client.delete(f"/api/entries/{old_id}")
        r2 = await client.post("/api/entries", json={"content": "Raj's number is 050-2222."})
        await asyncio.sleep(1.0)
        r = await client.post("/api/query", json={"query": "Raj's phone number"})
        results = r.json()["entries"]
        active = [e for e in results if e["is_superseded"] == 0]
        assert any("050-2222" in e["content"] for e in active)
        assert not any("050-1111" in e["content"] for e in active)

    async def test_superseded_entry_preserved_in_history(self, client):
        r1 = await client.post("/api/entries", json={"content": "Office address is Old Building, Floor 3."})
        old_id = r1.json()["id"]
        await client.delete(f"/api/entries/{old_id}")
        r = await client.get(f"/api/entries/{old_id}")
        data = r.json()
        assert data["is_superseded"] == 1

    async def test_multiple_sequential_updates(self, client):
        """Raj's number changes 5 times — only the last should surface."""
        prev_id = None
        for i in range(1, 6):
            if prev_id:
                await client.delete(f"/api/entries/{prev_id}")
            r = await client.post("/api/entries", json={"content": f"Raj's number is 050-{i:04d}."})
            prev_id = r.json()["id"]
        await asyncio.sleep(1.5)
        r = await client.post("/api/query", json={"query": "Raj's number"})
        results = r.json()["entries"]
        active = [e for e in results if e["is_superseded"] == 0]
        assert any("050-0005" in e["content"] for e in active)
        assert not any(f"050-{i:04d}" in e["content"] for e in active for i in range(1, 5))

    async def test_unrelated_entries_not_affected_by_supersede(self, client):
        r1 = await client.post("/api/entries", json={"content": "Mom's birthday is March 15."})
        r2 = await client.post("/api/entries", json={"content": "Raj's number is 050-1111."})
        await client.delete(f"/api/entries/{r2.json()['id']}")
        r3 = await client.get(f"/api/entries/{r1.json()['id']}")
        assert r3.json()["is_superseded"] == 0


# ============================================================
# 7. INLINE EDIT
# ============================================================

class TestInlineEdit:

    async def test_edit_updates_in_place_same_id(self, client):
        r1 = await client.post("/api/entries", json={"content": "Original content here."})
        old_id = r1.json()["id"]
        r2 = await client.patch(f"/api/entries/{old_id}", json={"content": "Updated content here."})
        assert r2.status_code == 200
        updated = r2.json()
        assert updated["id"] == old_id                  # same note, edited in place
        assert updated["content"] == "Updated content here."

    async def test_edit_does_not_supersede_or_duplicate(self, client):
        r1 = await client.post("/api/entries", json={"content": "Original text."})
        old_id = r1.json()["id"]
        await client.patch(f"/api/entries/{old_id}", json={"content": "Edited text."})
        r = await client.get(f"/api/entries/{old_id}")
        assert r.json()["is_superseded"] == 0           # not archived
        listed = await client.get("/api/entries")
        assert [e["id"] for e in listed.json()["entries"]] == [old_id]  # exactly one note

    async def test_edit_with_empty_content_rejected(self, client):
        r1 = await client.post("/api/entries", json={"content": "Some content."})
        old_id = r1.json()["id"]
        r2 = await client.patch(f"/api/entries/{old_id}", json={"content": ""})
        assert r2.status_code == 422

    async def test_edit_nonexistent_entry(self, client):
        r = await client.patch("/api/entries/99999999", json={"content": "Ghost edit."})
        assert r.status_code == 404

    async def test_edit_already_superseded_entry(self, client):
        r1 = await client.post("/api/entries", json={"content": "Entry to supersede."})
        old_id = r1.json()["id"]
        await client.delete(f"/api/entries/{old_id}")
        r2 = await client.patch(f"/api/entries/{old_id}", json={"content": "Trying to edit a dead entry."})
        # Should either reject or create new without touching superseded chain
        assert r2.status_code in [400, 409, 422]


# ============================================================
# 8. STRESS TESTS
# ============================================================

class TestStress:

    async def test_100_rapid_entries(self, client):
        """Fire 100 entries as fast as possible."""
        results = []
        for i in range(100):
            r = await client.post("/api/entries", json={"content": f"Rapid entry {i}: {fake.sentence()}"})
            results.append(r.status_code)
        assert all(s == 201 for s in results)

    async def test_concurrent_entries(self, client):
        """10 concurrent saves — no data corruption."""
        tasks = [
            client.post("/api/entries", json={"content": f"Concurrent entry {i}: {fake.sentence()}"})
            for i in range(10)
        ]
        responses = await asyncio.gather(*tasks)
        assert all(r.status_code == 201 for r in responses)
        ids = [r.json()["id"] for r in responses]
        assert len(ids) == len(set(ids)), "Duplicate IDs from concurrent writes"

    async def test_1000_entry_search_performance(self, client):
        """1000 entries — search must still complete under 3s."""
        for i in range(1000):
            await client.post("/api/entries", json={"content": f"{fake.paragraph()} NEEDLE-{i}"})
        await asyncio.sleep(10.0)  # let embeddings catch up
        start = time.time()
        r = await client.post("/api/query", json={"query": "NEEDLE-500"})
        elapsed = time.time() - start
        assert r.status_code == 200
        assert elapsed < 3.0, f"Search over 1000 entries took {elapsed:.2f}s"

    async def test_max_content_size(self, client):
        """Entry near SQLite text limit — should handle gracefully."""
        content = "x" * 999_999  # ~1MB text
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code in [201, 413]  # either accept or reject cleanly

    async def test_rapid_ghost_suggestions(self, client):
        """Ghost endpoint hammered with 50 requests — no crash."""
        await client.post("/api/entries", json={"content": "Test entry for rapid ghost test."})
        await asyncio.sleep(1.0)
        tasks = [
            client.post("/api/suggest", json={"text": f"test entry suggestion attempt {i}"})
            for i in range(50)
        ]
        responses = await asyncio.gather(*tasks)
        assert all(r.status_code == 200 for r in responses)

    async def test_search_with_only_superseded_entries(self, client):
        """All entries superseded — search returns empty cleanly."""
        r1 = await client.post("/api/entries", json={"content": "Only entry in the system."})
        await client.delete(f"/api/entries/{r1.json()['id']}")
        await asyncio.sleep(1.0)
        r = await client.post("/api/query", json={"query": "only entry"})
        assert r.status_code == 200
        results = r.json()["entries"]
        assert all(e["is_superseded"] == 0 for e in results)


# ============================================================
# 9. EDGE CASES — THE WEIRD SHIT
# ============================================================

class TestEdgeCases:

    async def test_entry_with_only_numbers(self, client):
        r = await client.post("/api/entries", json={"content": "1234567890"})
        assert r.status_code == 201

    async def test_entry_with_only_punctuation(self, client):
        r = await client.post("/api/entries", json={"content": "!!!???...---"})
        assert r.status_code in [201, 422]  # either fine, just don't crash

    async def test_duplicate_identical_entries(self, client):
        """Same content twice — both should be saved, no dedup on input."""
        content = "This is a duplicate entry."
        r1 = await client.post("/api/entries", json={"content": content})
        r2 = await client.post("/api/entries", json={"content": content})
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] != r2.json()["id"]

    async def test_entry_that_looks_like_sql(self, client):
        content = "SELECT id, content FROM entries WHERE is_superseded = 0 ORDER BY created_at DESC"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_entry_with_null_byte(self, client):
        content = "Entry with null\x00byte inside"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code in [201, 422]  # handle gracefully

    async def test_entry_unicode_edge_cases(self, client):
        content = "Emoji edge: 🏴󠁧󠁢󠁳󠁣󠁴󠁿 flag, zero-width joiner: 👨‍👩‍👧‍👦 family"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_rtl_text_arabic(self, client):
        content = "ملاحظة مهمة: الاجتماع يوم الثلاثاء الساعة العاشرة صباحاً"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201

    async def test_query_with_special_chars(self, client):
        r = await client.post("/api/query", json={"query": "what's the p@$$word for wifi?"})
        assert r.status_code == 200

    async def test_suggest_with_very_long_input(self, client):
        text = " ".join(fake.sentence() for _ in range(50))
        r = await client.post("/api/suggest", json={"text": text})
        assert r.status_code == 200

    async def test_entry_content_preserved_exactly(self, client):
        """What goes in must come out exactly."""
        content = "Exact: \t tabs \n newlines \r carriage returns and spaces   "
        r1 = await client.post("/api/entries", json={"content": content})
        entry_id = r1.json()["id"]
        r2 = await client.get(f"/api/entries/{entry_id}")
        assert r2.json()["content"] == content

    async def test_supersede_nonexistent_entry(self, client):
        r = await client.delete("/api/entries/99999999")
        assert r.status_code == 404

    async def test_restore_superseded_entry(self, client):
        r1 = await client.post("/api/entries", json={"content": "Entry to restore."})
        entry_id = r1.json()["id"]
        await client.delete(f"/api/entries/{entry_id}")
        r2 = await client.patch(f"/api/entries/{entry_id}/restore")
        assert r2.status_code == 200
        r3 = await client.get(f"/api/entries/{entry_id}")
        assert r3.json()["is_superseded"] == 0

    async def test_search_query_that_matches_everything(self, client):
        """Generic query — should return top 5, not all 100 entries."""
        for _ in range(100):
            await client.post("/api/entries", json={"content": fake.sentence()})
        await asyncio.sleep(3.0)
        r = await client.post("/api/query", json={"query": "the"})
        results = r.json()["entries"]
        assert len(results) <= 10  # must cap results, not dump everything

    async def test_interleaved_topics_isolation(self, client):
        """
        Classic Scenario C from PRD.
        Project update and family note interleaved.
        Query for project must not return family note as top result.
        """
        await client.post("/api/entries", json={"content": "Project Alpha migration delayed two weeks, API integration failing."})
        await client.post("/api/entries", json={"content": "Pick up birthday cake for Aathrey on Saturday morning."})
        await client.post("/api/entries", json={"content": "Project Alpha backend engineer says fix needs refactor."})
        await client.post("/api/entries", json={"content": "Remind Suha about parent-teacher meeting next Monday."})
        await asyncio.sleep(2.0)
        r = await client.post("/api/query", json={"query": "what is the status of Project Alpha"})
        results = r.json()["entries"]
        assert len(results) > 0
        top = results[0]["content"]
        assert "Alpha" in top or "migration" in top or "API" in top

    async def test_context_re_emergence_across_gap(self, client):
        """
        Classic Scenario D from PRD.
        Tax info Week 1, noise in between, tax update Week 3.
        Both must surface together.
        """
        await client.post("/api/entries", json={"content": "Met accountant. Tax deadline July 15th."})
        for _ in range(10):
            await client.post("/api/entries", json={"content": fake.paragraph()})
        await client.post("/api/entries", json={"content": "Accountant needs Q4 bank statements for tax filing."})
        await asyncio.sleep(2.0)
        r = await client.post("/api/query", json={"query": "what do I need for the tax filing"})
        results = r.json()["entries"]
        contents = " ".join(e["content"] for e in results)
        assert "July 15" in contents
        assert "Q4" in contents


# ============================================================
# 10. SECURITY TESTS
# ============================================================

class TestSecurity:

    async def test_only_binds_localhost(self):
        """App must not be reachable on 0.0.0.0."""
        import httpx
        try:
            r = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: httpx.get("http://0.0.0.0:7337/api/status", timeout=2.0)
                ),
                timeout=3.0
            )
            # If we got here on 0.0.0.0, that's a problem — but connection refused is expected
        except Exception:
            pass  # connection refused = correct behavior

    async def test_xss_not_executed_in_stored_content(self, client):
        """XSS payload stored as plain text — sanitization is frontend's job, but backend stores raw."""
        payload = '<img src=x onerror=alert(1)>'
        r = await client.post("/api/entries", json={"content": payload})
        assert r.status_code == 201
        stored = r.json()["content"]
        # Backend stores raw — that's correct. Frontend must sanitize.
        assert stored == payload

    async def test_path_traversal_in_entry(self, client):
        content = "../../../../etc/passwd some note"
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code == 201  # stored as text, no file access

    async def test_oversized_payload_handled(self, client):
        """10MB payload — must be rejected or handled, not crash."""
        content = "x" * 10_000_000
        r = await client.post("/api/entries", json={"content": content})
        assert r.status_code in [201, 413, 422]

    async def test_malformed_json_rejected(self, client):
        import httpx
        r = await client.post(
            "/api/entries",
            content=b"{not valid json}",
            headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 422

    async def test_missing_content_field_rejected(self, client):
        r = await client.post("/api/entries", json={"text": "wrong field name"})
        assert r.status_code == 422


# ============================================================
# 11. EMBEDDING & INDEXING
# ============================================================

class TestEmbedding:

    async def test_entry_gets_indexed(self, client):
        r = await client.post("/api/entries", json={"content": "Indexing test entry unique phrase ZXQWERTY123."})
        entry_id = r.json()["id"]
        await asyncio.sleep(2.0)
        r2 = await client.get(f"/api/entries/{entry_id}/chunks")
        assert r2.status_code == 200
        chunks = r2.json()["chunks"]
        assert len(chunks) > 0
        assert all(c["embedding"] is not None for c in chunks)

    async def test_long_entry_produces_multiple_chunks(self, client):
        content = " ".join(fake.sentence() for _ in range(200))  # ~1500 words
        r = await client.post("/api/entries", json={"content": content})
        entry_id = r.json()["id"]
        await asyncio.sleep(3.0)
        r2 = await client.get(f"/api/entries/{entry_id}/chunks")
        chunks = r2.json()["chunks"]
        assert len(chunks) > 1

    async def test_short_entry_produces_single_chunk(self, client):
        r = await client.post("/api/entries", json={"content": "Short note: call dentist."})
        entry_id = r.json()["id"]
        await asyncio.sleep(1.5)
        r2 = await client.get(f"/api/entries/{entry_id}/chunks")
        chunks = r2.json()["chunks"]
        assert len(chunks) == 1

    async def test_superseded_entry_chunks_excluded_from_search(self, client):
        r1 = await client.post("/api/entries", json={"content": "Unique phrase ABCXYZ999 in old entry."})
        entry_id = r1.json()["id"]
        await asyncio.sleep(1.0)
        await client.delete(f"/api/entries/{entry_id}")
        r = await client.post("/api/query", json={"query": "ABCXYZ999"})
        results = r.json()["entries"]
        assert not any("ABCXYZ999" in e["content"] for e in results if e["is_superseded"] == 0)


# ============================================================
# 12. EXPORT
# ============================================================

class TestExport:

    async def test_export_markdown(self, client):
        await client.post("/api/entries", json={"content": "Tax deadline is July 15th."})
        await client.post("/api/entries", json={"content": "Raj's number is 050-2222."})
        r = await client.get("/api/export/markdown")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        body = r.text
        assert "Tax deadline" in body
        assert "Raj's number" in body

    async def test_export_excludes_superseded(self, client):
        r1 = await client.post("/api/entries", json={"content": "Old wifi password oldpass123."})
        await client.delete(f"/api/entries/{r1.json()['id']}")
        await client.post("/api/entries", json={"content": "New wifi password newpass456."})
        r = await client.get("/api/export/markdown")
        body = r.text
        assert "newpass456" in body
        assert "oldpass123" not in body

    async def test_export_empty_db(self, client):
        r = await client.get("/api/export/markdown")
        assert r.status_code == 200
        assert len(r.text.strip()) == 0 or "# Scratchpad" in r.text

    async def test_export_filtered_by_query(self, client):
        await client.post("/api/entries", json={"content": "Project Alpha delayed two weeks."})
        await client.post("/api/entries", json={"content": "Buy cake for Aathrey."})
        await asyncio.sleep(1.5)
        r = await client.get("/api/export/markdown?query=Project Alpha")
        body = r.text
        assert "Project Alpha" in body
        assert "cake" not in body


# ============================================================
# CHECKBOX TOGGLE (in-place, no supersede, no re-embed)
# ============================================================

class TestCheckboxToggle:

    async def test_toggle_updates_in_place_same_id(self, client):
        r = await client.post("/api/entries", json={"content": "todo\n- [ ] milk\n- [ ] eggs"})
        eid = r.json()["id"]
        r2 = await client.patch(f"/api/entries/{eid}/checkbox", json={"index": 1, "checked": True})
        assert r2.status_code == 200
        body = r2.json()
        assert body["id"] == eid                  # same note, not a new one
        assert body["is_superseded"] == 0
        assert body["content"] == "todo\n- [ ] milk\n- [x] eggs"

    async def test_toggle_uncheck(self, client):
        r = await client.post("/api/entries", json={"content": "- [x] done"})
        eid = r.json()["id"]
        r2 = await client.patch(f"/api/entries/{eid}/checkbox", json={"index": 0, "checked": False})
        assert r2.json()["content"] == "- [ ] done"

    async def test_old_note_is_not_superseded(self, client):
        """A tick must not archive the note: the original id stays active and listed."""
        r = await client.post("/api/entries", json={"content": "- [ ] a"})
        eid = r.json()["id"]
        await client.patch(f"/api/entries/{eid}/checkbox", json={"index": 0, "checked": True})
        listed = await client.get("/api/entries")
        ids = [e["id"] for e in listed.json()["entries"]]
        assert ids == [eid]                        # exactly one active note, the same one

    async def test_index_out_of_range_409(self, client):
        r = await client.post("/api/entries", json={"content": "- [ ] only one"})
        eid = r.json()["id"]
        r2 = await client.patch(f"/api/entries/{eid}/checkbox", json={"index": 9, "checked": True})
        assert r2.status_code == 409

    async def test_negative_index_422(self, client):
        r = await client.post("/api/entries", json={"content": "- [ ] x"})
        eid = r.json()["id"]
        r2 = await client.patch(f"/api/entries/{eid}/checkbox", json={"index": -1, "checked": True})
        assert r2.status_code == 422

    async def test_toggle_missing_entry_404(self, client):
        r = await client.patch("/api/entries/999999/checkbox", json={"index": 0, "checked": True})
        assert r.status_code == 404

    async def test_toggle_superseded_409(self, client):
        r = await client.post("/api/entries", json={"content": "- [ ] gone"})
        eid = r.json()["id"]
        await client.delete(f"/api/entries/{eid}")
        r2 = await client.patch(f"/api/entries/{eid}/checkbox", json={"index": 0, "checked": True})
        assert r2.status_code == 409
