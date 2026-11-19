import asyncio
import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from ddgs.exceptions import DDGSException

import no_api_search as nas

LIVE = os.environ.get("NO_API_SEARCH_LIVE") == "1"
EXTRACTED = "Locally extracted text. " * 10
ARTICLE = "Title: Example\n\nURL Source: https://example.com\n\nMarkdown Content:\n" + "Real article text. " * 20


class FakeDDGS:
    def text(self, query, max_results=5):
        return [
            {"title": "  Free   Lunch\nAI ", "href": " https://example.com/a ", "body": "A  fallback\n router."},
            {"title": "No snippet", "href": "https://example.com/b", "body": ""},
        ][:max_results]

    def extract(self, url, fmt="text_markdown"):
        return {"url": url, "content": f"  # Extracted {url} as {fmt}\n{EXTRACTED}  "}


def http_response(body):
    response = MagicMock()
    response.read.return_value = body.encode()
    response.__enter__.return_value = response
    return response


DUCKDUCKGO_PAGE = """
<div class="result results_links results_links_deep result--ad">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad_domain=x.com&amp;u3=1">Sponsored</a></h2>
  <a class="result__snippet" href="https://duckduckgo.com/y.js?u3=1">Buy now</a>
</div>
<div class="result results_links results_links_deep web-result ">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="https://realpython.com/async-io-python/">Python&#x27;s asyncio: A Hands-On Walkthrough - Real Python</a>
  </h2>
  <a class="result__url" href="https://realpython.com/async-io-python/">realpython.com/async-io-python/</a>
  <a class="result__snippet" href="https://realpython.com/async-io-python/">Explore how <b>Python</b> <b>asyncio</b>
   works &amp; when to use it.</a>
</div>
<div class="result results_links results_links_deep web-result ">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html&amp;rut=abc">asyncio &lt;stdlib&gt;</a></h2>
</div>
<div class="result results_links results_links_deep web-result ">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://example.com/third">Third</a></h2>
  <a class="result__snippet" href="https://example.com/third">third snippet</a>
</div>
"""


class SearchTest(unittest.TestCase):
    @patch("no_api_search.urllib.request.urlopen", return_value=http_response(DUCKDUCKGO_PAGE))
    def test_duckduckgo_html_is_parsed(self, urlopen):
        self.assertEqual(
            nas.search("python asyncio", max_results=2),
            [
                {
                    "title": "Python's asyncio: A Hands-On Walkthrough - Real Python",
                    "url": "https://realpython.com/async-io-python/",
                    "snippet": "Explore how Python asyncio works & when to use it.",
                },
                {"title": "asyncio <stdlib>", "url": "https://docs.python.org/3/library/asyncio.html", "snippet": ""},
            ],
        )
        request = urlopen.call_args.args[0]
        self.assertEqual((request.full_url, request.data), (nas.DUCKDUCKGO, b"q=python+asyncio"))
        self.assertIn("Mozilla", request.get_header("User-agent"))

    @patch("no_api_search.DDGS", return_value=FakeDDGS())
    def test_falls_back_to_ddgs_when_duckduckgo_blocks(self, _):
        for failure in (
            {"return_value": http_response("<html>anomaly-modal challenge</html>")},
            {"side_effect": urllib.error.HTTPError(nas.DUCKDUCKGO, 403, "Forbidden", {}, None)},
            {"side_effect": TimeoutError()},
            {"return_value": http_response('<a class="result__a">markup changed</a>')},
        ):
            with self.subTest(failure=failure), patch("no_api_search.urllib.request.urlopen", **failure):
                self.assertEqual([r["url"] for r in nas.search("q")], ["https://example.com/a", "https://example.com/b"])

    @patch("no_api_search._duckduckgo", side_effect=ValueError("blocked"))
    @patch("no_api_search.DDGS", return_value=FakeDDGS())
    def test_ddgs_results_are_compacted(self, *_):
        self.assertEqual(
            nas.search("q", max_results=2),
            [
                {"title": "Free Lunch AI", "url": "https://example.com/a", "snippet": "A fallback router."},
                {"title": "No snippet", "url": "https://example.com/b", "snippet": ""},
            ],
        )

    @patch("no_api_search._duckduckgo", side_effect=ValueError("blocked"))
    @patch("no_api_search.DDGS")
    def test_no_results_is_empty_not_error(self, ddgs, _):
        ddgs.return_value.text.side_effect = DDGSException("No results found.")
        self.assertEqual(nas.search("zzqx"), [])
        self.assertEqual(nas.render("zzqx", []), "Search query: zzqx\nNo results found.")

    @patch("no_api_search._duckduckgo", side_effect=ValueError("blocked"))
    @patch("no_api_search.DDGS")
    def test_other_search_errors_propagate(self, ddgs, _):
        ddgs.return_value.text.side_effect = DDGSException("Ratelimit")
        with self.assertRaises(DDGSException):
            nas.search("q")

    def test_render_numbers_results_and_skips_empty_snippets(self):
        results = [
            {"title": "A", "url": "https://a", "snippet": "about a"},
            {"title": "B", "url": "https://b", "snippet": ""},
        ]
        self.assertEqual(nas.render("q", results), "Search query: q\n\n1. A\nSource: https://a\nabout a\n\n2. B\nSource: https://b")


def readers(jina, markdown_new):
    def read(reader, url):
        outcome = jina if reader == "https://r.jina.ai/" else markdown_new
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return read


PDF_BINARY = "Title: Converted Content\n\nMarkdown Content:\n%PDF-1.5 %\xe2\xe3 137 0 obj" + "x" * 500
WARNED = "Title: Not Found\n\nWarning: Target URL returned error 404: Not Found\n\nMarkdown Content:\n" + "Site chrome. " * 20
UNREADABLE = {
    "warning": WARNED,
    "javascript stub": "Title: x\n\nMarkdown Content:\nPlease enable JavaScript to continue." + " " * 200 + "x",
    "loading stub": "Title: x\n\nMarkdown Content:\nLoading..." + "." * 200,
    "tracker pixel": "Title: https://match.adsrvr.org/track\n\nMarkdown Content:\nA 1x1 image, likely be a tacker probe",
    "cloudflare challenge": 'Title: Just a moment...\n\nMarkdown Content:\n---\ntitle: "Just a moment..."\n---',
    "pdf binary": PDF_BINARY,
}
EXTRACTED_PAGE = f"# Extracted https://example.com as text_markdown\n{EXTRACTED}".strip()


class FetchTest(unittest.TestCase):
    @patch("no_api_search.urllib.request.urlopen", return_value=http_response(ARTICLE))
    def test_jina_request_keeps_link_text_only(self, urlopen):
        self.assertEqual(nas.fetch("https://example.com"), ARTICLE.strip())
        request = urlopen.call_args.args[0]
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(request.full_url, "https://r.jina.ai/https://example.com")
        self.assertEqual(request.get_header("X-retain-links"), "text")

    @patch("no_api_search.DDGS", return_value=FakeDDGS())
    def test_unreadable_jina_pages_fall_through_to_markdown_new(self, _):
        for name, page in UNREADABLE.items():
            with self.subTest(name), patch("no_api_search._read", side_effect=readers(page, "Markdown Content:\n" + EXTRACTED)):
                self.assertEqual(nas.fetch("https://example.com"), "Markdown Content:\n" + EXTRACTED.strip())

    @patch("no_api_search.DDGS", return_value=FakeDDGS())
    def test_fallback_links_keep_text_only(self, _):
        page = "Markdown Content:\n" + EXTRACTED + "\n[Flat in Shinjuku](/rooms/1?a=(b)&c=d) ![Photo 1](https://i/1.jpg) [](/x) array[0] (index)"
        with patch("no_api_search._read", side_effect=readers(WARNED, page)):
            self.assertTrue(nas.fetch("https://example.com").endswith("\nFlat in Shinjuku Photo 1  array[0] (index)"))

    @patch("no_api_search.DDGS", return_value=FakeDDGS())
    def test_jina_errors_fall_through(self, _):
        for error in (
            urllib.error.HTTPError("https://r.jina.ai/", 429, "Too Many Requests", {}, None),
            urllib.error.HTTPError("https://r.jina.ai/", 422, "Unprocessable", {}, None),
            urllib.error.URLError("down"),
            TimeoutError(),
        ):
            with self.subTest(type(error).__name__), patch("no_api_search._read", side_effect=readers(error, ARTICLE)):
                self.assertEqual(nas.fetch("https://example.com"), ARTICLE.strip())

    @patch("no_api_search.DDGS", return_value=FakeDDGS())
    def test_ddgs_extract_is_last_resort(self, _):
        with patch("no_api_search._read", side_effect=readers(WARNED, urllib.error.URLError("down"))):
            self.assertEqual(nas.fetch("https://example.com"), EXTRACTED_PAGE)

    @patch("no_api_search.DDGS")
    def test_ddgs_reference_links_keep_text_only(self, ddgs):
        ddgs.return_value.extract.return_value = {"content": EXTRACTED + "\n## Basic use[¶][2]\n* [World][3] [][4] m[0] (x)\n\n[2]: https://a/#use\n[3]: https://a/world\n[4]: https://a/\n"}
        with patch("no_api_search._read", side_effect=urllib.error.URLError("down")):
            self.assertTrue(nas.fetch("https://example.com").endswith("\n## Basic use¶\n* World  m[0] (x)"))

    @patch("no_api_search.DDGS")
    def test_unreadable_page_returned_when_nothing_better(self, ddgs):
        ddgs.return_value.extract.side_effect = DDGSException("HTTP 403")
        with patch("no_api_search._read", side_effect=readers(WARNED, PDF_BINARY)):
            self.assertEqual(nas.fetch("https://example.com"), WARNED.strip())

    @patch("no_api_search.DDGS")
    def test_pdf_binary_is_never_returned(self, ddgs):
        ddgs.return_value.extract.return_value = {"content": "%PDF-1.5 binary" + "x" * 500}
        with patch("no_api_search._read", side_effect=readers(PDF_BINARY, PDF_BINARY)), self.assertRaises(ValueError):
            nas.fetch("https://example.com/paper.pdf")

    @patch("no_api_search.DDGS")
    def test_last_error_raised_when_all_fail(self, ddgs):
        ddgs.return_value.extract.side_effect = DDGSException("HTTP 404")
        with patch("no_api_search._read", side_effect=urllib.error.URLError("down")):
            with self.assertRaisesRegex(DDGSException, "HTTP 404"):
                nas.fetch("https://example.com")

    @patch("no_api_search._read")
    def test_non_http_urls_rejected_before_network(self, read):
        for url in ("file:///etc/passwd", "ftp://example.com", "example.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                nas.fetch(url)
        read.assert_not_called()


class WindowTest(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(nas.window("abc", 0, 10), "abc")

    def test_truncation_note_and_offsets_rebuild_text(self):
        text = "".join(chr(97 + i % 26) for i in range(95))
        parts, offset = [], 0
        while True:
            chunk = nas.window(text, offset, 30)
            body, _, note = chunk.partition("\n\n[Truncated: ")
            parts.append(body)
            if not note:
                break
            self.assertIn(f"of {len(text)}", note)
            offset = int(note.split("offset=")[1].split(" ")[0])
        self.assertEqual("".join(parts), text)
        self.assertEqual(len(parts), 4)


def rpc(*messages):
    stdin = io.StringIO("".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages))
    stdout = io.StringIO()
    nas.serve(stdin, stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


class McpProtocolTest(unittest.TestCase):
    def test_handshake_and_listing(self):
        init, listing = rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            "",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        self.assertEqual(init["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(init["result"]["capabilities"], {"tools": {}})
        self.assertEqual([t["name"] for t in listing["result"]["tools"]], ["web_search", "fetch_url"])

    @patch("no_api_search.search", return_value=[{"title": "T", "url": "https://t", "snippet": "s"}])
    @patch("no_api_search.fetch", return_value="x" * 25)
    def test_tool_calls(self, _, search):
        found, page = rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "web_search", "arguments": {"query": "q", "max_results": "3"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "fetch_url", "arguments": {"url": "https://t", "max_chars": 10}}},
        )
        search.assert_called_once_with("q", 3)
        self.assertFalse(found["result"]["isError"])
        self.assertIn("1. T\nSource: https://t\ns", found["result"]["content"][0]["text"])
        self.assertTrue(page["result"]["content"][0]["text"].startswith("x" * 10 + "\n\n[Truncated: characters 0-10 of 25."))

    @patch("no_api_search.fetch", side_effect=ValueError("boom"))
    def test_tool_errors_are_results_not_crashes(self, _):
        failed, unknown, missing, ping = rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "fetch_url", "arguments": {"url": "https://t"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "web_search", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        )
        self.assertEqual(failed["result"], {"content": [{"type": "text", "text": "ValueError: boom"}], "isError": True})
        self.assertTrue(unknown["result"]["isError"])
        self.assertTrue(missing["result"]["isError"])
        self.assertEqual(ping["result"], {})

    def test_bad_input_gets_jsonrpc_errors(self):
        parse, method = rpc("{not json", {"jsonrpc": "2.0", "id": 7, "method": "resources/list"})
        self.assertEqual(parse["error"]["code"], -32700)
        self.assertEqual((method["id"], method["error"]["code"]), (7, -32601))


STUBBED_SERVER = """
import no_api_search as nas
nas.search = lambda query, max_results=5: [{"title": "Stub " + query, "url": "https://stub", "snippet": str(max_results)}]
nas.fetch = lambda url: "y" * 30000
nas.serve()
"""


async def official_client(code, *calls):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(command=sys.executable, args=["-c", code])
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        results = [await session.call_tool(name, args) for name, args in calls]
    return tools, results


class OfficialMcpClientTest(unittest.TestCase):
    def test_official_sdk_client_can_use_server(self):
        tools, (found, page) = asyncio.run(
            official_client(STUBBED_SERVER, ("web_search", {"query": "pi", "max_results": 2}), ("fetch_url", {"url": "https://stub"}))
        )
        self.assertEqual({t.name for t in tools.tools}, {"web_search", "fetch_url"})
        self.assertIn("1. Stub pi\nSource: https://stub\n2", found.content[0].text)
        self.assertEqual(len(page.content[0].text.split("\n\n[Truncated:")[0]), nas.MAX_CHARS)


class CliTest(unittest.TestCase):
    @patch("no_api_search.search", return_value=[])
    def test_search_joins_words(self, search):
        out = io.StringIO()
        with redirect_stdout(out):
            nas.main(["search", "free", "web", "--max_results", "2"])
        search.assert_called_once_with("free web", 2)
        self.assertEqual(out.getvalue(), "Search query: free web\nNo results found.\n")

    @patch("no_api_search.fetch", return_value="z" * 50)
    def test_fetch_window_flags(self, _):
        out = io.StringIO()
        with redirect_stdout(out):
            nas.main(["fetch", "https://t", "--offset", "40", "--max_chars", "5"])
        self.assertTrue(out.getvalue().startswith("zzzzz\n\n[Truncated: characters 40-45 of 50. Call again with offset=45"))

    def test_errors_exit_nonzero_with_message(self):
        with self.assertRaises(SystemExit) as exit:
            nas.main(["fetch", "file:///etc/passwd"])
        self.assertEqual(exit.exception.code, "error: ValueError: Only http(s) URLs are supported: file:///etc/passwd")


@unittest.skipUnless(LIVE, "set NO_API_SEARCH_LIVE=1 to hit the network")
class LiveTest(unittest.TestCase):
    def test_search(self):
        results = nas.search("python asyncio documentation", max_results=5)
        self.assertGreaterEqual(len(results), 3)
        self.assertTrue(all(r["url"].startswith("http") and r["title"] for r in results))

    def test_duckduckgo_markup_still_parses(self):
        try:
            results = nas._duckduckgo("site:python.org asyncio")
        except ValueError:
            self.skipTest("DuckDuckGo is challenging this IP")
        self.assertGreaterEqual(len(results), 3)
        self.assertTrue(any("python.org" in r["url"] for r in results))
        self.assertTrue(any(r["snippet"] for r in results))

    def test_fetch_static_page(self):
        self.assertIn("documentation examples", nas.fetch("https://example.com"))

    def test_fetch_js_rendered_page(self):
        self.assertIn("Tokyo", nas.fetch("https://www.airbnb.com/s/Tokyo/homes"))

    def test_fetch_pdf(self):
        self.assertIn("Attention Is All You Need", nas.window(nas.fetch("https://arxiv.org/pdf/1706.03762")))

    def test_fetch_bot_walled_page(self):
        self.assertIn("Slack", nas.fetch("https://www.g2.com/products/slack/reviews"))

    def test_markdown_new_path(self):
        with patch("no_api_search._read", side_effect=lambda reader, url: nas._read("https://markdown.new/", url)):
            self.assertIn("asyncio", nas.fetch("https://docs.python.org/3/library/asyncio.html"))

    def test_ddgs_fallback_path(self):
        with patch("no_api_search._read", side_effect=urllib.error.URLError("forced")):
            self.assertIn("documentation examples", nas.fetch("https://example.com"))

    def test_unreachable_url_is_an_error(self):
        with self.assertRaises(Exception):
            nas.fetch("https://no-such-host.invalid/")

    def test_official_client_against_real_network(self):
        code = "import no_api_search; no_api_search.serve()"
        _, (found,) = asyncio.run(official_client(code, ("web_search", {"query": "model context protocol", "max_results": 3})))
        self.assertFalse(found.is_error)
        self.assertIn("Source: http", found.content[0].text)


if __name__ == "__main__":
    unittest.main()
