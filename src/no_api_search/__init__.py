import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request

from ddgs import DDGS
from ddgs.exceptions import DDGSException

__version__ = "0.1.0"

DUCKDUCKGO = "https://html.duckduckgo.com/html/"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html",
}
READER_HEADERS = {"User-Agent": "no-api-search", "X-Retain-Links": "text"}
MAX_CHARS = 20000
STUBS = ("please enable javascript", "loading...", "just a moment")
LINKS = re.compile(r"!?\[([^\[\]]*)\]\((?:[^()]|\([^()]*\))*\)")
REFERENCES = re.compile(r"\[([^\[\]]*)\]\[\d+\]|^\[\d+\]: .*\n?", re.M)


def _compact(text):
    return " ".join((text or "").split())


def _plain(markup):
    return _compact(html.unescape(re.sub(r"<[^>]+>", "", markup)))


def _duckduckgo(query):
    request = urllib.request.Request(DUCKDUCKGO, data=urllib.parse.urlencode({"q": query}).encode(), headers=BROWSER_HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        page = response.read().decode("utf-8", "replace")
    results = []
    for block in page.split('class="result__a"')[1:]:
        url = html.unescape(re.search(r'href="([^"]*)"', block)[1])
        if "uddg=" in url:
            url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["uddg"][0]
        if "duckduckgo.com/y.js" in url:
            continue
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        results.append({"title": _plain(block.split(">", 1)[1].split("</a>", 1)[0]), "url": url, "snippet": _plain(snippet[1] if snippet else "")})
    if not results:
        raise ValueError("DuckDuckGo returned no parseable results")
    return results


def _ddgs(query, max_results):
    try:
        results = DDGS().text(query, max_results=max_results)
    except DDGSException as e:
        if "no results" in str(e).lower():
            return []
        raise
    return [
        {"title": _compact(r.get("title")), "url": (r.get("href") or "").strip(), "snippet": _compact(r.get("body"))}
        for r in results
    ]


def search(query, max_results=5):
    try:
        return _duckduckgo(query)[:max_results]
    except Exception:
        return _ddgs(query, max_results)


def render(query, results):
    if not results:
        return f"Search query: {query}\nNo results found."
    lines = [f"Search query: {query}", ""]
    for i, r in enumerate(results, 1):
        lines += [f"{i}. {r['title']}", f"Source: {r['url']}"] + ([r["snippet"]] if r["snippet"] else []) + [""]
    return "\n".join(lines).strip()


def _read(reader, url):
    request = urllib.request.Request(reader + url, headers=READER_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def _split(page):
    head, marker, body = page.partition("Markdown Content:")
    return (head, body.strip()) if marker else ("", page)


def fetch(url):
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Only http(s) URLs are supported: {url}")
    attempts = (
        lambda: _read("https://r.jina.ai/", url),
        lambda: _read("https://markdown.new/", url),
        lambda: REFERENCES.sub(r"\1", DDGS().extract(url)["content"]),
    )
    fallback, error = None, None
    for attempt in attempts:
        try:
            page = LINKS.sub(r"\1", attempt()).strip()
        except Exception as e:
            error = e
            continue
        head, body = _split(page)
        if body.startswith("%PDF"):
            continue
        if "Warning:" not in head and len(body) >= 100 and not body.lower().startswith(STUBS):
            return page
        fallback = fallback or page
    if fallback:
        return fallback
    raise error or ValueError(f"No readable content at {url}")


def window(text, offset=0, max_chars=MAX_CHARS):
    chunk = text[offset : offset + max_chars]
    end = offset + len(chunk)
    if end < len(text):
        chunk += f"\n\n[Truncated: characters {offset}-{end} of {len(text)}. Call again with offset={end} to read more.]"
    return chunk


TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web. Returns titles, URLs and snippets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "max_results": {"type": "integer", "description": "Number of results (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": "Read a web page or PDF as clean markdown, including JavaScript-rendered sites. Long pages are truncated; pass the offset from the truncation note to continue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL"},
                "offset": {"type": "integer", "description": "Character offset to start from (default 0)"},
                "max_chars": {"type": "integer", "description": f"Maximum characters to return (default {MAX_CHARS})"},
            },
            "required": ["url"],
        },
    },
]


def call(name, args):
    if name == "web_search":
        return render(args["query"], search(args["query"], int(args.get("max_results", 5))))
    if name == "fetch_url":
        return window(fetch(args["url"]), int(args.get("offset", 0)), int(args.get("max_chars", MAX_CHARS)))
    raise ValueError(f"Unknown tool: {name}")


def handle(message):
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "no-api-search", "version": __version__},
            "instructions": "Free web search (web_search) and web page reader (fetch_url).",
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        try:
            return {"content": [{"type": "text", "text": call(params["name"], params.get("arguments") or {})}], "isError": False}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    raise LookupError(method)


def serve(stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            if "id" not in message:
                continue
            try:
                reply = {"jsonrpc": "2.0", "id": message["id"], "result": handle(message)}
            except LookupError:
                reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": f"Method not found: {message.get('method')}"}}
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="no-api-search", description="Free, keyless web search and page reader.")
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("search", help="search the web")
    s.add_argument("query", nargs="+")
    s.add_argument("--max_results", type=int, default=5)
    f = commands.add_parser("fetch", help="read a URL as markdown")
    f.add_argument("url")
    f.add_argument("--offset", type=int, default=0)
    f.add_argument("--max_chars", type=int, default=MAX_CHARS)
    commands.add_parser("mcp", help="run the MCP server on stdio")
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    if command == "mcp":
        return serve()
    if command == "search":
        args["query"] = " ".join(args["query"])
    try:
        print(call("web_search" if command == "search" else "fetch_url", args))
    except Exception as e:
        sys.exit(f"error: {type(e).__name__}: {e}")
