"""공식 문서 조회(BE-05). 레포 tests/test_documentation.py를 이식했다. 네트워크를 쓰지 않는다."""

from __future__ import annotations

import zlib
from collections.abc import Callable, Iterator
from urllib.error import URLError

import pytest

from server.docs import documentation
from server.docs.package_registry import PACKAGE_DOCS

REQUESTS = PACKAGE_DOCS["requests"]
DOTENV = PACKAGE_DOCS["dotenv"]
PYDANTIC = PACKAGE_DOCS["pydantic"]
STREAMLIT = PACKAGE_DOCS["streamlit"]


def inventory(*entries: tuple[str, str]) -> bytes:
    """Build a Sphinx objects.inv file from (name, uri) pairs."""
    header = (
        b"# Sphinx inventory version 2\n# Project: Test\n# Version: 1\n"
        b"# The remainder of this file is compressed using zlib.\n"
    )
    body = "\n".join(f"{name} py:function 1 {uri} -" for name, uri in entries)
    return header + zlib.compress(body.encode())


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[dict[str, bytes]], list[str]]]:
    """Serve fixed responses by URL and record every requested URL."""
    pages: dict[str, bytes] = {}
    requested: list[str] = []

    def download(url: str, limit: int) -> bytes:
        requested.append(url)
        if url not in pages:
            raise URLError("offline")
        return pages[url][:limit]

    def serve(responses: dict[str, bytes]) -> list[str]:
        pages.update(responses)
        return requested

    monkeypatch.setattr(documentation, "_download", download)
    caches = (
        documentation._fetch_page,
        documentation._fetch_inventory,
        documentation._fetch_llms_index,
    )
    for cache in caches:
        cache.cache_clear()
    yield serve
    for cache in caches:
        cache.cache_clear()


def test_unregistered_package_is_unsupported_without_network_request(web):
    requested = web({})

    evidence = documentation.lookup_documentation("internal_billing", "connect")

    assert evidence.status == "unsupported"
    assert evidence.source_id == "" and evidence.url == ""
    assert requested == []


def test_registered_sphinx_symbol_is_cited_from_package_docs(web):
    web(
        {
            REQUESTS.index_url: inventory(("requests.get", "api/#$")),
            REQUESTS.docs_url + "api/": (
                b'<dl><dt id="requests.get">get(url)</dt><dd><p>Sends a GET request.</p></dd>'
                b'<dt id="requests.post">post(url)</dt><dd><p>Sends a POST request.</p></dd></dl>'
            ),
        }
    )

    evidence = documentation.lookup_documentation("requests", "get")

    assert evidence.status == "exact_match"
    assert evidence.url == REQUESTS.docs_url + "api/#requests.get"
    assert evidence.title == "Requests documentation: requests.get"
    assert evidence.excerpt == "Sends a GET request."


def test_overloaded_signatures_share_one_definition(web):
    web(
        {
            REQUESTS.index_url: inventory(("requests.get", "api/#$")),
            REQUESTS.docs_url + "api/": (
                b'<dl><dt id="requests.get">get(url)</dt><dt>get(url, params)</dt>'
                b"<dd><p>Sends a GET request.</p></dd></dl>"
            ),
        }
    )

    evidence = documentation.lookup_documentation("requests", "get")

    assert evidence.status == "exact_match"
    assert evidence.excerpt == "Sends a GET request."


def test_mkdocstrings_page_is_cited(web):
    web(
        {
            DOTENV.index_url: inventory(("dotenv.load_dotenv", "reference/#$")),
            DOTENV.docs_url + "reference/": (
                b'<div class="doc doc-object"><h2 id="dotenv.load_dotenv" class="doc doc-heading">'
                b'load_dotenv</h2><div class="doc doc-contents"><p>Parse a .env file.</p>'
                b'<div class="admonition">Note.</div></div></div>'
                b'<div class="doc doc-contents"><p>Another object.</p></div>'
            ),
        }
    )

    evidence = documentation.lookup_documentation("dotenv", "load_dotenv")

    assert evidence.status == "exact_match"
    assert evidence.excerpt == "Parse a .env file. Note."


def test_heading_section_page_is_cited(web):
    web(
        {
            PYDANTIC.index_url: inventory(("pydantic.BaseModel", "api/base_model/#$")),
            PYDANTIC.docs_url + "api/base_model/": (
                b'<h2 id="pydantic.BaseModel">BaseModel</h2><svg><text>icon</text></svg>'
                b"<p>A base class for creating Pydantic models.</p>"
                b'<h3 id="pydantic.BaseModel.model_dump">model_dump</h3><p>Dump.</p>'
            ),
        }
    )

    evidence = documentation.lookup_documentation("pydantic", "BaseModel")

    assert evidence.status == "exact_match"
    assert evidence.excerpt == "A base class for creating Pydantic models."


def test_reexported_symbol_resolves_only_when_unambiguous(web):
    web(
        {
            PYDANTIC.index_url: inventory(
                ("pydantic.main.BaseModel", "api/base_model/#$"),
                ("pydantic.fields.Field", "api/fields/#$"),
                ("pydantic.types.Field", "api/types/#$"),
            ),
            PYDANTIC.docs_url + "api/base_model/": (
                b'<h2 id="pydantic.main.BaseModel">BaseModel</h2><p>A base class.</p>'
            ),
        }
    )

    unique = documentation.lookup_documentation("pydantic", "BaseModel")
    ambiguous = documentation.lookup_documentation("pydantic", "Field")

    assert unique.status == "exact_match"
    assert unique.url == PYDANTIC.docs_url + "api/base_model/#pydantic.main.BaseModel"
    assert ambiguous.status == "page_only"
    assert ambiguous.url == PYDANTIC.docs_url


def test_symbol_missing_from_index_is_not_citable(web):
    web({REQUESTS.index_url: inventory(("requests.get", "api/#$"))})

    evidence = documentation.lookup_documentation("requests", "Session.mount")

    assert evidence.status == "page_only"
    assert evidence.url == REQUESTS.docs_url


def test_llms_txt_symbol_is_cited(web):
    web(
        {
            STREAMLIT.index_url: (
                b"# Streamlit\n"
                b"  - [st.title](/develop/api-reference/text/st.title)\n"
                b"    st.title displays text in title formatting.\n"
                b"  - [st.column_config](/develop/api-reference/data/st.column_config)\n"
                b"    Configure columns.\n"
            )
        }
    )

    evidence = documentation.lookup_documentation("streamlit", "title")

    assert evidence.status == "exact_match"
    assert evidence.url == "https://docs.streamlit.io/develop/api-reference/text/st.title"
    assert evidence.excerpt == "st.title displays text in title formatting."


def test_llms_txt_parent_page_is_linked_but_not_cited(web):
    web(
        {
            STREAMLIT.index_url: (
                b"  - [st.column_config](/develop/api-reference/data/st.column_config)\n"
                b"    Configure columns.\n"
            )
        }
    )

    evidence = documentation.lookup_documentation("streamlit", "column_config.TextColumn")

    assert evidence.status == "page_only"
    assert evidence.url == "https://docs.streamlit.io/develop/api-reference/data/st.column_config"


def test_index_download_failure_is_unavailable(web):
    web({})

    evidence = documentation.lookup_documentation("requests", "get")

    assert evidence.status == "unavailable"
    assert evidence.error_type == "URLError"
    assert evidence.url == REQUESTS.docs_url


# --- 표준 라이브러리: Python 문서 색인으로 실제 페이지를 찾는다 --------------

PY_INVENTORY = "https://docs.python.org/3/objects.inv"
ASYNCIO_TASK_PAGE = "https://docs.python.org/3/library/asyncio-task.html"


def test_stdlib_symbol_resolves_its_page_through_python_inventory(web):
    requested = web(
        {
            PY_INVENTORY: inventory(("asyncio.gather", "library/asyncio-task.html#$")),
            ASYNCIO_TASK_PAGE: (
                b'<dl><dt id="asyncio.gather">gather(*aws)</dt>'
                b"<dd><p>Run awaitable objects concurrently.</p></dd></dl>"
            ),
        }
    )

    evidence = documentation.lookup_documentation("asyncio", "gather")

    assert evidence.status == "exact_match"
    assert evidence.url == ASYNCIO_TASK_PAGE + "#asyncio.gather"
    assert evidence.excerpt == "Run awaitable objects concurrently."
    assert requested == [PY_INVENTORY, ASYNCIO_TASK_PAGE]


def test_stdlib_falls_back_to_module_page_when_inventory_is_unavailable(web):
    json_page = "https://docs.python.org/3/library/json.html"
    web({json_page: b'<dl><dt id="json.dumps">dumps(obj)</dt><dd><p>Serialize.</p></dd></dl>'})

    evidence = documentation.lookup_documentation("json", "dumps")

    assert evidence.status == "exact_match"
    assert evidence.url == json_page + "#json.dumps"


def test_stdlib_page_download_failure_is_unavailable(web):
    web({PY_INVENTORY: inventory(("asyncio.gather", "library/asyncio-task.html#$"))})

    evidence = documentation.lookup_documentation("asyncio", "gather")

    assert evidence.status == "unavailable"
    assert evidence.url == ASYNCIO_TASK_PAGE + "#asyncio.gather"


def test_builtins_skip_inventory(web):
    page = "https://docs.python.org/3/library/functions.html"
    requested = web({page: b'<dl><dt id="print">print(*objects)</dt><dd><p>Print.</p></dd></dl>'})

    evidence = documentation.lookup_documentation("builtins", "print")

    assert evidence.status == "exact_match"
    assert requested == [page]
