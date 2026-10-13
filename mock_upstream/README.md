# mock_upstream

Deterministic, OpenAI-compatible upstream stub for routing-lab. It impersonates
the upstream that the LiteLLM proxy under test routes to and injects faults
based on the requested model name.

The lab calls this service with `litellm_params.model: "openai/<mode>"` and
`api_base: "http://mock-upstream:8000/v1"`. LiteLLM strips the `openai/`
prefix, so this service normally receives `"model": "<mode>"`. The mode is read
as the substring after the last `/`, so a stray prefix (`openai/healthy`)
still resolves. Everything is internal-only (no host ports); the container
listens on port 8000.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/v1/chat/completions` | Chat completions. Supports `"stream": true` (SSE) and `"stream": false` (single JSON). |
| `GET` | `/v1/models` | OpenAI-style model list containing the 8 mode ids. |
| `GET` | `/healthz` | Liveness probe: `{"status": "ok"}`. |
| `GET` | `/__hits` | Request journal: JSON array of chat-completion requests, oldest first. |
| `DELETE` | `/__hits` | Clears the journal. Returns `{"cleared": true}`. |

Only `/v1/chat/completions` requests are journaled; health and model-list
probes are not.

## Modes

The `model` field of the request selects the behavior. Unknown models get an
immediate `404` `model_not_found`.

| Mode | Streaming behavior | Non-stream behavior |
| --- | --- | --- |
| `healthy` | 5 content chunks, ~50 ms apart, then a `"finish_reason": "stop"` chunk, then `data: [DONE]`. | Standard `chat.completion` JSON. |
| `stall-pre` | Response headers sent immediately; first chunk delayed by `STALL_PRE_SECONDS`; then a short completion. | Waits `STALL_PRE_SECONDS`, then JSON. |
| `stall-mid` | `STALL_MID_CHUNKS` chunks sent quickly, then silence for `STALL_MID_SECONDS`; then the stream finishes. | Waits `STALL_MID_SECONDS`, then JSON. |
| `slow-steady` | `SLOW_CHUNKS` chunks, one every `SLOW_GAP_MS` (defaults: 12 x 800 ms ~= 10 s total). | Waits `SLOW_CHUNKS * SLOW_GAP_MS`, then JSON. |
| `err-429` | Immediate HTTP 429 (identical for stream and non-stream). | |
| `err-503` | Immediate HTTP 503. | |
| `err-500` | Immediate HTTP 500. | |
| `err-404` | Immediate HTTP 404 (`model_not_found`). | |

Error bodies are OpenAI-shaped:
`{"error": {"message": ..., "type": ..., "code": ...}}` using types
`rate_limit_error` / `service_unavailable_error` / `internal_server_error` /
`invalid_request_error` and codes `rate_limit_exceeded` / `service_unavailable`
/ `internal_server_error` / `model_not_found`.

Stall and slow modes are designed for streaming scenarios (`"stream": true`);
for non-stream requests they wait the equivalent total delay and then return
the plain completion, so both request shapes are safe to call.

SSE chunks have the shape:

```
{"id":"chatcmpl-mock-<n>","object":"chat.completion.chunk","created":<unix int>,
 "model":"<received model>","choices":[{"index":0,"delta":{"content":"..."},
 "finish_reason":null}]}
```

(one JSON object per `data:` line, each terminated by a blank line).

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `STALL_PRE_SECONDS` | `30` | `stall-pre`: delay before the first SSE chunk. |
| `STALL_MID_SECONDS` | `30` | `stall-mid`: silence held after the initial chunks. |
| `STALL_MID_CHUNKS` | `3` | `stall-mid`: number of content chunks sent before the silence. |
| `SLOW_CHUNKS` | `12` | `slow-steady`: number of content chunks. |
| `SLOW_GAP_MS` | `800` | `slow-steady`: milliseconds between chunks. |

## Request journal

Every completed, aborted, or failed `/v1/chat/completions` request appends one
entry:

| Field | Type | Meaning |
| --- | --- | --- |
| `ts` | float | Unix timestamp when the request was received. |
| `model` | string | The raw `model` value received (e.g. `healthy`). |
| `mode` | string | Extracted mode id, or `unknown`. |
| `stream` | bool | Whether the client requested streaming. |
| `status` | int | HTTP status of the response (200 for aborted streams, whose headers were already sent). |
| `chunks_sent` | int | Content chunks delivered over SSE; `1` for a delivered non-stream JSON body; `0` if nothing was delivered. |
| `outcome` | string | `completed` (full response delivered), `errored` (fault mode returned an error), or `aborted` (client disconnected first). |
| `duration_s` | float | Wall-clock duration of the request. |

The journal is in memory: it survives until the container restarts or
`DELETE /__hits` is called. Recording is guarded by a lock and safe for
concurrent requests (a single uvicorn worker is used).

## Build and run

```bash
docker build -t routing-lab-mock ./mock_upstream
docker run --rm -p 127.0.0.1:18000:8000 routing-lab-mock  # host port for local probing
```

In the dev stack this service is wired in as `mock-upstream` by
`deploy/docker-compose.dev.yml` (no host ports).
