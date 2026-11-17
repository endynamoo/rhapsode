# Searching your library

Rhapsode can search the text of every book you have imported — the "where was
that passage I read last week" question — without leaving the machine. A result
opens the book at the exact sentence, with the matching words marked.

Search is one modal in the reader (`Ctrl+F` / `⌘F`, or the magnifier in the top
bar). Results are grouped by book, each with a snippet, the chapter or page, and
the sentence number.

## What it searches

The text the importer already extracted: PDFs with a text layer, EPUBs, DOCX,
RTF, TXT, Markdown, HTML, and anything OCR turned into text. Rendered page
images are not re-read; a scanned PDF is only searchable to the extent OCR
captured it at import.

The index is built from `murmur.db` into a second database,
`search.db`, beside it. It is **derived data**: deleting it loses nothing but
the time to rebuild, and the next index pass recreates it.

## How it works

| Step | What happens |
| --- | --- |
| Chunking | One speech unit (normally one sentence) is one chunk. A unit longer than 600 characters is split on a word boundary, so a hit is never a paragraph you cannot find. |
| Anchors | Every chunk stores the document id, the unit index (the reader's position), the page the importer recorded, the contents entry it falls in, and character offsets inside the unit. That tuple is what the UI jumps to. |
| Lexical ranking | SQLite FTS5 with the `unicode61` tokenizer, ordered by `bm25()`. No stemming: "walk" and "walking" are different tokens. |
| Meaning ranking | Optional. Sentences are embedded with `all-MiniLM-L6-v2` (ONNX, CPU) and compared by cosine similarity. A meaning-only hit must clear 0.22 **and** be within 60% of the best score, so a small library does not answer every query with every sentence it owns. |
| Fusion | Reciprocal rank fusion: `score = Σ 1 / (60 + rank)` over the lexical and semantic lists. Neither score has to be calibrated against the other. |
| Recency | `score += 0.002 × 0.5^(age / 30 days)`, where age is how long ago you last opened the book or saved a position. 0.002 is about seven places in one list at the top of the ranking: a tie-break, not a veto. |
| Ties | Final order is by score, then document id, unit, chunk ordinal, so the same query always returns the same order. |

### Incremental and resumable

- Each book stores a **signature** (`updated`, character count, word count, page
  count). An index pass compares signatures from `documents` without reading any
  body; unchanged books cost one row each.
- When the signature moves, the body hash decides whether the text really
  changed. A rename or a saved position refreshes the metadata and keeps the
  chunks.
- Rows are written in batches of 200 inside one transaction per batch, and the
  book is marked `partial` until the last batch commits. A killed process
  resumes at the next unit; the `(doc_id, unit, ord)` uniqueness constraint
  makes a duplicated row impossible.
- Only one book is loaded at a time, so peak memory is the largest single
  document, not the library. Vectors for one book are written in batches of 32.
- The worker runs on a daemon thread. Startup never waits for it, and an import
  only queues a document; the search panel, the **Index library** button and
  `POST /api/search/index` are what start a pass.

### Reading recency

`progress` rows are written when a document is opened (`GET /api/docs/{id}`) or
its position is saved (`PATCH /api/docs/{id}`). Importing is not reading.

`recent=7d` therefore means *documents whose reading activity is within the last
seven days*. `24h`, `2w`, `2m` (months of 30 days) and `all` work as well. A
book that was imported and never opened is not "recent".

## The optional model

Lexical search needs nothing beyond the standard library. Meaning search needs
one small model:

| | |
| --- | --- |
| Model | `Xenova/all-MiniLM-L6-v2`, int8 ONNX export, 384 dimensions |
| Licence | Apache-2.0 |
| Size | ~23 MB on disk (22.9 MB model + 0.7 MB tokenizer) |
| Runtime | `onnxruntime` on the CPU, one thread, already installed for Kokoro |
| Location | `<data dir>/models/minilm-l6-v2/` (`%LOCALAPPDATA%\Rhapsode\models\...` on Windows) |
| Download | `POST /api/search/model`, or the **Download** button in the search panel |
| Disable | set `MURMUR_SEMANTIC=off`; the files are then ignored |

The download is lazy, streams to `*.part` and renames when complete, reports
progress through `/api/search/status`, and never runs at startup. Books indexed
before the model arrived are embedded on the next pass (the worker backfills
`embedded < chunks`), so you do not have to rebuild from scratch.

Without the model the search panel says **"Keyword search only — the meaning
model is not downloaded"** and the API reports `"mode": "lexical"`. It does not
silently pretend to search by meaning.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/search?q=…&limit=20&offset=0&recent=7d&doc=<id>` | Ranked hits |
| GET | `/api/search/status` | Coverage, pending books, index progress, model state |
| POST | `/api/search/index` | Start indexing: `{}`, `{"force": true}`, or `{"doc": "<id>"}` → 202 |
| POST | `/api/search/model` | Download the embedding model → 202 |

`q` is required (a blank query is a 400). `limit` is clamped to 1–50. `offset`
pages through the same ranking. `recent` accepts `7d`, `24h`, `2w`, `all` or
nothing; anything else is a 400 that names the format.

```json
{
  "query": "why did the light go out",
  "mode": "hybrid",
  "limit": 20,
  "offset": 0,
  "total": 3,
  "tookMs": 5.2,
  "recent": "",
  "terms": ["why", "did", "light", "go", "out"],
  "semantic": {"available": true, "state": "ready", "model": "all-MiniLM-L6-v2", "error": ""},
  "hits": [
    {
      "chunkId": 128,
      "docId": "59c7a12cc8b4",
      "title": "Mira and the light",
      "unit": 85, "unitCount": 332, "ord": 0,
      "page": 0, "chapter": 2, "chapterTitle": "The fourth bell",
      "score": 0.033,
      "recency": 1.0,
      "lexicalRank": 1,
      "semanticRank": 2,
      "snippet": {"text": "…the light went out…", "highlights": [[6, 11]], "truncatedStart": true, "truncatedEnd": false},
      "location": {"unit": 85, "unitCount": 332, "page": 0, "chapter": 2, "chapterTitle": "The fourth bell"},
      "jump": {"docId": "59c7a12cc8b4", "unit": 85, "page": 0, "chunkId": 128, "terms": ["why", "did", "light", "go", "out"]}
    }
  ]
}
```

`total` counts the fused candidates (at most 200 from each list), not every row
in the index. `snippet.highlights` are character ranges into `snippet.text`;
the page builds `<mark>` elements from text and ranges, never from HTML, so book
text cannot inject markup.

`jump` is the machine-usable target: `docId` + `unit` is exactly
`openDoc(id, {position})` in the reader, and `page` is the page the reader
should show for PDF and EPUB documents.

## Measured on this machine

Windows 11, Python 3.12, RX 9070 XT (the model is CPU-only):

| | |
| --- | --- |
| Book | *Mira and the light*, 6,460 words, 33,544 characters, 332 sentences |
| Lexical index | 1.1 s, `search.db` 188 KB |
| With embeddings | +1.0 s (332 vectors), `search.db` 884 KB |
| Query (lexical) | 2–3 ms |
| Query (hybrid) | 4.5–7 ms warm |
| Model download | 3.1 s on a fast connection |

The vector table is the bulk of the file: 384 float32 = 1,536 bytes per chunk.
SQLite does not shrink the file when rows are deleted; the space is reused by
later inserts.

## Limits and follow-ups

- **No approximate-nearest-neighbour index.** Every query scores every vector,
  streamed in blocks of 4096 to bound memory. That is milliseconds at a few
  thousand chunks and fine for a personal library; a 100k-chunk library would
  want an ANN index.
- **No stemming or fuzzy matching** on the lexical side. Meaning search
  compensates for inflection but not for typos.
- **One worker per process**, no job queue persistence. Interrupting the app
  mid-book leaves it `partial`, and the next pass resumes; there is no
  "cancel index" button.
- **Word-level highlighting on jump is text-view only.** PDF and EPUB documents
  open on the right page, but their own renderers do not mark the matched words.
- **No "search inside this book" control in the UI**; the API accepts `doc=`.
- **Indexed text is the imported text.** A scanned PDF without OCR has nothing
  to find.

## For agents

- The index is derived data. Never migrate it by hand: delete `search.db` and
  let a pass rebuild it.
- `murmur/semantic.py` must stay import-light: `onnxruntime` is imported inside
  `_load()`, and the test suite installs neither the model nor onnxruntime.
- Do not index from the request thread. `search.note_document()` only queues;
  `search.start_background()` is the one place a thread is spawned.
- All paths in `search.py` and `semantic.py` derive from `config.HOME` /
  `config.MODELS_DIR` at call time, because tests move them mid-process.
