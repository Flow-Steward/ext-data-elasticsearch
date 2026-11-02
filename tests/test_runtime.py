"""Host protocol and Elasticsearch closed-DSL regressions."""

import pytest
import main
import transport


def envelope(name="search", **args):
    return {
        "mode": "action",
        "contract_version": "extension_host_v2",
        "runtime_context": {
            "account_id": "account",
            "project_id": "project",
            "workflow_id": "workflow",
            "job_id": "job",
            "step_id": "step",
            "step_execution_id": "execution",
            "group_id": None,
            "for_each_id": None,
            "test_mode": False,
            "external_effect_id": "claim",
        },
        "action": {
            "action_id": name,
            "input": {
                "connection_ref": "conn",
                "index": "chunks",
                "source_fields": ["text"],
                **args,
            },
            "target": {
                "connection": {
                    "connection_id": "conn",
                    "connection_type": "elasticsearch",
                    "config": {"origin": "https://es.example", "indices": ["chunks"]},
                    "secrets": {"api_key": "dummy"},
                }
            },
        },
    }


def fake(monkeypatch, value=None):
    calls = []

    def invoke(config, secrets, method, path, body=None, **kw):
        calls.append((method, path, body))
        return (
            value
            if value is not None
            else {"hits": {"hits": [{"_id": "one", "_score": 1.0, "_source": {"text": "hello"}}]}}
        )

    monkeypatch.setattr(transport, "invoke", invoke)
    return calls


def test_search_and_projection(monkeypatch):
    calls = fake(monkeypatch)
    v = main.handle(envelope())
    assert v["ok"], v
    assert v["result"]["documents"] == [
        {"document_id": "one", "score": 1.0, "source": {"text": "hello"}}
    ]
    assert calls == [
        (
            "POST",
            "/chunks/_search",
            {
                "query": {"match_all": {}},
                "_source": ["text"],
                "size": 100,
                "track_total_hits": False,
            },
        )
    ]


@pytest.mark.parametrize(
    "name,extra,method,path,body",
    [
        (
            "get_document",
            {"document_id": "a/b?x"},
            "GET",
            "/chunks/_doc/a%2Fb%3Fx?_source_includes=text",
            None,
        ),
        (
            "upsert_document",
            {"document_id": "one", "document": {"text": "x"}},
            "PUT",
            "/chunks/_doc/one",
            {"text": "x"},
        ),
        (
            "update_document",
            {"document_id": "one", "doc": {"text": "y"}},
            "POST",
            "/chunks/_update/one",
            {"doc": {"text": "y"}, "doc_as_upsert": False},
        ),
        ("delete_document", {"document_id": "one"}, "DELETE", "/chunks/_doc/one", None),
    ],
)
def test_document_operations(monkeypatch, name, extra, method, path, body):
    calls = fake(
        monkeypatch,
        {"_id": "one", "_source": {"text": "hello"}, "found": True, "result": "updated"},
    )
    p = envelope(name, **extra)
    if name != "get_document":
        p["action"]["input"].pop("source_fields")
    assert main.handle(p)["ok"]
    assert calls == [(method, path, body)]


@pytest.mark.parametrize(
    "query",
    [
        {"script": {}},
        {"query_string": {"query": "*"}},
        {"term": {"text": {"value": "x"}}},
        {"match_all": {"boost": 10}},
        {"bool": {"unknown": []}},
    ],
)
def test_query_injection_rejected(monkeypatch, query):
    calls = fake(monkeypatch)
    assert not main.handle(envelope(query=query))["ok"]
    assert not calls


@pytest.mark.parametrize(
    "key,value",
    [
        ("index", "*"),
        ("index", "chunks/../secret"),
        ("source_fields", ["secret.*"]),
        ("sort", [{"secret.path": "asc"}]),
        ("size", 101),
        ("runtime_mappings", {}),
    ],
)
def test_closed_arguments(monkeypatch, key, value):
    calls = fake(monkeypatch)
    assert not main.handle(envelope(**{key: value}))["ok"]
    assert not calls


@pytest.mark.parametrize("change", ["test_mode", "claim", "account", "project", "connection"])
def test_write_guards(monkeypatch, change):
    calls = fake(monkeypatch)
    p = envelope("delete_document", document_id="one")
    p["action"]["input"].pop("source_fields")
    if change == "test_mode":
        p["runtime_context"]["test_mode"] = True
    if change == "claim":
        p["runtime_context"].pop("external_effect_id")
    if change in {"account", "project"}:
        p["runtime_context"][change + "_id"] = None
    if change == "connection":
        p["action"]["input"]["connection_ref"] = "foreign"
    v = main.handle(p)
    assert not v["ok"] and v["definitely_no_external_effect"]
    assert not calls


def dataset(name="preview_query", arguments=None):
    p = envelope(name)
    p["runtime_context"].update(
        account_id=None, project_id=None, step_id=None, step_execution_id=None
    )
    p["action"]["input"] = {
        "connection_config": {"origin": "https://es.example", "indices": ["chunks"]},
        "secret_refs": {"api_key": "dummy"},
        "dataset": {
            "dataset_id": "d",
            "resource_scope_jsonb": {
                "resources": [{"resource_id": "chunks", "columns": ["text", "value"]}]
            },
        },
        "query_template": {
            "query_template_id": "q",
            "dataset_id": "d",
            "query_language": "json",
            "query_definition_jsonb": {
                "resource_id": "chunks",
                "operation": "search",
                "arguments": arguments or {"source_fields": ["value"]},
            },
        },
    }
    return p


@pytest.mark.parametrize("value", [False, 0, None, {"nested": 1}, [1, 2]])
def test_dataset_preserves_typed_json(monkeypatch, value):
    fake(
        monkeypatch,
        {"hits": {"hits": [{"_id": "one", "_score": None, "_source": {"value": value}}]}},
    )
    v = main.handle(dataset())
    assert v["ok"], v
    assert v["result"]["rows"] == [{"value": value}]
    assert v["result"]["output_schema"]["properties"]["value"] == {}


@pytest.mark.parametrize(
    "arguments",
    [
        {"source_fields": ["secret"]},
        {"source_fields": ["text"], "query": {"term": {"secret": "x"}}},
        {"source_fields": ["text"], "sort": [{"secret": "asc"}]},
        {"source_fields": ["text"], "knn": {"field": "secret"}},
    ],
)
def test_dataset_scope_before_request(monkeypatch, arguments):
    calls = fake(monkeypatch)
    assert not main.handle(dataset(arguments=arguments))["ok"]
    assert not calls


def test_dataset_no_unscoped_result(monkeypatch):
    fake(monkeypatch, {"hits": {"hits": [{"_source": {"value": 1, "secret": "x"}, "_id": "one"}]}})
    v = main.handle(dataset())
    assert not v["ok"]


def test_typed_parameter_and_unbound_validation(monkeypatch):
    calls = fake(monkeypatch)
    p = dataset(
        "validate_query", {"source_fields": ["text"], "query": {"term": {"value": {"$param": "v"}}}}
    )
    p["action"]["input"]["query_template"]["param_schema_jsonb"] = {
        "properties": {"v": {"type": "boolean"}}
    }
    v = main.handle(p)
    assert v["ok"], v
    assert v["result"]["validation_complete"] is False
    assert v["result"]["validation_status"] == "sample_parameters_required"
    assert not calls
    p["action"]["input"]["params"] = {"v": False}
    p["action"]["action_id"] = "preview_query"
    assert main.handle(p)["ok"]
    assert calls[0][2]["query"] == {"term": {"value": False}}


def test_catalog_null_admin_scope():
    p = dataset()
    p.update(mode="query", query={"query_id": "supported_connectors"})
    v = main.handle(p)
    assert v["ok"], v
    assert v["result"]["connectors"][0]["query_language"] == "json"


def test_unbound_structural_parameters_require_samples(monkeypatch):
    calls = fake(monkeypatch)
    p = dataset(
        "validate_query",
        {
            "source_fields": {"$param": "fields"},
            "query": {"$param": "query"},
            "sort": {"$param": "sort"},
            "size": {"$param": "size"},
        },
    )
    p["action"]["input"]["query_template"]["param_schema_jsonb"] = {
        "properties": {
            "fields": {"type": "array"},
            "query": {"type": "object"},
            "sort": {"type": "array"},
            "size": {"type": "integer"},
        }
    }
    v = main.handle(p)
    assert v["ok"], v
    assert v["result"]["validation_complete"] is False
    assert v["result"]["required_sample_params"] == ["fields", "query", "size", "sort"]
    assert not calls
    p["action"]["action_id"] = "execute_template"
    p["action"]["input"]["params"] = {
        "fields": ["text"],
        "query": {"term": {"secret": "x"}},
        "sort": [],
        "size": 1,
    }
    assert not main.handle(p)["ok"]
    assert not calls


def test_dataset_budget_counts_complete_envelope(monkeypatch):
    # A compact result alone fits 1024, but its complete host envelope does not.
    fake(monkeypatch, {"hits": {"hits": [{"_id": "one", "_source": {"value": "x" * 870}}]}})
    p = dataset()
    p["action"]["input"]["limits"] = {"max_bytes": 1024}
    v = main.handle(p)
    assert not v["ok"]
