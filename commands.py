"""Closed Elasticsearch DSL and exact single-document paths."""

from __future__ import annotations
import math
from urllib.parse import quote, urlencode
from validation import require, integer, text, field, index_name, bounded_json

WRITES = {"upsert_document", "update_document", "delete_document"}


def scalar(v):
    require(v is None or type(v) in {str, int, float, bool}, "Query values must be scalar.")
    if isinstance(v, str):
        text(v, "query value", 4096)
    bounded_json(v)


def query(v, check_field=field):
    require(isinstance(v, dict) and len(v) == 1, "One closed query operator required.")
    op, value = next(iter(v.items()))
    require(
        op in {"match_all", "term", "terms", "range", "match", "bool"},
        "Unsupported query operator.",
    )
    require(isinstance(value, dict), "Query operand must be an object.")
    if op == "match_all":
        require(not value, "match_all takes no controls.")
    elif op == "bool":
        require(
            bool(value)
            and set(value) <= {"must", "must_not", "filter", "should", "minimum_should_match"},
            "Unsupported bool control.",
        )
        for k, c in value.items():
            if k == "minimum_should_match":
                integer(c, "minimum_should_match", 0, 32)
            else:
                require(
                    isinstance(c, list) and 1 <= len(c) <= 32, "Bounded query clauses required."
                )
                for child in c:
                    query(child, check_field)
    else:
        require(len(value) == 1, "One field per query operator.")
        k, c = next(iter(value.items()))
        check_field(k)
        if op == "terms":
            require(isinstance(c, list) and 1 <= len(c) <= 100, "Bounded terms required.")
            for item in c:
                scalar(item)
        elif op == "range":
            require(
                isinstance(c, dict) and bool(c) and set(c) <= {"gt", "gte", "lt", "lte"},
                "Unsupported range control.",
            )
            for item in c.values():
                scalar(item)
        else:
            scalar(c)
            if op == "match":
                require(isinstance(c, str), "match requires text.")
    return v


def projection(v, check_field=field):
    require(isinstance(v, list) and 1 <= len(v) <= 100, "Explicit source_fields required.")
    require(len(set(v)) == len(v), "Duplicate fields.")
    for k in v:
        check_field(k)
    return v


def prepare(name, args, config, *, limit=100, check_field=field):
    allowed = {
        "get_document": {"document_id", "source_fields"},
        "search": {"query", "source_fields", "sort", "size"},
        "upsert_document": {"document_id", "document"},
        "update_document": {"document_id", "doc", "doc_as_upsert"},
        "delete_document": {"document_id"},
        "knn_search": {"field", "query_vector", "k", "num_candidates", "filter", "source_fields"},
    }
    require(name in allowed, "Unknown action.")
    require(set(args) <= allowed[name] | {"index", "connection_ref"}, "Unknown action input.")
    index = index_name(args.get("index"))
    require(index in config["indices"], "Index outside configured allowlist.")
    path = "/" + quote(index, safe="")
    if name in {"search", "knn_search"}:
        body = {
            "_source": projection(args.get("source_fields"), check_field),
            "size": min(
                integer(
                    args.get("size", args.get("k", 100 if name == "search" else 5)), "size", 1, 100
                ),
                limit,
            ),
            "track_total_hits": False,
        }
        if name == "search":
            body = {"query": query(args.get("query", {"match_all": {}}), check_field), **body}
            if "sort" in args:
                sort = args["sort"]
                require(isinstance(sort, list) and len(sort) <= 8, "Bounded sort required.")
                for item in sort:
                    require(
                        isinstance(item, dict) and len(item) == 1, "Explicit sort field required."
                    )
                    k, direction = next(iter(item.items()))
                    check_field(k)
                    require(direction in {"asc", "desc"}, "Invalid sort direction.")
                body["sort"] = sort
        else:
            k = integer(args.get("k", 5), "k", 1, 100)
            n = integer(args.get("num_candidates", 100), "num_candidates", k, 10000)
            f = args.get("field")
            check_field(f)
            vector = args.get("query_vector")
            require(
                isinstance(vector, list)
                and 1 <= len(vector) <= 4096
                and all(type(x) in {int, float} and math.isfinite(x) for x in vector),
                "Finite bounded vector required.",
            )
            knn = {"field": f, "query_vector": vector, "k": min(k, limit), "num_candidates": n}
            if "filter" in args:
                knn["filter"] = query(args["filter"], check_field)
            body["knn"] = knn
        bounded_json(body)
        return "POST", path + "/_search", body
    docid = text(args.get("document_id"), "document_id", 512, nonempty=True)
    require(docid not in {".", ".."}, "Invalid document ID.")
    path += ("/_update/" if name == "update_document" else "/_doc/") + quote(docid, safe="")
    if name == "get_document":
        fields = projection(args.get("source_fields"), check_field)
        return "GET", path + "?" + urlencode({"_source_includes": ",".join(fields)}), None
    if name == "delete_document":
        return "DELETE", path, None
    doc = args.get("document" if name == "upsert_document" else "doc")
    require(isinstance(doc, dict) and bool(doc), "Nonempty document required.")
    bounded_json(doc)
    if name == "update_document":
        upsert = args.get("doc_as_upsert", False)
        require(type(upsert) is bool, "doc_as_upsert must be boolean.")
        return "POST", path, {"doc": doc, "doc_as_upsert": upsert}
    return "PUT", path, doc


def documents(name, value, selected, limit):
    require(isinstance(value, dict), "Invalid provider object.", code="invalid_provider_response")
    if name in WRITES:
        require(
            value.get("result") in {"created", "updated", "deleted", "not_found", "noop"},
            "Invalid write result.",
            code="invalid_provider_response",
        )
        return {
            "applied": value["result"] not in {"not_found", "noop"},
            "document_id": value.get("_id"),
            "result": value["result"],
        }
    hits = (
        value.get("hits", {}).get("hits")
        if name in {"search", "knn_search"}
        else ([] if value.get("found") is False else [value])
    )
    require(
        isinstance(hits, list) and len(hits) <= limit,
        "Excessive provider hits.",
        code="response_too_large",
    )
    result = []
    for h in hits:
        require(
            isinstance(h, dict)
            and isinstance(h.get("_source"), dict)
            and set(h["_source"]) <= set(selected),
            "Provider source escaped projection.",
            code="invalid_provider_response",
        )
        require(
            isinstance(h.get("_id"), str), "Missing document ID.", code="invalid_provider_response"
        )
        result.append({"document_id": h["_id"], "score": h.get("_score"), "source": h["_source"]})
    bounded_json(result, 1024 * 1024)
    return {"documents": result, "count": len(result), "found": bool(result)}


def validate_vector_mapping(mapping, index, field_name, vector):
    require(
        isinstance(mapping, dict) and set(mapping) == {index},
        "Unexpected mapping.",
        code="invalid_provider_response",
    )
    m = mapping[index].get("mappings", {}).get("properties", {}).get(field_name, {})
    require(
        isinstance(m, dict) and m.get("type") == "dense_vector" and m.get("index", True),
        "Indexed dense_vector mapping required.",
        code="unsupported_vector_mapping",
    )
    require(
        "dims" not in m or m["dims"] == len(vector),
        "Vector dimension mismatch.",
        code="unsupported_vector_mapping",
    )
