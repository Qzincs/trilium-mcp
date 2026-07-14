# trilium-mcp

MCP server for connecting ChatGPT Web to a TriliumNext knowledge base through the Trilium ETAPI.

ChatGPT Web connects to this server over Streamable HTTP, and the server calls Trilium ETAPI on its
behalf. It supports metadata search, content reads, note creation, content edits, renames, and
branch moves. Write operations require explicit confirmation and, where applicable, a current
`blobId` check.

## Requirements

- Python 3.12+
- `uv`
- A running TriliumNext instance with ETAPI enabled
- A Trilium ETAPI token

## Configuration

Copy the example environment file and adjust it for your Trilium instance:

```bash
cp .env.example .env
```

The server loads `.env` from the current working directory. Environment variables take precedence
over values in `.env`.

Required settings:

```env
TRILIUM_ETAPI_URL=https://notes.example.com/etapi
TRILIUM_ETAPI_TOKEN=replace-with-server-side-token
```

Optional settings:

```env
MCP_HOST=127.0.0.1
MCP_PORT=8000
TRILIUM_REQUEST_TIMEOUT=30
TRILIUM_DEFAULT_SEARCH_LIMIT=10
TRILIUM_MAX_SEARCH_LIMIT=50
TRILIUM_MAX_CONTENT_CHARS=100000
```

`TRILIUM_ETAPI_URL` must include the `/etapi` path.

## Run

Install dependencies and start the MCP server:

```bash
uv sync
uv run python -m trilium_mcp.server
```

By default, the server listens on:

```text
http://127.0.0.1:8000/mcp
```

Configure your MCP client to use Streamable HTTP at that URL.

## Docker

Docker Compose is the recommended way to run the server. Create `.env` as described above, then
start it:

```bash
docker compose up -d --build
```

Follow its logs or stop it with:

```bash
docker compose logs -f
docker compose down
```

The container listens on `0.0.0.0`, while Compose publishes its port only on the host loopback
interface: `http://127.0.0.1:8000/mcp`. Expose it through a reverse proxy or tunnel rather than
publishing it directly to the internet.

To build and run without Compose:

```bash
docker build -t trilium-mcp .
docker run --rm --env-file .env --env MCP_HOST=0.0.0.0 \
  --publish 127.0.0.1:8000:8000 trilium-mcp
```

## Tools

Read tools:

- `search_notes` — search notes by title, content, or ETAPI search syntax; returns metadata only.
- `get_note` — fetch note metadata, including parent and child branch IDs.
- `get_note_content` — read raw note content with optional character-based pagination.
- `find_in_note` — find literal text in a note body and return offsets plus context.
- `get_recent_notes` — list recently modified notes.
- `list_note_children` — list direct children of a note.
- `get_note_locations` — list all tree locations for a note, including branch IDs for moves.

Write tools:

- `replace_note_content` — replace a note body.
- `append_note` — append raw text to a note body.
- `create_note` — create a text note under a parent note.
- `edit_note_content` — replace exact text in a note body.
- `rename_note` — rename a note.
- `move_note` — move one specific branch to a new parent.

Write tools require `confirm=true`. Content-changing tools also require the current `blobId` as
`expected_blob_id`; read the note first with `get_note` or `get_note_content`, review the planned
change, then call the write tool with that current ID.

For `text/html` notes, content tools operate on raw HTML, not rendered visible text.

## Development

Run the test suite:

```bash
uv run pytest
```

Run linting:

```bash
uv run ruff check
```

The test suite uses mocked HTTP responses and does not require a live Trilium server.

## Project layout

```text
src/trilium_mcp/
  server.py          Streamable HTTP MCP entry point
  config.py          environment-backed settings
  client.py          async Trilium ETAPI client
  errors.py          safe domain errors
  tools/
    notes.py         note search, read, and write tools
    branches.py      child and location tools
tests/               unit tests with mocked ETAPI calls
```
