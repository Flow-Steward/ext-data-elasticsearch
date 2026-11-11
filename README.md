# Elasticsearch for Flow Steward

Connect Elasticsearch to Flow Steward for document lookup, full-text retrieval and similarity search. Workflow actions get documents by ID, search selected source fields, create or replace documents, merge partial updates and delete individual IDs. Search supports text matches, exact terms, ranges and Boolean filters, with sorting. Dense-vector search uses your existing indexed vector field and returns neighbors with scores and selected document content.

Read-only JSON Datasets provide mapping inspection, saved parameterized get/search queries, previews and output-schema inference within selected indices and fields. Included example workflows cover chunk ingestion, updates, deletion and answers with source citations. Supply your own document and question embeddings, and configure the project's answer model to compose a retrieval-and-answer workflow.

Create concrete indices and mappings before setup; this connector does not administer them. Search returns at most 100 hits and supports explicit top-level field selection. Aggregations, scripts, the bulk API and delete-by-query are outside its operation set. Elasticsearch's normal refresh interval controls search visibility after writes. Track chunk IDs externally when replacing or removing multi-chunk sources. The connector accesses Elasticsearch directly, without requiring Kibana.

## Runtime and operation requirements

Direct Elasticsearch document CRUD, bounded search, dense-vector retrieval and native read-only JSON Datasets. No Kibana or MCP server is required. Extension `flowsteward.elasticsearch` 1.0.4 uses the host-provided `flowsteward-extension-sdk==0.4.0` and Python standard library; no server, SDK wheel or client megadriver is bundled. Linux AMD64 and ARM64 runtime targets have separate installer ZIPs. No triggers are declared.

## Connect

Create an Elasticsearch project connection with non-secret configuration:

```json
{"origin":"https://elasticsearch.example.org:9200","indices":["knowledge-chunks"],"request_timeout_seconds":15}
```

Supply **either** secret `api_key` (the Elasticsearch encoded API key value) **or** both secret `username` and `password`. Authentication modes are exclusive. Do not put credentials in the origin. Origins have no path, query or fragment. Indices must be 1–32 distinct concrete lowercase names, starting with a letter or digit; wildcards, `_all`, comma lists, remote-cluster selectors and leading-dot names are rejected. Configure concrete indices; alias and data-stream resolution are not supported. Provider index privileges remain authoritative; use `read` for reads, `index`/`write` for document writes, `delete`/`write` for deletes, and `view_index_metadata` for mapping evidence and vector/metadata operations. No cluster-monitor privilege is required.

Public endpoints require HTTPS. A host administrator can permit private addresses with the SDK's `FS_ALLOW_PRIVATE_REMOTE_URLS` policy; private HTTP is accepted only when **all** resolved addresses are nonpublic. Public HTTP credentials are rejected. DNS policy checks every returned address and the SDK pins those addresses, preserves TLS hostname verification/SNI and rejects redirects. The Linux worker is terminated and joined at the complete operation deadline, including DNS, TLS, metadata and body reads. Unsupported platforms fail closed. There are no global socket settings or automatic retries.

## Workflow actions

Every connected action takes `connection_ref` and `index`. The selected project connection must match, and account/project scope is mandatory. All writes require the host's durable external-effect claim and are suppressed in test mode. A timeout after dispatch has unknown effect; inspect the exact document before deciding on a retry.

| Action | Inputs beyond connection/index | Behavior |
| --- | --- | --- |
| `get_document` | `document_id`, `source_fields` | Get one exact ID and explicit source fields; missing document returns `found:false`. |
| `search` | `source_fields`, optional `query`, `sort`, `size` | Closed Query DSL; default `match_all`, at most 100 hits. |
| `upsert_document` | `document_id`, `document` | `PUT /index/_doc/id`: create or fully replace the exact ID. |
| `update_document` | `document_id`, `doc`, optional `doc_as_upsert` | Partial document merge, no scripts. Upsert defaults to **false**. |
| `delete_document` | `document_id` | Delete one exact ID; missing document is a successful no-op. |
| `knn_search` | `field`, `query_vector`, `source_fields`, optional `k`, `num_candidates`, `filter` | Top-level `knn` in `_search`; verify indexed `dense_vector` mapping and dimensions. |

Explicit document IDs are UTF-8, nonempty and at most 512 bytes. Path segments are percent-encoded; `.` and `..` IDs are rejected. Explicit `source_fields` accepts top-level case-sensitive names, with no patterns or dotted paths. Output `documents` contains separate `document_id`, `score` and `source` objects, so metadata cannot overwrite stored fields. Search/source results are bounded to 100, requests to 8 MiB and JSON depth 20/10,000 nodes, raw provider responses to 1 MiB, host results to 480 KiB and complete operations to 1–30 seconds.

The supported query operators are `match_all:{}`, `term`, `terms` (at most 100 scalars), `range` (`gt/gte/lt/lte` only), `match` (plain string), and `bool` (`must`, `must_not`, `filter`, `should`: arrays of 1–32 clauses; integer `minimum_should_match`). Sort is an array of at most eight single-field objects whose direction is `asc` or `desc`. Example search inputs:

```json
{"connection_ref":"my-connection","index":"knowledge-chunks","query":{"bool":{"filter":[{"term":{"document_id":"manual"}}],"must":[{"match":{"text":"installation"}}]}},"source_fields":["document_id","chunk_id","text","source_uri","title"],"size":5}
```

Unknown keys/operators are rejected. No scripts, `query_string`, runtime mappings, stored fields, aggregations, rescore, index creation, mapping mutation, bulk API or delete-by-query are exposed. Dense vectors must contain 1–4096 finite numbers; `k` is 1–100 and `num_candidates` is `k`–10,000. The extension does **not** generate embeddings. Supply document/query embeddings from the same model and configuration. Invalid/unsupported vector mappings produce an explicit error; advanced vector field types and 9.x-only features are not supported.

## Operator setup for the reproducible example

An administrator creates the concrete index outside the extension, with suitable credentials and TLS. This small mapping uses synthetic three-dimensional vectors to make the example reproducible, not to claim useful semantic embeddings:

```http
PUT /knowledge-chunks
Content-Type: application/json

{"settings":{"number_of_shards":1,"number_of_replicas":0},"mappings":{"dynamic":false,"properties":{"document_id":{"type":"keyword"},"chunk_id":{"type":"keyword"},"source_uri":{"type":"keyword"},"title":{"type":"keyword"},"text":{"type":"text"},"vector":{"type":"dense_vector","dims":3,"index":true,"similarity":"cosine"}}}}
```

The disposable example's zero replicas are for a single-node test; operators choose production settings. Elasticsearch search is near real time: GET sees a write immediately, search sees it after the normal refresh interval. The extension never forces refreshes. Only Elasticsearch 8.19.24 is covered by the controlled real-server test; hosted/cloud deployments and other versions are not live-validated.

## RAG workflow bundle

Import the four workflows and accompanying agent/skill from `extension-descriptor.yaml`; select the project connection in each connected step. Configure the project's model provider for the answer agent. The artifacts use actual Core connector, agent, conditional and set-variable steps, checked by `scripts/validate_workflows.py`. No external embedding action is invented.

1. Call **Elasticsearch ingest chunk** (`elasticsearch_ingest_chunk`) with `index:"knowledge-chunks"`, `document_id:"manual"`, `chunk_id:"intro"`, `text:"The example service listens on port 9200."`, `source_uri:"https://example.org/manual"`, `title:"Manual"` and `vector:[1,0,0]`. The local `prepare_chunk` computes a stable SHA-256 physical ID from the exact document/chunk identifiers and returns a document preserving those identifiers and source metadata; `upsert_document` writes it. Save the returned `physical_id` in your ingestion manifest.
2. To replace a chunk, call the same ingestion workflow with the same document/chunk IDs, new text and a freshly supplied embedding; the physical ID stays stable. To change only metadata, call `elasticsearch_update_document` with the physical ID and `doc:{"title":"Updated manual"}`. Partial updates do not automatically recompute vectors.
3. After Elasticsearch's refresh, call **Elasticsearch ask** (`elasticsearch_ask`) with `index:"knowledge-chunks"`, `question:"Which port does the example service use?"`, and `query_vector:[1,0,0]`. kNN projects stored document/chunk IDs, source URI/title/text and score. `prepare_sources` labels excerpts `[S1]`, etc.; the project model answers only from that context. Empty retrieval bypasses the model and abstains. The local finalizer requires answer markers to match the known cited-source IDs exactly.
4. Call `elasticsearch_delete_document` with the saved physical ID. For a multi-chunk document, maintain the physical-ID manifest yourself and delete each removed/trailing chunk explicitly. No hidden scan or delete-by-query occurs. Whole-document replacement is therefore an explicit caller-managed lifecycle, not an atomic batch operation.

Source text is untrusted data, never instructions. `grounded:true` means citation identifiers are internally consistent; semantic entailment still depends on the model and evidence. No real or paid model request is made by the deterministic extension integration tests.

## Native Dataset / Data Query JSON contract

The descriptor advertises `supported_connectors`, `runtime_health` and the six native host actions: `test_connection`, `introspect_schema`, `validate_query`, `preview_query`, `execute_template`, `infer_output_schema`. Admin host calls may have null account/project IDs; workflow scope guards remain strict. `test_connection` reads an allowed index mapping and reports actual evidence without calling global cluster endpoints.

A saved query uses `query_language:"json"` and exactly these definition keys:

```json
{"resource_id":"knowledge-chunks","operation":"search","arguments":{"source_fields":["text","title"],"query":{"term":{"document_id":{"$param":"document"}}},"size":10}}
```

Declare `document` under the saved template's `param_schema_jsonb.properties` with type `string`, then execute with `params:{"document":"manual"}`. Markers have exactly one member `{"$param":"name"}` and bind real typed values recursively; text is never interpolated. Declared JSON types include string, integer, number, boolean, null, object and array. Every supplied value is revalidated, including bound query structures and scopes. With missing samples, schema validation reports `validation_complete:false`, `validation_status:"sample_parameters_required"` and `required_sample_params`; it does not execute or claim complete validation.

Dataset operations are only `get` and `search`; `get` takes the same `document_id` and `source_fields` arguments as the workflow read. Resource IDs must occur in both configured indices and `dataset.resource_scope_jsonb.resources`. Column lists enforce exact top-level case-sensitive references in source projection, query/filter and sort **before** request and on returned output. For vector retrieval use `operation:"search"` with arguments `{"source_fields":["text"],"knn":{"field":"vector","query_vector":[1,0,0],"k":5,"num_candidates":100,"filter":{"term":{"document_id":"manual"}}}}`. The vector field, filter and projection must all be in the Dataset column scope. Mapping/dimension validation and search share the same deadline. `knn` is exclusive with `query`, `sort` and `size`; hybrid OR semantics are deliberately not exposed. Dataset rows contain only selected stored source fields, preserving false, zero, null, objects and arrays; no full unscoped source object or colliding metadata field is inserted. JSON cells intentionally use `value_type:"json"` and `output_schema:{}`: mappings do not prove whether a field is scalar or multi-valued.

Metadata reads only configured/scoped indices, with a 100-column total bound. Nested/object and dotted mapping fields are skipped and the snapshot reports truncation/skipped fields. There is no global cluster discovery. Dataset contracts are bounded to 64 KiB/depth 20/10,000 nodes, rows to 100, complete output envelopes to 400 KiB or the lower requested limit, and the entire operation including metadata to at most 30 seconds. Limits accept `max_preview_rows`, `max_runtime_rows`, `max_bytes` and `timeout_seconds`.

## Development and packaging

All verification runs through Core CLI's disposable `fs-audit` container, not host pytest:

```sh
FS_COMPOSE_PROJECT_NAME=fs-nosql-elasticsearch \
FS_COMPOSE_FILES='infra/docker/docker-compose.yaml /path/to/sanitized-2g-override.yaml' \
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests \
  --extension-root /path/to/ext-data-elasticsearch \
  --extension-id flowsteward.elasticsearch --evidence-mode off
```

Live tests require a private, disposable **8.19.24** fixture with security enabled and a read-only `/tmp/fs-elasticsearch-live.json` audit-container fixture containing `origin` and ephemeral `password` (username `elastic`). They skip explicitly without it. Never point them at an existing index: they own and remove `chunks` in the isolated fixture. Installer archives are deterministic:

```sh
python3 scripts/package_extension.py --runtime-target linux-amd64
python3 scripts/package_extension.py --runtime-target linux-arm64
```

Each ZIP includes correct `.fs-package.yaml` target metadata and a pure Python payload, under the installer limits of 50 MiB compressed / 150 MiB extracted. `NOTICE` and `BRAND-ASSETS.md` identify the independent extension and the logo source.

API references: [8.19 Index API](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docs-index_.html), [Update API](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docs-update.html), [Search API](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/search-search.html), [kNN search](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/knn-search.html), [dense vectors](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/dense-vector.html), [Docker](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docker.html).

Native structured Dataset queries require extension host contract **1.5.0** (planned in Core app0.9.10). Application and host-contract version numbers are independent. Update Core before installing this release. Dataset queries are reads; document writes remain guarded workflow actions.
