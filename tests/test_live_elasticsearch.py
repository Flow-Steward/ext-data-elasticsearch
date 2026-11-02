"""Controlled real Elasticsearch 8.19.24 integration; opt in via fixture env."""

from __future__ import annotations
import os
import json
from pathlib import Path
import time
import pytest
import main
import transport
from test_runtime import envelope, dataset
from validation import connection_values

FIXTURE = Path("/tmp/fs-elasticsearch-live.json")
pytestmark = pytest.mark.skipif(
    not FIXTURE.is_file(), reason="Controlled Elasticsearch fixture not configured"
)


def conn():
    return {
        "connection_id": "conn",
        "connection_type": "elasticsearch",
        "config": {
            "origin": json.loads(FIXTURE.read_text())["origin"],
            "indices": ["chunks"],
            "request_timeout_seconds": 10,
        },
        "secrets": {"username": "elastic", "password": json.loads(FIXTURE.read_text())["password"]},
    }


def invoke(name, **args):
    p = envelope(name, **args)
    p["action"]["target"]["connection"] = conn()
    if name in {"upsert_document", "update_document", "delete_document"}:
        p["action"]["input"].pop("source_fields", None)
    v = main.handle(p)
    assert v["ok"], {"code": v.get("error_code"), "operation": name}
    return v["result"]


def admin(name="preview_query", arguments=None):
    p = dataset(name, arguments)
    c = conn()
    p["action"]["input"].update(connection_config=c["config"], secret_refs=c["secrets"])
    return p


@pytest.fixture(scope="module", autouse=True)
def setup_index():
    previous = os.environ.get("FS_ALLOW_PRIVATE_REMOTE_URLS")
    os.environ["FS_ALLOW_PRIVATE_REMOTE_URLS"] = "true"
    c, s = connection_values(conn())
    last = None
    for _ in range(90):
        try:
            info = transport.invoke(c, s, "GET", "/", timeout_seconds=2)
            assert info["version"]["number"] == "8.19.24"
            break
        except Exception as exc:
            last = type(exc).__name__
            time.sleep(1)
    else:
        pytest.fail("Controlled fixture readiness deadline: " + str(last))
    transport.invoke(
        c,
        s,
        "PUT",
        "/chunks",
        {
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "dynamic": False,
                "properties": {
                    "text": {"type": "text"},
                    "document_id": {"type": "keyword"},
                    "chunk_id": {"type": "keyword"},
                    "source_uri": {"type": "keyword"},
                    "title": {"type": "keyword"},
                    "flag": {"type": "boolean"},
                    "n": {"type": "integer"},
                    "vector": {
                        "type": "dense_vector",
                        "dims": 3,
                        "index": True,
                        "similarity": "cosine",
                    },
                },
            },
        },
        timeout_seconds=10,
    )
    yield
    try:
        transport.invoke(c, s, "DELETE", "/chunks", timeout_seconds=10)
    finally:
        if previous is None:
            os.environ.pop("FS_ALLOW_PRIVATE_REMOTE_URLS", None)
        else:
            os.environ["FS_ALLOW_PRIVATE_REMOTE_URLS"] = previous


def safe_request(method, path, body=None):
    c, s = connection_values(conn())
    try:
        return transport.invoke(c, s, method, path, body, timeout_seconds=10)
    except Exception as exc:
        pytest.fail(
            "Controlled Elasticsearch fixture request failed: "
            + getattr(exc, "code", type(exc).__name__),
            pytrace=False,
        )


def refresh():
    safe_request("POST", "/chunks/_refresh")


def test_real_document_crud_and_vector():
    invoke(
        "upsert_document",
        document_id="live/a?b",
        document={
            "text": "Earth is round",
            "vector": [1, 0, 0],
            "document_id": "manual",
            "chunk_id": "intro",
            "source_uri": "https://example.org/manual",
            "title": "Manual",
        },
    )
    v = invoke("get_document", document_id="live/a?b")
    assert v["documents"][0]["source"] == {"text": "Earth is round"}
    invoke("update_document", document_id="live/a?b", doc={"text": "Earth is approximately round"})
    refresh()
    v = invoke("search", query={"match": {"text": "Earth"}}, size=5)
    assert v["documents"][0]["source"]["text"] == "Earth is approximately round"
    v = invoke(
        "knn_search",
        field="vector",
        query_vector=[1, 0, 0],
        k=2,
        num_candidates=10,
        source_fields=["document_id", "chunk_id", "text", "source_uri", "title"],
    )
    assert (
        v["count"] == 1
        and v["documents"][0]["source"]["source_uri"] == "https://example.org/manual"
    )
    p = envelope("knn_search", field="vector", query_vector=[1, 0], k=2)
    p["action"]["target"]["connection"] = conn()
    assert main.handle(p)["error_code"] == "unsupported_vector_mapping"
    invoke("delete_document", document_id="live/a?b")
    assert not invoke("get_document", document_id="live/a?b")["found"]


@pytest.mark.parametrize("value", [False, 0, None, {"a": 1}, [1, 2]])
def test_real_dataset_json_and_typed_parameter(value):
    invoke(
        "upsert_document",
        document_id="typed",
        document={"text": "typed", "value": value, "flag": False, "n": 0},
    )
    refresh()
    p = admin(
        arguments={"source_fields": ["value"], "query": {"term": {"flag": {"$param": "flag"}}}}
    )
    p["action"]["input"]["dataset"]["resource_scope_jsonb"]["resources"][0]["columns"] = [
        "value",
        "flag",
    ]
    p["action"]["input"]["query_template"]["param_schema_jsonb"] = {
        "properties": {"flag": {"type": "boolean"}}
    }
    p["action"]["input"]["params"] = {"flag": False}
    v = main.handle(p)
    assert v["ok"], v.get("error_code")
    assert v["result"]["rows"] == [{"value": value}]
    p["action"]["action_id"] = "infer_output_schema"
    assert main.handle(p)["result"]["output_schema"]["properties"]["value"] == {}
    invoke("delete_document", document_id="typed")


def test_real_dataset_admin_metadata_scope_budget_and_write_guards():
    p = admin("test_connection")
    assert main.handle(p)["ok"]
    p = admin("introspect_schema")
    p["action"]["input"].pop("dataset")
    v = main.handle(p)
    assert v["ok"] and [r["resource_id"] for r in v["result"]["resources"]] == ["chunks"]
    invoke(
        "upsert_document", document_id="budget", document={"value": "x" * 2000, "text": "bounded"}
    )
    refresh()
    p = admin()
    p["action"]["input"]["limits"] = {"max_bytes": 1024}
    assert not main.handle(p)["ok"]
    p = admin(arguments={"source_fields": ["text"], "query": {"term": {"secret": "hidden"}}})
    assert not main.handle(p)["ok"]
    p = envelope("delete_document", document_id="budget")
    p["action"]["input"].pop("source_fields")
    p["action"]["target"]["connection"] = conn()
    p["runtime_context"]["test_mode"] = True
    assert main.handle(p)["error_code"] == "test_mode_suppressed"
    assert invoke("get_document", document_id="budget")["found"]
    p["runtime_context"]["test_mode"] = False
    p["runtime_context"].pop("external_effect_id")
    assert main.handle(p)["error_code"] == "effect_claim_required"
    assert invoke("get_document", document_id="budget")["found"]
    invoke("delete_document", document_id="budget")


def test_real_native_dataset_knn_scope_and_parameters():
    invoke(
        "upsert_document",
        document_id="native-vector",
        document={"text": "Native vector evidence", "vector": [1, 0, 0], "flag": False},
    )
    refresh()
    p = admin(
        arguments={
            "source_fields": ["text"],
            "knn": {
                "field": "vector",
                "query_vector": {"$param": "vector"},
                "k": 2,
                "num_candidates": 10,
                "filter": {"term": {"flag": False}},
            },
        }
    )
    p["action"]["input"]["dataset"]["resource_scope_jsonb"]["resources"][0]["columns"] = [
        "text",
        "vector",
        "flag",
    ]
    p["action"]["input"]["query_template"]["param_schema_jsonb"] = {
        "properties": {"vector": {"type": "array"}}
    }
    p["action"]["input"]["params"] = {"vector": [1, 0, 0]}
    v = main.handle(p)
    assert v["ok"], v.get("error_code")
    assert v["result"]["rows"] == [{"text": "Native vector evidence"}]
    p["action"]["input"]["params"] = {"vector": [1, 0]}
    assert main.handle(p)["error_code"] == "unsupported_vector_mapping"
    p["action"]["input"]["params"] = {"vector": [1, 0, 0]}
    p["action"]["input"]["dataset"]["resource_scope_jsonb"]["resources"][0]["columns"] = [
        "text",
        "flag",
    ]
    assert not main.handle(p)["ok"]
    invoke("delete_document", document_id="native-vector")
