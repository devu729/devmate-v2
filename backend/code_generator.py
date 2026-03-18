"""
code_generator.py
Core code generation logic for DevMate v2.

Public functions:
  build_generation_prompt(feature_request, repo_context)   → str
  parse_generated_code(response)                           → list[{file, code, action, explanation}]
  validate_generated_code(code_blocks, repo_file_paths)    → {valid, issues}
"""

import re
import json
import logging
from typing import Optional

log = logging.getLogger("devmate.code_generator")

# Actions the agent can specify for a code block
VALID_ACTIONS = {"create", "modify", "append"}

# Extensions that typically don't need import validation
SKIP_IMPORT_VALIDATION = {".md", ".yaml", ".yml", ".json", ".toml", ".env", ".txt", ".sh", ".bat"}


# ─── Prompt builder ───────────────────────────────────────────────────────────

def build_generation_prompt(feature_request: str, repo_context: dict) -> str:
    """
    Constructs the user-facing prompt for code generation.
    Injects repo structure, naming conventions, framework, and dep versions
    so the agent generates code that fits the existing codebase exactly.
    """
    repo_name      = repo_context.get("repo_name", "this repo")
    file_paths     = repo_context.get("file_paths", [])
    dependencies   = repo_context.get("dependencies", {})
    code_patterns  = repo_context.get("code_patterns", {})

    framework         = code_patterns.get("framework", "unknown")
    naming            = code_patterns.get("naming_conventions", "unknown")
    import_style      = code_patterns.get("import_style", "unknown")
    folder_structure  = code_patterns.get("folder_structure", "unknown")
    entry_points      = code_patterns.get("entry_points", [])

    # Build dependency constraint block (top 15 per ecosystem)
    dep_constraints = _format_dep_constraints(dependencies)

    # Find likely related files for the feature request
    relevant_paths = _find_relevant_paths(feature_request, file_paths)
    relevant_section = (
        "\n".join(f"  {p}" for p in relevant_paths)
        if relevant_paths
        else "  (search the Knowledge Base for relevant files)"
    )

    # Infer likely output location from folder structure
    suggested_location = _suggest_file_location(feature_request, folder_structure, framework)

    return f"""You are generating code for the {repo_name} codebase.

FEATURE REQUEST:
{feature_request}

CODEBASE CONSTRAINTS — follow these exactly:
  Framework:          {framework}
  Folder structure:   {folder_structure}
  Naming convention:  {naming}
  Import style:       {import_style}
  Entry points:       {', '.join(entry_points) if entry_points else 'none detected'}

DEPENDENCY VERSIONS (use these exact versions, no upgrades):
{dep_constraints}

LIKELY RELATED FILES (search KB for these first):
{relevant_section}

SUGGESTED OUTPUT LOCATION: {suggested_location}

INSTRUCTIONS:
1. Search the Knowledge Base for existing files related to this feature.
2. Read their content to understand current patterns.
3. Generate code that integrates seamlessly — same style, same imports, same structure.
4. Only reference file paths that exist in the repo (or new files you are creating).
5. For existing files, show the complete modified version (action: "modify"), not just a diff.
6. Return ONLY the JSON structure from your system prompt. No markdown, no explanation outside JSON.
"""


def _format_dep_constraints(dependencies: dict) -> str:
    lines = []
    for ecosystem, pkgs in dependencies.items():
        if not pkgs:
            continue
        lines.append(f"  [{ecosystem.upper()}]")
        for pkg, ver in list(pkgs.items())[:15]:
            lines.append(f"    {pkg}: {ver}")
    return "\n".join(lines) if lines else "  (none detected — infer from existing imports)"


def _find_relevant_paths(feature_request: str, file_paths: list[str]) -> list[str]:
    """
    Heuristically surfaces file paths that are likely relevant to the feature.
    Uses keyword matching against the request.
    """
    request_lower = feature_request.lower()

    # Extract keywords: words longer than 3 chars, not stopwords
    stopwords = {
        "add", "create", "make", "build", "implement", "write", "generate",
        "the", "for", "with", "that", "this", "from", "into", "using",
        "new", "and", "our", "can", "will", "should", "need",
    }
    keywords = [
        w for w in re.findall(r"[a-z][a-z0-9_]{2,}", request_lower)
        if w not in stopwords
    ]

    if not keywords:
        return []

    scored: list[tuple[int, str]] = []
    for path in file_paths:
        path_lower = path.lower()
        score = sum(1 for kw in keywords if kw in path_lower)
        if score > 0:
            scored.append((score, path))

    scored.sort(reverse=True)
    return [p for _, p in scored[:10]]


def _suggest_file_location(
    feature_request: str,
    folder_structure: str,
    framework: str,
) -> str:
    """Suggests where a new file for this feature might live."""
    req_lower = feature_request.lower()

    # Detect feature type from keywords
    if any(w in req_lower for w in ("test", "spec", "unit")):
        return "tests/"

    if any(w in req_lower for w in ("route", "endpoint", "api", "handler")):
        if "src-layout" in folder_structure:
            return "src/routes/ or src/api/"
        if "monorepo" in folder_structure:
            return "backend/routes/ or backend/api/"
        return "routes/ or api/"

    if any(w in req_lower for w in ("model", "schema", "entity", "table")):
        return "models/ or schemas/"

    if any(w in req_lower for w in ("middleware", "auth", "permission", "guard")):
        return "middleware/"

    if any(w in req_lower for w in ("util", "helper", "common", "shared")):
        return "utils/ or lib/"

    if any(w in req_lower for w in ("config", "setting", "env")):
        return "config/"

    if any(w in req_lower for w in ("service", "client", "integration")):
        return "services/"

    if any(w in req_lower for w in ("component", "page", "view", "ui")):
        if "Next.js" in framework or "React" in framework:
            return "components/ or pages/"
        if "Vue" in framework:
            return "components/ or views/"

    return "(infer from existing file structure)"


# ─── Response parser ──────────────────────────────────────────────────────────

def parse_generated_code(response: str) -> list[dict]:
    """
    Parses the agent's JSON response into a clean list of code blocks.
    Each block: {file, code, action, explanation}

    Handles:
      - Clean JSON
      - JSON wrapped in markdown fences
      - JSON with surrounding prose
      - Partial / malformed JSON (best-effort extraction)
    """
    if not response or not response.strip():
        return []

    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", response.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned.strip(), flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # Try to isolate the JSON object
    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        cleaned = json_match.group(0)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: try to extract code blocks with regex
        log.warning("Could not parse JSON response — attempting regex extraction")
        return _regex_extract_code_blocks(response)

    raw_blocks = data.get("code_blocks", [])
    if not isinstance(raw_blocks, list):
        return []

    blocks = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue
        file_path = block.get("file", "").strip()
        code = block.get("code", "").strip()
        action = block.get("action", "create").lower().strip()
        explanation = block.get("explanation", "").strip()

        if not file_path or not code:
            continue

        if action not in VALID_ACTIONS:
            action = "create"

        blocks.append({
            "file": file_path,
            "code": code,
            "action": action,
            "explanation": explanation,
        })

    return blocks


def _regex_extract_code_blocks(response: str) -> list[dict]:
    """
    Last-resort extractor: finds ```lang ... ``` fences in a prose response
    and pairs them with the nearest file path mention.
    """
    blocks = []
    # Find all code fences with optional language tag
    fence_pattern = re.compile(r"```(\w*)\s*\n(.*?)```", re.DOTALL)
    path_pattern  = re.compile(
        r"(?:file|path|create|modify|update|in)\s*[:`]?\s*[`'\"]?([^\s`'\"]+\.[a-zA-Z]{1,6})[`'\"]?",
        re.IGNORECASE,
    )

    text_chunks = re.split(r"```\w*\s*\n.*?```", response, flags=re.DOTALL)
    fences = fence_pattern.findall(response)

    for i, (lang, code) in enumerate(fences):
        file_path = ""
        # Look in the preceding text chunk for a file path
        if i < len(text_chunks):
            path_match = path_pattern.search(text_chunks[i])
            if path_match:
                file_path = path_match.group(1)

        if not file_path:
            file_path = f"generated_file_{i + 1}.{lang or 'txt'}"

        blocks.append({
            "file": file_path,
            "code": code.strip(),
            "action": "create",
            "explanation": f"Extracted from prose response (block {i + 1})",
        })

    return blocks


# ─── Validator ────────────────────────────────────────────────────────────────

def validate_generated_code(
    code_blocks: list[dict],
    repo_file_paths: list[str],
) -> dict:
    """
    Validates generated code blocks against the known repo file paths.

    Checks:
      1. For "modify"/"append" blocks — file must exist in the repo
      2. Imports reference modules that exist in the repo (Python only)
      3. No obviously hallucinated paths (e.g. `/home/user/`, `C:\\Users\\`)
      4. No empty code blocks

    Returns: {valid: bool, issues: [str]}
    """
    issues: list[str] = []
    path_set = set(repo_file_paths)

    # Build a set of importable module names from Python paths
    python_modules = _extract_python_module_names(repo_file_paths)

    for block in code_blocks:
        file_path = block.get("file", "")
        code      = block.get("code", "")
        action    = block.get("action", "create")

        if not code.strip():
            issues.append(f"Empty code block for file: {file_path or '(unnamed)'}")
            continue

        # Rule 1: modify/append — file must exist
        if action in ("modify", "append") and file_path not in path_set:
            issues.append(
                f"Action '{action}' on '{file_path}' but this file was not found in the repo. "
                "If it's a new file, use action 'create' instead."
            )

        # Rule 2: no absolute system paths
        if _has_absolute_system_path(file_path):
            issues.append(
                f"File path '{file_path}' looks like an absolute system path. "
                "Use repo-relative paths only."
            )

        # Rule 3: Python import validation
        ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
        if ext == ".py" and python_modules:
            bad_imports = _find_unknown_local_imports(code, python_modules)
            for imp in bad_imports:
                issues.append(
                    f"In '{file_path}': import '{imp}' does not match any file in the repo. "
                    "This may be a hallucinated module name."
                )

        # Rule 4: skip-extension files shouldn't have code validation
        # (already handled by checking ext != ".py")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
    }


def _extract_python_module_names(file_paths: list[str]) -> set[str]:
    """
    Converts repo file paths to importable Python module names.
    e.g. backend/models/user.py  →  {"backend", "models", "user", "backend.models.user"}
    """
    modules = set()
    for path in file_paths:
        if not path.endswith(".py"):
            continue
        # Remove .py and convert slashes to dots
        module_path = path[:-3].replace("/", ".").replace("\\", ".")
        parts = module_path.split(".")
        # Add each part and each prefix
        for i in range(len(parts)):
            modules.add(parts[i])                       # leaf name
            modules.add(".".join(parts[:i + 1]))        # dotted prefix
    return modules


def _find_unknown_local_imports(code: str, known_modules: set[str]) -> list[str]:
    """
    Finds `from X import Y` and `import X` statements where X looks like
    a local module (no dots in top-level, not a stdlib/well-known name)
    but doesn't appear in known_modules.
    """
    # Well-known stdlib and popular third-party prefixes to skip
    stdlib_prefixes = {
        "os", "sys", "re", "io", "json", "time", "math", "random", "copy",
        "typing", "pathlib", "datetime", "collections", "functools", "itertools",
        "contextlib", "dataclasses", "enum", "abc", "logging", "traceback",
        "threading", "asyncio", "concurrent", "subprocess", "hashlib", "uuid",
        "base64", "urllib", "http", "email", "html", "xml", "csv", "sqlite3",
        "unittest", "inspect", "importlib", "pkgutil", "warnings", "weakref",
        # Common third-party
        "fastapi", "flask", "django", "starlette", "pydantic", "sqlalchemy",
        "alembic", "celery", "redis", "httpx", "aiohttp", "requests",
        "pytest", "numpy", "pandas", "scipy", "sklearn", "torch", "tensorflow",
        "boto3", "openai", "anthropic", "dotenv", "uvicorn", "gunicorn",
        "click", "typer", "rich", "loguru", "structlog",
        "react", "vue", "angular", "next", "express", "axios",
        "bs4", "lxml", "beautifulsoup4", "toml", "yaml",
        "__future__", "builtins",
    }

    unknown = []
    import_pattern = re.compile(
        r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))",
        re.MULTILINE,
    )

    for match in import_pattern.finditer(code):
        raw = (match.group(1) or match.group(2) or "").strip()
        # Get the top-level module name
        top = raw.split(".")[0].split(",")[0].strip()

        if not top or top.startswith("_"):
            continue
        if top in stdlib_prefixes:
            continue
        if top in known_modules:
            continue

        # If it has dots and the dotted form is known, it's fine
        if "." in raw and raw in known_modules:
            continue

        # Only flag if it looks like a local import (no version markers, short names)
        if re.match(r"^[a-z][a-z0-9_]{1,30}$", top):
            unknown.append(top)

    return list(set(unknown))


def _has_absolute_system_path(path: str) -> bool:
    """Returns True if the path looks like an absolute OS path."""
    return bool(
        re.match(r"^/(?:home|root|usr|var|tmp|etc)/", path)
        or re.match(r"^[A-Za-z]:\\", path)
        or path.startswith("~/")
    )
