"""Connection, Dataset and response boundary regressions."""

from __future__ import annotations
import pytest
import main
from test_runtime import dataset, envelope, fake


@pytest.mark.parametrize(
    "origin",
    [
        "https://user:pass@es.example",
        "https://es.example/path",
        "https://es.example?x=1",
        "https://es.example#fragment",
        "file:///tmp/a",
        "http://es.example:bad",
        "https://es.example\\bad",
    ],
)
def test_connection_origin_rejects_endpoint_injection(monkeypatch, origin):
    calls = fake(monkeypatch)
    p = envelope()
    p["action"]["target"]["connection"]["config"]["origin"] = origin
    assert main.handle(p)["error_code"] == "invalid_connection"
    assert not calls


@pytest.mark.parametrize(
    "indices",
    [
        [],
        ["*"],
        ["_all"],
        ["Chunks"],
        ["chunks,other"],
        ["remote:chunks"],
        ["chunks", "chunks"],
        ["chunks"] * 33,
    ],
)
def test_concrete_index_allowlist(monkeypatch, indices):
    calls = fake(monkeypatch)
    p = envelope()
    p["action"]["target"]["connection"]["config"]["indices"] = indices
    assert main.handle(p)["error_code"] == "invalid_connection"
    assert not calls


@pytest.mark.parametrize(
    "secrets",
    [
        {},
        {"api_key": "a", "username": "u", "password": "p"},
        {"username": "u"},
        {"api_key": "a\r\nX: y"},
        {"username": "u", "password": ""},
    ],
)
def test_exclusive_secret_auth_modes(monkeypatch, secrets):
    calls = fake(monkeypatch)
    p = envelope()
    p["action"]["target"]["connection"]["secrets"] = secrets
    assert main.handle(p)["error_code"] == "invalid_connection"
    assert not calls


def test_dataset_mapping_scopes_and_unsupported_fields(monkeypatch):
    fake(
        monkeypatch,
        {
            "chunks": {
                "mappings": {
                    "properties": {
                        "text": {"type": "text"},
                        "value": {"type": "keyword"},
                        "nested": {"type": "nested", "properties": {"secret": {"type": "keyword"}}},
                        "dotted.field": {"type": "keyword"},
                    }
                }
            }
        },
    )
    p = dataset("introspect_schema")
    p["action"]["input"].pop("dataset")
    v = main.handle(p)
    assert v["ok"], v
    resource = v["result"]["resources"][0]
    assert [c["name"] for c in resource["columns"]] == ["text", "value"]
    assert all(c["value_type"] == "json" and c["output_schema"] == {} for c in resource["columns"])
    assert v["result"]["truncated"]
    assert v["result"]["schema_snapshot"]["metadata"]["skipped_unsupported_fields"] == 2


def test_dataset_mapping_cannot_discover_unconfigured_index(monkeypatch):
    fake(monkeypatch, {"secret": {"mappings": {}}})
    p = dataset("introspect_schema")
    p["action"]["input"].pop("dataset")
    assert not main.handle(p)["ok"]


def test_partial_update_never_accepts_scripts(monkeypatch):
    calls = fake(monkeypatch)
    p = envelope("update_document", document_id="one", doc={"text": "x"}, script={"source": "bad"})
    p["action"]["input"].pop("source_fields")
    assert not main.handle(p)["ok"]
    assert not calls


def test_uncertain_write_is_not_retryable(monkeypatch):
    from validation import Failure
    import transport

    def fail(*a, **kw):
        raise Failure("timeout", "Operation deadline exceeded.", dispatched=True)

    monkeypatch.setattr(transport, "invoke", fail)
    p = envelope("delete_document", document_id="one")
    p["action"]["input"].pop("source_fields")
    v = main.handle(p)
    assert not v["ok"] and v["retryable"] is False and v["definitely_no_external_effect"] is False
    assert v["external_effect_status"] == "timeout_unknown"


def test_dataset_query_is_read_only_even_with_valid_template(monkeypatch):
    calls = fake(monkeypatch)
    p = dataset()
    p["action"]["input"]["query_template"]["query_definition_jsonb"]["operation"] = (
        "delete_document"
    )
    assert not main.handle(p)["ok"]
    assert not calls


def test_dataset_preserves_metadata_named_source_columns(monkeypatch):
    fake(
        monkeypatch,
        {
            "hits": {
                "hits": [{"_id": "provider", "_score": 1, "_source": {"_id": "stored", "score": 0}}]
            }
        },
    )
    p = dataset(arguments={"source_fields": ["_id", "score"]})
    p["action"]["input"]["dataset"]["resource_scope_jsonb"]["resources"][0]["columns"] = [
        "_id",
        "score",
    ]
    v = main.handle(p)
    assert v["ok"], v
    assert v["result"]["rows"] == [{"_id": "stored", "score": 0}]


def test_dataset_row_limit_applies_to_complete_provider_result(monkeypatch):
    fake(
        monkeypatch,
        {"hits": {"hits": [{"_id": str(i), "_source": {"value": i}} for i in range(3)]}},
    )
    p = dataset()
    p["action"]["input"]["limits"] = {"max_preview_rows": 2}
    assert not main.handle(p)["ok"]


@pytest.mark.parametrize(
    "origin",
    ["https://@es.example", "https://es.example?", "https://es.example#", "https://es.example:0"],
)
def test_origin_rejects_empty_credentials_controls_and_zero_port(monkeypatch, origin):
    calls = fake(monkeypatch)
    p = envelope()
    p["action"]["target"]["connection"]["config"]["origin"] = origin
    assert main.handle(p)["error_code"] == "invalid_connection"
    assert not calls
