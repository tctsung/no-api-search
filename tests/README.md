# Tests

Run from the repository root.

```bash
uv run python -m unittest discover -s tests                         # offline
NO_API_SEARCH_LIVE=1 uv run python -m unittest discover -s tests    # also calls DuckDuckGo, Jina and markdown.new
```
