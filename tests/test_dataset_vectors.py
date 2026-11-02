"""Native JSON search supports only explicitly scoped, supplied-vector kNN."""

import pytest
import main
import transport
from test_runtime import dataset, fake


def vector_request(name="preview_query"):
    p = dataset(
        name,
        {
            "source_fields": ["text"],
            "knn": {
                "field": "vector",
                "query_vector": [1, 0, 0],
                "k": 2,
                "num_candidates": 10,
                "filter": {"term": {"value": False}},
            },
        },
    )
    p["action"]["input"]["dataset"]["resource_scope_jsonb"]["resources"][0]["columns"] = [
        "text",
        "value",
        "vector",
    ]
    return p


def vector_network(monkeypatch, mapping=None):
    calls = []

    def invoke(config, secrets, method, path, body=None, **kw):
        calls.append((method, path, body))
        if path.endswith("/_mapping"):
            return {
                "chunks": {
                    "mappings": {
                        "properties": {
                            "vector": mapping or {"type": "dense_vector", "dims": 3, "index": True}
                        }
                    }
                }
            }
        return {"hits": {"hits": [{"_id": "one", "_score": 1, "_source": {"text": "earth"}}]}}

    monkeypatch.setattr(transport, "invoke", invoke)
    return calls


def test_dataset_knn_scoped_mapping_and_query(monkeypatch):
    calls = vector_network(monkeypatch)
    v = main.handle(vector_request())
    assert v["ok"], v
    assert v["result"]["rows"] == [{"text": "earth"}]
    assert calls == [
        ("GET", "/chunks/_mapping", None),
        (
            "POST",
            "/chunks/_search",
            {
                "_source": ["text"],
                "size": 2,
                "track_total_hits": False,
                "knn": {
                    "field": "vector",
                    "query_vector": [1, 0, 0],
                    "k": 2,
                    "num_candidates": 10,
                    "filter": {"term": {"value": False}},
                },
            },
        ),
    ]


@pytest.mark.parametrize("part", ["field", "filter", "projection"])
def test_dataset_vector_scopes_precede_any_request(monkeypatch, part):
    calls = vector_network(monkeypatch)
    p = vector_request()
    a = p["action"]["input"]["query_template"]["query_definition_jsonb"]["arguments"]
    if part == "field":
        a["knn"]["field"] = "secret"
    elif part == "filter":
        a["knn"]["filter"] = {"term": {"secret": "hidden"}}
    else:
        a["source_fields"] = ["secret"]
    assert not main.handle(p)["ok"]
    assert not calls


def test_dataset_vector_dimension_validation(monkeypatch):
    calls = vector_network(monkeypatch, {"type": "dense_vector", "dims": 4, "index": True})
    v = main.handle(vector_request())
    assert v["error_code"] == "unsupported_vector_mapping"
    assert len(calls) == 1


def test_dataset_complete_input_contract_budget(monkeypatch):
    calls = fake(monkeypatch)
    p = dataset(
        arguments={"source_fields": ["text"], "query": {"terms": {"value": ["x" * 900] * 64}}}
    )
    p["action"]["input"]["secret_refs"]["api_key"] = "k" * 8192
    assert not main.handle(p)["ok"]
    assert not calls


@pytest.mark.parametrize("control", ["query", "sort", "size"])
def test_dataset_knn_rejects_ambiguous_hybrid_controls(monkeypatch, control):
    calls = vector_network(monkeypatch)
    p = vector_request()
    a = p["action"]["input"]["query_template"]["query_definition_jsonb"]["arguments"]
    a[control] = {"match_all": {}} if control == "query" else [] if control == "sort" else 2
    assert not main.handle(p)["ok"]
    assert not calls


@pytest.mark.parametrize("location", ["arguments", "knn"])
def test_dataset_cannot_override_scoped_resource_inside_arguments(monkeypatch, location):
    calls = vector_network(monkeypatch)
    p = (
        vector_request()
        if location == "knn"
        else dataset(arguments={"source_fields": ["text"], "index": "secret-index"})
    )
    p["action"]["input"]["connection_config"]["indices"].append("secret-index")
    if location == "knn":
        p["action"]["input"]["query_template"]["query_definition_jsonb"]["arguments"]["knn"][
            "index"
        ] = "secret-index"
    v = main.handle(p)
    assert not v["ok"]
    assert not calls
