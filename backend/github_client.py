"""
github_client.py
GitHub REST API client for DevMate v2.

Public functions:
  parse_github_url(url)               → (owner, repo)
  fetch_repo_files(owner, repo, token) → list[{path, content, size}]
  get_repo_metadata(owner, repo, token) → {language, stars, description}
  extract_dependencies(files)          → {python, node, go, rust}
  extract_code_patterns(files)         → {framework, folder_structure, naming_conventions, import_style}
"""

import re
import json
import base64
import asyncio
import logging
from typing import Optional

import httpx

log = logging.getLogger("devmate.github")

GITHUB_API = "https://api.github.com"

# Files to skip — binary, lock files, generated output, etc.
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".whl", ".egg",
    ".pyc", ".pyo", ".pyd",
    ".so", ".dylib", ".dll", ".exe", ".bin",
    ".min.js", ".min.css",
    ".map",
    ".lock",          # package-lock.json, yarn.lock, Cargo.lock, poetry.lock
    ".sum",           # go.sum
}

SKIP_DIRS = {
    ".git", ".github", "node_modules", "__pycache__", ".pytest_cache",
    "dist", "build", ".next", ".nuxt", "coverage", ".nyc_output",
    "venv", ".venv", "env", ".env", "vendor",
    ".idea", ".vscode",
}

# Maximum file size to index (100 KB)
MAX_FILE_BYTES = 100 * 1024

# Concurrency limit for file fetching
FETCH_CONCURRENCY = 10


# ─── URL parsing ─────────────────────────────────────────────────────────────

def parse_github_url(url: str) -> tuple[str, str]:
    """
    Extracts (owner, repo) from a GitHub URL.
    Accepts:
      https://github.com/owner/repo
      https://github.com/owner/repo.git
      https://github.com/owner/repo/tree/main/...
      git@github.com:owner/repo.git
    """
    url = url.strip().rstrip("/")

    # SSH format
    ssh = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh:
        return ssh.group(1), ssh.group(2)

    # HTTPS format
    https = re.match(r"https?://github\.com/([^/]+)/([^/\s]+?)(?:\.git|/.*)?$", url)
    if https:
        return https.group(1), https.group(2)

    raise ValueError(
        f"Cannot parse GitHub URL: {url!r}. "
        "Expected format: https://github.com/owner/repo"
    )


# ─── Repo metadata ───────────────────────────────────────────────────────────

async def get_repo_metadata(
    owner: str,
    repo: str,
    token: Optional[str] = None,
) -> dict:
    """Returns {language, stars, description, default_branch, topics}."""
    headers = _auth_headers(token)
    async with httpx.AsyncClient(headers=headers, timeout=20) as client:
        r = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}")
        _raise_for_status(r, f"repo metadata for {owner}/{repo}")
        data = r.json()
        return {
            "language": data.get("language"),
            "stars": data.get("stargazers_count", 0),
            "description": data.get("description", ""),
            "default_branch": data.get("default_branch", "main"),
            "topics": data.get("topics", []),
            "size_kb": data.get("size", 0),
        }


# ─── File tree + content fetching ────────────────────────────────────────────

async def fetch_repo_files(
    owner: str,
    repo: str,
    token: Optional[str] = None,
) -> list[dict]:
    """
    Fetches all indexable source files from a GitHub repo.
    Returns: [{path, content, size, language}]
    
    Strategy:
      1. GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1
         → full file tree in one request
      2. Filter out binary/skip paths
      3. Fetch file contents concurrently (FETCH_CONCURRENCY at a time)
    """
    headers = _auth_headers(token)

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        # Step 1: get full tree
        tree = await _fetch_tree(client, owner, repo)
        if not tree:
            raise RuntimeError(f"Could not fetch file tree for {owner}/{repo}")

        # Step 2: filter to indexable blobs
        blobs = [
            item for item in tree
            if item.get("type") == "blob"
            and _should_index(item.get("path", ""))
            and item.get("size", 0) <= MAX_FILE_BYTES
        ]

        log.info("%s/%s: %d indexable files (of %d total)", owner, repo, len(blobs), len(tree))

        # Step 3: fetch contents concurrently
        semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)
        tasks = [
            _fetch_file_content(client, owner, repo, blob["path"], blob.get("size", 0), semaphore)
            for blob in blobs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    files = []
    for result in results:
        if isinstance(result, Exception):
            log.warning("File fetch error: %s", result)
            continue
        if result is not None:
            files.append(result)

    log.info("%s/%s: successfully fetched %d files", owner, repo, len(files))
    return files


async def _fetch_tree(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
) -> list[dict]:
    """Fetches the recursive git tree. Falls back to default branch if HEAD fails."""
    for ref in ("HEAD", "main", "master"):
        url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
        try:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                return data.get("tree", [])
        except Exception:
            continue
    return []


async def _fetch_file_content(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    path: str,
    size: int,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    async with semaphore:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
        try:
            r = await client.get(url)
            if r.status_code != 200:
                return None

            data = r.json()
            encoding = data.get("encoding", "")
            raw_content = data.get("content", "")

            if encoding == "base64":
                try:
                    content = base64.b64decode(raw_content).decode("utf-8", errors="replace")
                except Exception:
                    return None
            else:
                content = raw_content

            return {
                "path": path,
                "content": content,
                "size": size,
                "language": _detect_language(path),
            }
        except Exception as exc:
            log.debug("Could not fetch %s: %s", path, exc)
            return None


# ─── Dependency extraction ────────────────────────────────────────────────────

def extract_dependencies(files: list[dict]) -> dict:
    """
    Parses known dependency manifests from the fetched files.
    Returns:
      {
        python: {package: version},
        node:   {package: version},
        go:     {module: version},
        rust:   {crate: version},
      }
    """
    result = {"python": {}, "node": {}, "go": {}, "rust": {}}

    file_map = {f["path"]: f["content"] for f in files}

    # Python — requirements.txt
    for path, content in file_map.items():
        filename = path.split("/")[-1].lower()
        if filename == "requirements.txt":
            result["python"].update(_parse_requirements_txt(content))

    # Python — pyproject.toml
    for path, content in file_map.items():
        if path.endswith("pyproject.toml"):
            result["python"].update(_parse_pyproject_toml(content))

    # Python — setup.cfg / setup.py (best-effort)
    for path, content in file_map.items():
        if path.endswith("setup.cfg"):
            result["python"].update(_parse_setup_cfg(content))

    # Node — package.json
    for path, content in file_map.items():
        filename = path.split("/")[-1]
        if filename == "package.json" and "node_modules" not in path:
            result["node"].update(_parse_package_json(content))

    # Go — go.mod
    for path, content in file_map.items():
        if path.endswith("go.mod"):
            result["go"].update(_parse_go_mod(content))

    # Rust — Cargo.toml
    for path, content in file_map.items():
        if path.endswith("Cargo.toml"):
            result["rust"].update(_parse_cargo_toml(content))

    # Remove empty ecosystems
    return {k: v for k, v in result.items() if v}


def _parse_requirements_txt(content: str) -> dict:
    deps = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Handle: package==1.0, package>=1.0, package~=1.0, package[extra]==1.0
        m = re.match(r"^([A-Za-z0-9_\-]+)(?:\[.*?\])?([=~><!\^]+)([\w.\-*]+)", line)
        if m:
            deps[m.group(1).lower()] = m.group(2) + m.group(3)
        else:
            # bare package name
            name = re.match(r"^([A-Za-z0-9_\-]+)", line)
            if name:
                deps[name.group(1).lower()] = "any"
    return deps


def _parse_pyproject_toml(content: str) -> dict:
    deps = {}
    in_deps = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped in ("[tool.poetry.dependencies]", "[project]"):
            in_deps = True
            continue
        if stripped.startswith("[") and stripped != "[tool.poetry.dependencies]":
            # check if still in a relevant section
            if not stripped.startswith("[tool.poetry.dev") and in_deps:
                in_deps = False

        if in_deps:
            m = re.match(r'^([A-Za-z0-9_\-]+)\s*=\s*["\']?([^"\'#\n]+)["\']?', stripped)
            if m and m.group(1).lower() not in ("python", "name", "version", "description"):
                deps[m.group(1).lower()] = m.group(2).strip()

    # Also scan dependencies = [...] array format (PEP 621)
    in_array = False
    for line in content.splitlines():
        stripped = line.strip()
        if re.match(r'^dependencies\s*=\s*\[', stripped):
            in_array = True
            # check same line
            items = re.findall(r'"([^"]+)"', stripped)
            for item in items:
                m = re.match(r"([A-Za-z0-9_\-]+)([\s>=<!~\^]+)([\w.\-*]+)?", item)
                if m:
                    deps[m.group(1).lower()] = (m.group(2) + (m.group(3) or "")).strip()
            continue
        if in_array:
            if "]" in stripped:
                in_array = False
                continue
            items = re.findall(r'"([^"]+)"', stripped)
            for item in items:
                m = re.match(r"([A-Za-z0-9_\-]+)([\s>=<!~\^]+)([\w.\-*]+)?", item)
                if m:
                    deps[m.group(1).lower()] = (m.group(2) + (m.group(3) or "")).strip()

    return deps


def _parse_setup_cfg(content: str) -> dict:
    deps = {}
    in_install = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "install_requires":
            in_install = True
            continue
        if stripped.startswith("[") or (in_install and stripped.startswith("[")):
            in_install = False
        if in_install and stripped and not stripped.startswith("#"):
            m = re.match(r"([A-Za-z0-9_\-]+)([>=<!~\^]+[\w.\-]+)?", stripped)
            if m:
                deps[m.group(1).lower()] = m.group(2) or "any"
    return deps


def _parse_package_json(content: str) -> dict:
    deps = {}
    try:
        data = json.loads(content)
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            for pkg, version in data.get(section, {}).items():
                deps[pkg] = version
    except json.JSONDecodeError:
        pass
    return deps


def _parse_go_mod(content: str) -> dict:
    deps = {}
    in_require = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "require (":
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        # single-line require
        m = re.match(r"^require\s+(\S+)\s+(\S+)", stripped)
        if m:
            deps[m.group(1)] = m.group(2)
            continue
        if in_require:
            parts = stripped.split()
            if len(parts) >= 2 and not stripped.startswith("//"):
                deps[parts[0]] = parts[1]
    return deps


def _parse_cargo_toml(content: str) -> dict:
    deps = {}
    in_deps = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped in ("[dependencies]", "[dev-dependencies]", "[build-dependencies]"):
            in_deps = True
            continue
        if stripped.startswith("[") and in_deps:
            in_deps = False
        if in_deps and stripped and not stripped.startswith("#"):
            m = re.match(r'^([A-Za-z0-9_\-]+)\s*=\s*["\']?([^"\'{\n]+)["\']?', stripped)
            if m:
                deps[m.group(1)] = m.group(2).strip()
    return deps


# ─── Code pattern detection ───────────────────────────────────────────────────

def extract_code_patterns(files: list[dict]) -> dict:
    """
    Analyses file paths and contents to detect:
      - framework
      - folder_structure (flat | src-layout | monorepo | ...)
      - naming_conventions (snake_case | camelCase | kebab-case)
      - import_style (absolute | relative | mixed)
      - entry_points ([main.py, index.ts, ...])
      - test_framework (pytest | jest | go-test | ...)
    """
    paths = [f["path"] for f in files]
    file_map = {f["path"]: f["content"] for f in files}

    return {
        "framework": _detect_framework(paths, file_map),
        "folder_structure": _detect_folder_structure(paths),
        "naming_conventions": _detect_naming_conventions(paths),
        "import_style": _detect_import_style(file_map),
        "entry_points": _detect_entry_points(paths),
        "test_framework": _detect_test_framework(paths, file_map),
    }


def _detect_framework(paths: list[str], file_map: dict) -> str:
    path_set = set(p.lower() for p in paths)
    all_content = " ".join(v for v in file_map.values() if len(v) < 10000)

    # Python frameworks
    if any("fastapi" in c.lower() for c in file_map.values() if "requirements" in "x"):
        pass  # handled below via deps

    if any("from fastapi" in c or "import fastapi" in c for c in file_map.values()):
        return "FastAPI"
    if any("from flask" in c or "import flask" in c for c in file_map.values()):
        return "Flask"
    if any("from django" in c or "import django" in c for c in file_map.values()):
        return "Django"

    # JS/TS frameworks
    if any("next.config" in p for p in path_set):
        return "Next.js"
    if any("nuxt.config" in p for p in path_set):
        return "Nuxt.js"
    if any("svelte.config" in p for p in path_set):
        return "SvelteKit"
    if any("vite.config" in p for p in path_set):
        if any("react" in c for c in file_map.values() if ".tsx" in "x"):
            return "React + Vite"
        return "Vite"
    if any("angular.json" in p for p in path_set):
        return "Angular"
    if any("vue.config" in p for p in path_set) or any(".vue" in p for p in path_set):
        return "Vue.js"

    # Go
    if any(p.endswith("main.go") for p in paths):
        if any("gin-gonic/gin" in c for c in file_map.values()):
            return "Go + Gin"
        if any("labstack/echo" in c for c in file_map.values()):
            return "Go + Echo"
        if any("gorilla/mux" in c for c in file_map.values()):
            return "Go + Gorilla"
        return "Go"

    # Rust
    if any(p.endswith("main.rs") for p in paths):
        if any("actix-web" in c for c in file_map.values()):
            return "Rust + Actix"
        if any("axum" in c for c in file_map.values()):
            return "Rust + Axum"
        return "Rust"

    # Generic detection by dominant language
    ext_counts: dict[str, int] = {}
    for p in paths:
        ext = "." + p.split(".")[-1] if "." in p.split("/")[-1] else ""
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    dominant = max(ext_counts, key=lambda k: ext_counts[k], default="")
    lang_map = {
        ".py": "Python", ".ts": "TypeScript", ".tsx": "React/TypeScript",
        ".js": "JavaScript", ".jsx": "React", ".go": "Go",
        ".rs": "Rust", ".rb": "Ruby", ".java": "Java", ".cs": "C#",
    }
    return lang_map.get(dominant, "Unknown")


def _detect_folder_structure(paths: list[str]) -> str:
    top_dirs = set()
    for p in paths:
        parts = p.split("/")
        if len(parts) > 1:
            top_dirs.add(parts[0])

    if {"frontend", "backend"} & top_dirs or {"client", "server"} & top_dirs:
        return "monorepo-frontend-backend"
    if {"packages", "apps"} & top_dirs:
        return "monorepo-packages"
    if "src" in top_dirs:
        return "src-layout"
    if any(p.startswith("lib/") for p in paths):
        return "lib-layout"
    return "flat"


def _detect_naming_conventions(paths: list[str]) -> str:
    filenames = [p.split("/")[-1].split(".")[0] for p in paths if "/" in p]
    snake = sum(1 for f in filenames if "_" in f and f == f.lower())
    camel = sum(1 for f in filenames if f and f[0].islower() and any(c.isupper() for c in f))
    kebab = sum(1 for f in filenames if "-" in f and f == f.lower())
    pascal = sum(1 for f in filenames if f and f[0].isupper() and "_" not in f)

    counts = {"snake_case": snake, "camelCase": camel, "kebab-case": kebab, "PascalCase": pascal}
    return max(counts, key=lambda k: counts[k])


def _detect_import_style(file_map: dict) -> str:
    absolute = 0
    relative = 0
    for path, content in file_map.items():
        if not path.endswith(".py"):
            continue
        for line in content.splitlines():
            if line.strip().startswith("from .") or line.strip().startswith("from .."):
                relative += 1
            elif line.strip().startswith("from ") or line.strip().startswith("import "):
                absolute += 1

    if relative == 0 and absolute == 0:
        return "unknown"
    if relative > absolute * 0.3:
        return "mixed" if absolute > relative * 0.3 else "relative"
    return "absolute"


def _detect_entry_points(paths: list[str]) -> list[str]:
    candidates = [
        "main.py", "app.py", "run.py", "server.py", "wsgi.py", "asgi.py",
        "index.js", "index.ts", "main.ts", "app.ts", "server.ts",
        "main.go", "main.rs", "main.rb",
    ]
    found = []
    for p in paths:
        filename = p.split("/")[-1]
        if filename in candidates:
            found.append(p)
    return found


def _detect_test_framework(paths: list[str], file_map: dict) -> str:
    path_set = set(p.lower() for p in paths)

    if any("pytest" in c for c in file_map.values() if len(c) < 5000):
        return "pytest"
    if any("unittest" in c for c in file_map.values() if len(c) < 5000):
        return "unittest"
    if any("describe(" in c or "it(" in c for c in file_map.values() if len(c) < 5000):
        if any(".spec.ts" in p or ".test.ts" in p for p in path_set):
            return "jest"
        return "mocha/jest"
    if any(p.endswith("_test.go") for p in path_set):
        return "go-test"
    if any(p.endswith("_test.rs") or "tests/" in p for p in path_set):
        return "rust-test"
    return "unknown"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _auth_headers(token: Optional[str]) -> dict:
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _should_index(path: str) -> bool:
    """Returns True if the file at this path should be indexed."""
    lower = path.lower()
    parts = lower.split("/")

    # Skip hidden dirs and known noise dirs
    for part in parts[:-1]:
        if part in SKIP_DIRS or part.startswith("."):
            return False

    filename = parts[-1]
    if filename.startswith("."):
        # allow: .env.example, .gitignore, .dockerignore, Makefile etc.
        if filename not in (".env.example", ".gitignore", ".dockerignore", ".editorconfig"):
            return False

    # Skip by extension
    for ext in SKIP_EXTENSIONS:
        if lower.endswith(ext):
            return False

    # Skip lock files by name
    if filename in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml",
                    "poetry.lock", "cargo.lock", "go.sum", "composer.lock"):
        return False

    return True


def _detect_language(path: str) -> str:
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "tsx", ".jsx": "jsx", ".go": "go", ".rs": "rust",
        ".rb": "ruby", ".java": "java", ".cs": "csharp", ".cpp": "cpp",
        ".c": "c", ".h": "c", ".hpp": "cpp", ".php": "php",
        ".swift": "swift", ".kt": "kotlin", ".scala": "scala",
        ".sh": "bash", ".bash": "bash", ".zsh": "bash",
        ".yaml": "yaml", ".yml": "yaml", ".json": "json",
        ".toml": "toml", ".ini": "ini", ".cfg": "ini",
        ".md": "markdown", ".rst": "rst", ".txt": "text",
        ".html": "html", ".css": "css", ".scss": "scss", ".sass": "sass",
        ".sql": "sql", ".graphql": "graphql", ".proto": "protobuf",
        ".dockerfile": "dockerfile",
    }
    lower = path.lower()
    if lower.endswith("dockerfile") or lower.split("/")[-1] == "dockerfile":
        return "dockerfile"
    if lower.endswith("makefile") or lower.split("/")[-1] == "makefile":
        return "makefile"
    for ext, lang in ext_map.items():
        if lower.endswith(ext):
            return lang
    return "text"


def _raise_for_status(response: httpx.Response, context: str):
    if response.status_code == 403:
        raise RuntimeError(
            f"GitHub API rate limit or auth error fetching {context}. "
            "Set GITHUB_TOKEN in .env for higher rate limits."
        )
    if response.status_code == 404:
        raise RuntimeError(f"Not found: {context}. Is the repo public?")
    if response.status_code >= 400:
        raise RuntimeError(
            f"GitHub API error {response.status_code} fetching {context}: {response.text[:200]}"
        )
