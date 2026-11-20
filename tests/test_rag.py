"""RAG source identity and citation consistency through host actions."""

import json

import main
from test_runtime import envelope


def local(name, **args):
    p = envelope(name)
    p["action"]["input"] = args
    return main.handle(p)


def test_stable_chunk_identity_and_source_metadata():
    a = local(
        "prepare_chunk",
        document_id="manual",
        chunk_id="intro",
        text="Old",
        source_uri="https://example.org/manual",
        title="Manual",
        vector=[1, 0, 0],
    )
    b = local(
        "prepare_chunk",
        document_id="manual",
        chunk_id="intro",
        text="New",
        source_uri="https://example.org/manual",
        title="Manual",
        vector=[0, 1, 0],
    )
    assert a["ok"] and b["ok"], a
    assert a["result"]["document_id"] == b["result"]["document_id"]
    assert b["result"]["document"] == {
        "document_id": "manual",
        "chunk_id": "intro",
        "text": "New",
        "source_uri": "https://example.org/manual",
        "title": "Manual",
        "vector": [0, 1, 0],
    }


def test_retrieval_grounded_answer_and_empty_abstention():
    docs = [
        {
            "document_id": "physical",
            "score": 1,
            "source": {
                "document_id": "manual",
                "chunk_id": "intro",
                "text": "Earth is round.",
                "source_uri": "https://example.org/manual",
                "title": "Manual",
            },
        }
    ]
    r = local("prepare_sources", documents=docs)
    assert r["ok"], r
    source = r["result"]["sources"][0]
    token = source["citation_token"]
    assert json.loads(r["result"]["context"]) == [
        {"citation_token": token, "excerpt": "Earth is round."},
    ]
    f = local(
        "finalize_answer",
        answer=f"Round [{token}]",
        cited_source_ids=[token],
        sources=r["result"]["sources"],
    )
    assert f["ok"] and f["result"]["grounded"]
    assert f["result"]["answer"] == "Round [S1]"
    assert "citation_token" not in f["result"]["sources"][0]
    assert f["result"]["sources"][0]["source_uri"] == "https://example.org/manual"
    assert not local(
        "finalize_answer",
        answer="Round [S2]",
        cited_source_ids=["S2"],
        sources=r["result"]["sources"],
    )["ok"]
    f = local("finalize_answer", answer="", cited_source_ids=[], sources=[])
    assert f["ok"] and f["result"]["abstained"]


def test_excerpt_markers_are_text_and_tokens_are_fresh():
    docs = [
        {"source": {"text": 'Sample text mentioning "[S2]".'}},
        {"source": {"text": "Separate reference."}},
    ]
    first = local("prepare_sources", documents=docs)["result"]
    second = local("prepare_sources", documents=docs)["result"]
    entries = json.loads(first["context"])
    assert entries[0]["excerpt"] == docs[0]["source"]["text"]
    assert [entry["citation_token"] for entry in entries] != [
        source["citation_token"] for source in second["sources"]
    ]
    assert not local(
        "finalize_answer", answer="Quoted [S2]", cited_source_ids=["S2"], sources=first["sources"]
    )["ok"]


def test_serialized_context_respects_budget_even_when_text_needs_escaping():
    from citations import build_context

    result = build_context([{"payload": {"text": 'Quoted "chapter".\n' * 10}}], max_characters=100)
    entries = json.loads(result["context"])
    assert len(result["context"]) <= 100
    assert result["truncated"]
    assert entries[0]["excerpt"] == result["sources"][0]["excerpt"]


def test_insufficient_evidence_passes_core_output_contract_and_citation_bridge():
    import json
    import os
    from pathlib import Path
    import subprocess

    root = Path(__file__).resolve().parents[1]
    response = subprocess.run(
        [
            "/usr/local/bin/python",
            str(root / "scripts/validate_workflows.py"),
            str(root),
            "--abstention-only",
        ],
        env={**os.environ, "PYTHONPATH": "/app"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert response.returncode == 0, response.stdout + response.stderr
    assert json.loads(response.stdout) == {
        "answer": "I do not have enough retrieved evidence to answer this question.",
        "sources": [],
        "citation_count": 0,
        "grounded": False,
        "abstained": True,
    }
