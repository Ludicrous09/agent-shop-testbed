# String Utils API

A FastAPI service that exposes the functions in `src/string_utils.py` as HTTP
endpoints.

## Running locally

Install dependencies and run the app with uvicorn:

```bash
pip install -r requirements.txt
uvicorn src.api.app:app
```

By default this serves the API at `http://127.0.0.1:8000`.

## Running in a container

Build and run the image defined by the `Dockerfile`:

```bash
docker build -t string-utils-api .
docker run --rm -p 8000:8000 string-utils-api
```

The container listens on port 8000, so it is available at
`http://127.0.0.1:8000` on the host.

## The authoritative contract

Once the service is running, the full, machine-readable API contract is
available at:

- `GET /docs` — interactive Swagger UI
- `GET /openapi.json` — the raw OpenAPI document

The endpoint list below is a convenience for humans skimming this file. If it
ever disagrees with `GET /openapi.json`, the OpenAPI document is correct and
this document should be updated to match.

## Endpoints

All endpoints below accept and return JSON, and are mounted under the
`/strings` prefix.

### `POST /strings/reverse`

Reverse a string.

Request:

```json
{"s": "hello"}
```

Response:

```json
{"result": "olleh"}
```

### `POST /strings/is-palindrome`

Check whether a string is a palindrome (case-insensitive, ignoring spaces).

Request:

```json
{"s": "A man a plan a canal Panama"}
```

Response:

```json
{"result": true}
```

### `POST /strings/word-count`

Count the words in a string.

Request:

```json
{"s": "The quick brown fox"}
```

Response:

```json
{"result": 4}
```

### `POST /strings/truncate`

Truncate a string to `max_length`, appending `suffix` (default `"..."`) when
truncation occurs.

Request:

```json
{"s": "The quick brown fox", "max_length": 10}
```

Response:

```json
{"result": "The qui..."}
```

### `POST /strings/parse-config`

Parse `key=value` configuration text into a dict, ignoring `#` comments.

Request:

```json
{"text": "# comment\nname=value\nport=8080"}
```

Response:

```json
{"result": {"name": "value", "port": "8080"}}
```

### `POST /strings/slugify`

Convert a string into a lowercase, URL-safe slug.

Request:

```json
{"s": "Hello, World!"}
```

Response:

```json
{"result": "hello-world"}
```

### `POST /strings/title-case`

Capitalise each word, keeping short joining words (e.g. "of", "the") lowercase
unless they are first.

Request:

```json
{"s": "the lord of the rings"}
```

Response:

```json
{"result": "The Lord of the Rings"}
```
