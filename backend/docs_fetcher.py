"""
docs_fetcher.py
External documentation fetcher for DevMate v2.

Public functions:
  detect_libraries(dependencies)                     → list[{name, version, docs_url}]
  fetch_docs_page(url)                               → cleaned text
  search_docs(library, version, query, api_key)      → relevant_section str
"""

import re
import logging
import asyncio
from typing import Optional

import httpx

log = logging.getLogger("devmate.docs_fetcher")

# Request config
FETCH_TIMEOUT   = 15  # seconds
MAX_PAGE_CHARS  = 50_000
MAX_RESULT_CHARS = 4_000

# ─── Library → docs URL registry ────────────────────────────────────────────
# Maps (library_name_lower, major_version_str) → docs URL
# Empty major_version_str = default/latest
_REGISTRY: dict[tuple[str, str], str] = {
    # ── Python web ─────────────────────────────────────────────────────────
    ("fastapi",      ""):   "https://fastapi.tiangolo.com/",
    ("fastapi",      "0"):  "https://fastapi.tiangolo.com/",
    ("starlette",    ""):   "https://www.starlette.io/",
    ("flask",        ""):   "https://flask.palletsprojects.com/en/stable/",
    ("flask",        "3"):  "https://flask.palletsprojects.com/en/3.0.x/",
    ("flask",        "2"):  "https://flask.palletsprojects.com/en/2.3.x/",
    ("flask",        "1"):  "https://flask.palletsprojects.com/en/1.1.x/",
    ("django",       ""):   "https://docs.djangoproject.com/en/stable/",
    ("django",       "5"):  "https://docs.djangoproject.com/en/5.0/",
    ("django",       "4"):  "https://docs.djangoproject.com/en/4.2/",
    ("django",       "3"):  "https://docs.djangoproject.com/en/3.2/",
    # ── Python HTTP ────────────────────────────────────────────────────────
    ("httpx",        ""):   "https://www.python-httpx.org/",
    ("aiohttp",      ""):   "https://docs.aiohttp.org/en/stable/",
    ("requests",     ""):   "https://docs.python-requests.org/en/latest/",
    # ── Python data / ORM ──────────────────────────────────────────────────
    ("sqlalchemy",   ""):   "https://docs.sqlalchemy.org/en/20/",
    ("sqlalchemy",   "2"):  "https://docs.sqlalchemy.org/en/20/",
    ("sqlalchemy",   "1"):  "https://docs.sqlalchemy.org/en/14/",
    ("alembic",      ""):   "https://alembic.sqlalchemy.org/en/latest/",
    ("tortoise-orm", ""):   "https://tortoise.github.io/",
    ("peewee",       ""):   "http://docs.peewee-orm.com/en/latest/",
    # ── Python validation / serialization ──────────────────────────────────
    ("pydantic",     ""):   "https://docs.pydantic.dev/latest/",
    ("pydantic",     "2"):  "https://docs.pydantic.dev/latest/",
    ("pydantic",     "1"):  "https://docs.pydantic.dev/1.10/",
    ("marshmallow",  ""):   "https://marshmallow.readthedocs.io/en/stable/",
    ("attrs",        ""):   "https://www.attrs.org/en/stable/",
    # ── Python task queue / cache ──────────────────────────────────────────
    ("celery",       ""):   "https://docs.celeryq.dev/en/stable/",
    ("redis",        ""):   "https://redis-py.readthedocs.io/en/stable/",
    ("dramatiq",     ""):   "https://dramatiq.io/",
    ("rq",           ""):   "https://python-rq.org/docs/",
    # ── Python auth ────────────────────────────────────────────────────────
    ("authlib",      ""):   "https://docs.authlib.org/en/latest/",
    ("passlib",      ""):   "https://passlib.readthedocs.io/en/stable/",
    ("python-jose",  ""):   "https://python-jose.readthedocs.io/en/latest/",
    # ── Python cloud ───────────────────────────────────────────────────────
    ("boto3",        ""):   "https://boto3.amazonaws.com/v1/documentation/api/latest/index.html",
    ("openai",       ""):   "https://platform.openai.com/docs/",
    ("anthropic",    ""):   "https://docs.anthropic.com/",
    # ── Python testing ─────────────────────────────────────────────────────
    ("pytest",       ""):   "https://docs.pytest.org/en/stable/",
    ("hypothesis",   ""):   "https://hypothesis.readthedocs.io/en/latest/",
    # ── JS/TS frameworks ───────────────────────────────────────────────────
    ("react",        ""):   "https://react.dev/",
    ("react",        "18"): "https://react.dev/",
    ("react",        "17"): "https://legacy.reactjs.org/docs/getting-started.html",
    ("react-dom",    ""):   "https://react.dev/reference/react-dom",
    ("vue",          ""):   "https://vuejs.org/guide/introduction",
    ("vue",          "3"):  "https://vuejs.org/guide/introduction",
    ("vue",          "2"):  "https://v2.vuejs.org/v2/guide/",
    ("next",         ""):   "https://nextjs.org/docs",
    ("next",         "14"): "https://nextjs.org/docs",
    ("next",         "13"): "https://nextjs.org/docs/getting-started",
    ("nuxt",         ""):   "https://nuxt.com/docs",
    ("svelte",       ""):   "https://svelte.dev/docs",
    ("angular",      ""):   "https://angular.io/docs",
    # ── JS/TS runtimes / meta-frameworks ───────────────────────────────────
    ("vite",         ""):   "https://vitejs.dev/guide/",
    ("esbuild",      ""):   "https://esbuild.github.io/",
    ("webpack",      ""):   "https://webpack.js.org/concepts/",
    ("remix",        ""):   "https://remix.run/docs/en/main",
    ("solid-js",     ""):   "https://www.solidjs.com/docs/latest",
    # ── JS/TS server ───────────────────────────────────────────────────────
    ("express",      ""):   "https://expressjs.com/en/4x/api.html",
    ("express",      "4"):  "https://expressjs.com/en/4x/api.html",
    ("express",      "5"):  "https://expressjs.com/en/5x/api.html",
    ("fastify",      ""):   "https://fastify.dev/docs/latest/",
    ("koa",          ""):   "https://koajs.com/",
    ("hapi",         ""):   "https://hapi.dev/api",
    ("nestjs",       ""):   "https://docs.nestjs.com/",
    ("trpc",         ""):   "https://trpc.io/docs",
    # ── JS/TS data ─────────────────────────────────────────────────────────
    ("prisma",       ""):   "https://www.prisma.io/docs",
    ("drizzle-orm",  ""):   "https://orm.drizzle.team/docs/overview",
    ("typeorm",      ""):   "https://typeorm.io/",
    ("mongoose",     ""):   "https://mongoosejs.com/docs/",
    ("sequelize",    ""):   "https://sequelize.org/docs/v6/",
    # ── JS/TS HTTP / state ─────────────────────────────────────────────────
    ("axios",        ""):   "https://axios-http.com/docs/intro",
    ("swr",          ""):   "https://swr.vercel.app/docs/getting-started",
    ("react-query",  ""):   "https://tanstack.com/query/latest/docs/framework/react/overview",
    ("zustand",      ""):   "https://zustand.docs.pmnd.rs/getting-started/introduction",
    ("redux",        ""):   "https://redux.js.org/introduction/getting-started",
    ("redux-toolkit", ""): "https://redux-toolkit.js.org/introduction/getting-started",
    # ── JS/TS utility ──────────────────────────────────────────────────────
    ("lodash",       ""):   "https://lodash.com/docs/",
    ("ramda",        ""):   "https://ramdajs.com/docs/",
    ("date-fns",     ""):   "https://date-fns.org/docs/Getting-Started",
    ("dayjs",        ""):   "https://day.js.org/docs/en/installation/installation",
    ("zod",          ""):   "https://zod.dev/?id=introduction",
    ("yup",          ""):   "https://github.com/jquense/yup",
    ("typescript",   ""):   "https://www.typescriptlang.org/docs/",
    # ── Go ────────────────────────────────────────────────────────────────
    ("github.com/gin-gonic/gin",     ""): "https://gin-gonic.com/docs/",
    ("github.com/labstack/echo",     ""): "https://echo.labstack.com/docs",
    ("github.com/gorilla/mux",       ""): "https://pkg.go.dev/github.com/gorilla/mux",
    ("github.com/go-chi/chi",        ""): "https://go-chi.io/#/README",
    ("gorm.io/gorm",                 ""): "https://gorm.io/docs/",
    ("github.com/jmoiron/sqlx",      ""): "https://jmoiron.github.io/sqlx/",
    ("github.com/golang-jwt/jwt",    ""): "https://pkg.go.dev/github.com/golang-jwt/jwt/v5",
    ("github.com/spf13/viper",       ""): "https://github.com/spf13/viper#readme",
    ("github.com/uber-go/zap",       ""): "https://pkg.go.dev/go.uber.org/zap",
    # ── Rust ──────────────────────────────────────────────────────────────
    ("axum",         ""):   "https://docs.rs/axum/latest/axum/",
    ("actix-web",    ""):   "https://actix.rs/docs",
    ("tokio",        ""):   "https://tokio.rs/tokio/tutorial",
    ("serde",        ""):   "https://serde.rs/",
    ("sqlx",         ""):   "https://docs.rs/sqlx/latest/sqlx/",
    ("reqwest",      ""):   "https://docs.rs/reqwest/latest/reqwest/",
    ("tower",        ""):   "https://docs.rs/tower/latest/tower/",
    ("tracing",      ""):   "https://docs.rs/tracing/latest/tracing/",
}


# ─── detect_libraries ─────────────────────────────────────────────────────────

def detect_libraries(dependencies: dict) -> list[dict]:
    """
    Takes the extracted dependencies dict and returns a list of
    {name, version, docs_url, ecosystem} for every library that
    has a known docs URL.
    """
    found = []
    for ecosystem, pkgs in dependencies.items():
        for lib, version in pkgs.items():
            url = _resolve_docs_url(lib, version)
            found.append({
                "name": lib,
                "version": version,
                "docs_url": url,
                "ecosystem": ecosystem,
            })
    # Sort: known docs URLs first, then alphabetical
    found.sort(key=lambda x: (x["docs_url"].startswith("https://pypi"), x["name"]))
    return found


def _resolve_docs_url(library: str, version: str) -> str:
    """Resolves the best-matching docs URL for a library + version."""
    lib_lower = library.lower().strip()
    ver_clean = re.sub(r"[^0-9.]", "", version)

    # Extract major version number
    major = ""
    m = re.match(r"^(\d+)", ver_clean)
    if m:
        major = m.group(1)

    # Exact match with major version
    if (lib_lower, major) in _REGISTRY:
        return _REGISTRY[(lib_lower, major)]

    # Default (no version qualifier)
    if (lib_lower, "") in _REGISTRY:
        return _REGISTRY[(lib_lower, "")]

    # Partial name match (e.g. "gin" matches "github.com/gin-gonic/gin")
    for (reg_lib, _), url in _REGISTRY.items():
        if lib_lower in reg_lib or reg_lib.endswith(f"/{lib_lower}"):
            return url

    # PyPI fallback for Python packages
    return f"https://pypi.org/project/{lib_lower}/"


# ─── fetch_docs_page ──────────────────────────────────────────────────────────

async def fetch_docs_page(url: str) -> str:
    """
    Fetches a documentation page and returns cleaned text.
    Strips navigation, ads, scripts, and boilerplate.
    Returns empty string on failure.
    """
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": "DevMate/2.0 (+https://github.com/devmate) docs-reader",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            r = await client.get(url)

        if r.status_code != 200:
            log.warning("Docs fetch: HTTP %d for %s", r.status_code, url)
            return ""

        return _clean_html(r.text, url)

    except Exception as exc:
        log.warning("Docs fetch failed for %s: %s", url, exc)
        return ""


def _clean_html(html: str, source_url: str) -> str:
    """Parses HTML and returns cleaned readable text."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("beautifulsoup4 not available — returning raw text snippet")
        return html[:MAX_PAGE_CHARS]

    soup = BeautifulSoup(html, "lxml")

    # Remove non-content elements
    _remove_tags = [
        "script", "style", "noscript", "svg", "img",
        "nav", "header", "footer", "aside",
        # Common class patterns for chrome/navigation
    ]
    for tag_name in _remove_tags:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove elements by class/id patterns that indicate navigation chrome
    nav_patterns = re.compile(
        r"\b(nav|navbar|sidebar|menu|breadcrumb|toc|table-of-contents|"
        r"header|footer|advertisement|cookie|banner|overlay)\b",
        re.IGNORECASE,
    )
    for tag in soup.find_all(True):
        class_str = " ".join(tag.get("class", []))
        id_str = tag.get("id", "")
        if nav_patterns.search(class_str) or nav_patterns.search(id_str):
            tag.decompose()

    # Try to isolate the main docs content
    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"^(content|main|docs|readme|article)", re.I))
        or soup.find(class_=re.compile(
            r"\b(content|markdown|prose|docs-content|article|main-content)\b", re.I
        ))
        or soup.body
    )

    if main_content is None:
        return ""

    # Extract text preserving code blocks
    lines = []
    for element in main_content.descendants:
        if not hasattr(element, "name"):
            # NavigableString — plain text
            text = str(element).strip()
            if text:
                lines.append(text)
        elif element.name in ("pre", "code"):
            code_text = element.get_text()
            if code_text.strip():
                lines.append(f"\n```\n{code_text.strip()}\n```\n")

    # Merge and clean up whitespace
    full_text = " ".join(lines)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)
    full_text = re.sub(r" {2,}", " ", full_text)

    return full_text[:MAX_PAGE_CHARS]


# ─── search_docs ──────────────────────────────────────────────────────────────

async def search_docs(
    library: str,
    version: str,
    query: str,
    api_key: str,
) -> str:
    """
    High-level function:
      1. Resolves docs URL for library + version
      2. Fetches and cleans the page
      3. Uses DO Gradient Inference to extract the relevant section

    Returns the relevant docs text as a string.
    """
    from gradient_inference import run_inference

    docs_url = _resolve_docs_url(library, version)
    log.info("search_docs: %s %s — fetching %s", library, version, docs_url)

    page_text = await fetch_docs_page(docs_url)

    if not page_text.strip():
        log.warning("Empty docs page for %s %s — using inference only", library, version)
        return await run_inference(
            api_key=api_key,
            prompt=f"For {library} {version}: {query}",
            system=(
                f"You are a documentation expert for {library} version {version}. "
                "Answer precisely based on that version's API. "
                "Include code examples. Do not suggest deprecated or newer APIs."
            ),
        )

    # Extract the most relevant section using inference
    system = (
        f"You are a documentation expert. "
        f"Given raw documentation for {library} {version}, "
        "extract ONLY the section that answers the user's question. "
        "Preserve any code examples exactly. "
        "Be concise. Output only the relevant section, no preamble."
    )

    prompt = (
        f"Library: {library} {version}\n"
        f"Docs URL: {docs_url}\n"
        f"Question: {query}\n\n"
        f"RAW DOCUMENTATION (first {MAX_PAGE_CHARS} chars):\n"
        f"{page_text[:MAX_PAGE_CHARS]}\n\n"
        "Extract the section that answers the question above."
    )

    result = await run_inference(
        api_key=api_key,
        prompt=prompt,
        system=system,
    )

    return result[:MAX_RESULT_CHARS] if result else f"No relevant documentation found for: {query}"


# ─── Multi-library docs fetch ─────────────────────────────────────────────────

async def fetch_all_docs_for_session(
    dependencies: dict,
    queries: list[str],
    api_key: str,
    max_concurrent: int = 3,
) -> dict[str, str]:
    """
    Fetches relevant docs for multiple libraries concurrently.
    Used to pre-warm the agent with version-specific docs context.

    Returns: {library_name: relevant_docs_text}
    """
    libraries = detect_libraries(dependencies)
    if not libraries or not queries:
        return {}

    semaphore = asyncio.Semaphore(max_concurrent)
    results: dict[str, str] = {}

    async def _fetch_one(lib: dict, query: str):
        async with semaphore:
            try:
                text = await search_docs(
                    library=lib["name"],
                    version=lib["version"],
                    query=query,
                    api_key=api_key,
                )
                return lib["name"], text
            except Exception as exc:
                log.warning("Failed fetching docs for %s: %s", lib["name"], exc)
                return lib["name"], ""

    # Use the first query as the primary search intent
    primary_query = queries[0] if queries else "getting started"
    tasks = [_fetch_one(lib, primary_query) for lib in libraries[:10]]
    fetched = await asyncio.gather(*tasks)

    for name, text in fetched:
        if text:
            results[name] = text

    return results
