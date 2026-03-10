import ast
import json
import os
from pathlib import Path


SKIP_DIRS = {"venv", ".venv", "__pycache__", ".git", "node_modules", ".idea", "dist", "build"}


def _extract_calls(node):
    calls = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                calls.append(f"{ast.unparse(func.value)}.{func.attr}")
            elif isinstance(func, ast.Name):
                calls.append(func.id)
    return list(dict.fromkeys(calls))  # deduplicate, preserve order


def parse_python_file(filepath: str) -> dict:
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return {"file": filepath, "error": "SyntaxError", "classes": [], "functions": [], "imports": [], "calls": []}

    classes = []
    functions = []
    imports = []
    calls = []

    for node in ast.walk(tree):
        # Top-level imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.asname or alias.name}" if module else alias.name)

        # Classes
        elif isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            methods = [n.name for n in ast.walk(node) if isinstance(n, ast.FunctionDef)]
            classes.append({"name": node.name, "bases": bases, "methods": methods})

        # Functions (top-level + class methods)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_calls = _extract_calls(node)
            functions.append({"name": node.name, "calls": func_calls})
            calls.extend(func_calls)

    return {
        "file": filepath,
        "classes": classes,
        "functions": [f["name"] for f in functions],
        "function_details": functions,
        "imports": list(dict.fromkeys(imports)),
        "calls": list(dict.fromkeys(calls)),
    }


def parse_repository(repo_path: str) -> list[dict]:
    repo_path = Path(repo_path).resolve()
    results = []

    for root, dirs, files in os.walk(repo_path):
        # Prune skip dirs in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = str(Path(root) / fname)
            parsed = parse_python_file(fpath)
            parsed["file"] = str(Path(fpath).relative_to(repo_path))
            results.append(parsed)

    return results


def save_output(results: list[dict], output_path: str = "parser_output.json"):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Parsed {len(results)} files -> {output_path}")


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    data = parse_repository(target)
    save_output(data)
