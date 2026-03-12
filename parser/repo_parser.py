import ast
import json
import os
import re
from pathlib import Path


SKIP_DIRS = {"venv", ".venv", "__pycache__", ".git", "node_modules", ".idea", "dist", "build"}
JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}


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
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}" if module else alias.name)

        # Classes
        elif isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            methods = [n.name for n in ast.walk(node) if isinstance(n, ast.FunctionDef)]
            classes.append({"name": node.name, "bases": bases, "methods": methods,
                            "line_start": node.lineno, "line_end": node.end_lineno})

        # Functions (top-level + class methods)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_calls = _extract_calls(node)
            functions.append({"name": node.name, "calls": func_calls,
                              "line_start": node.lineno, "line_end": node.end_lineno})
            calls.extend(func_calls)

    return {
        "file": filepath,
        "classes": classes,
        "functions": [f["name"] for f in functions],
        "function_details": functions,
        "imports": list(dict.fromkeys(imports)),
        "calls": list(dict.fromkeys(calls)),
    }


# ── JS/TS Parser ─────────────────────────────────────────────────────────────

RE_NAMED_FUNC = re.compile(
    r'^[ \t]*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(',
    re.MULTILINE
)
RE_ARROW_FUNC = re.compile(
    r'^[ \t]*(?:export\s+(?:default\s+)?)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[a-zA-Z_]\w*)\s*=>',
    re.MULTILINE
)
RE_CLASS = re.compile(
    r'^[ \t]*(?:export\s+(?:default\s+)?)?class\s+(\w+)(?:\s+extends\s+(\w+))?',
    re.MULTILINE
)
RE_METHOD = re.compile(
    r'^[ \t]+(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?(\w+)\s*\([^)]*\)\s*\{',
    re.MULTILINE
)
RE_IMPORT_ES6 = re.compile(
    r'''import\s+(?:(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s*,?\s*)*\s*from\s+['"]([^'"]+)['"]''',
    re.MULTILINE
)
RE_REQUIRE = re.compile(
    r'''(?:const|let|var)\s+(?:\w+|\{[^}]*\})\s*=\s*require\s*\(\s*['"]([^'"]+)['"]\s*\)''',
    re.MULTILINE
)
RE_CALL = re.compile(r'(?<!\w)(\w+(?:\.\w+)*)\s*\(')

JS_KEYWORDS = {'if', 'for', 'while', 'switch', 'catch', 'return', 'throw',
               'new', 'typeof', 'instanceof', 'delete', 'void', 'class',
               'import', 'export', 'from', 'const', 'let', 'var', 'function',
               'async', 'await', 'yield', 'super', 'this', 'else', 'try',
               'finally', 'do', 'in', 'of', 'with', 'debugger', 'case',
               'default', 'break', 'continue', 'true', 'false', 'null',
               'undefined', 'console', 'window', 'document', 'Array', 'Object',
               'String', 'Number', 'Boolean', 'Promise', 'Map', 'Set'}


def _find_block_end(lines: list[str], start_idx: int) -> int:
    depth = 0
    found_open = False
    for i in range(start_idx, len(lines)):
        for ch in lines[i]:
            if ch == '{':
                depth += 1
                found_open = True
            elif ch == '}':
                depth -= 1
                if found_open and depth == 0:
                    return i + 1  # 1-based
    return min(start_idx + 50, len(lines))  # fallback


def _extract_js_calls(body: str, self_name: str) -> list[str]:
    calls = []
    for m in RE_CALL.finditer(body):
        name = m.group(1)
        simple = name.split('.')[-1]
        if simple not in JS_KEYWORDS and simple != self_name:
            calls.append(name)
    return list(dict.fromkeys(calls))


def _normalize_js_import(imp: str) -> str:
    imp = re.sub(r'^\.\.?/', '', imp)
    imp = re.sub(r'^@/', '', imp)
    imp = re.sub(r'\.(js|jsx|ts|tsx|json|mjs|cjs)$', '', imp)
    return imp


def parse_javascript_file(filepath: str) -> dict:
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {"file": filepath, "error": "ReadError", "classes": [],
                "functions": [], "function_details": [], "imports": [], "calls": []}

    lines = source.splitlines()

    # --- Imports ---
    imports = []
    for m in RE_IMPORT_ES6.finditer(source):
        imports.append(_normalize_js_import(m.group(1)))
    for m in RE_REQUIRE.finditer(source):
        imports.append(_normalize_js_import(m.group(1)))
    imports = list(dict.fromkeys(imports))

    # --- Classes ---
    classes = []
    for m in RE_CLASS.finditer(source):
        cls_name = m.group(1)
        bases = [m.group(2)] if m.group(2) else []
        cls_line_start = source[:m.start()].count('\n') + 1
        cls_line_end = _find_block_end(lines, cls_line_start - 1)
        class_body = '\n'.join(lines[cls_line_start:cls_line_end])
        methods = []
        for mm in RE_METHOD.finditer(class_body):
            mname = mm.group(1)
            if mname not in ('constructor',) and mname not in methods:
                methods.append(mname)
        if 'constructor' in class_body:
            methods = ['constructor'] + methods
        classes.append({
            "name": cls_name, "bases": bases,
            "methods": list(dict.fromkeys(methods)),
            "line_start": cls_line_start, "line_end": cls_line_end,
        })

    # --- Functions (named + arrow) ---
    function_details = []
    func_names = []

    for m in RE_NAMED_FUNC.finditer(source):
        fname = m.group(1)
        line_start = source[:m.start()].count('\n') + 1
        line_end = _find_block_end(lines, line_start - 1)
        body = '\n'.join(lines[line_start - 1:line_end])
        calls = _extract_js_calls(body, fname)
        function_details.append({
            "name": fname, "calls": calls,
            "line_start": line_start, "line_end": line_end,
        })
        func_names.append(fname)

    for m in RE_ARROW_FUNC.finditer(source):
        fname = m.group(1)
        if fname in func_names:
            continue
        line_start = source[:m.start()].count('\n') + 1
        line_end = _find_block_end(lines, line_start - 1)
        body = '\n'.join(lines[line_start - 1:line_end])
        calls = _extract_js_calls(body, fname)
        function_details.append({
            "name": fname, "calls": calls,
            "line_start": line_start, "line_end": line_end,
        })
        func_names.append(fname)

    # Class methods as function_details too
    for cls in classes:
        for method_name in cls["methods"]:
            if method_name not in func_names:
                for i in range(cls["line_start"] - 1, min(cls["line_end"], len(lines))):
                    if re.search(rf'\b{re.escape(method_name)}\s*\(', lines[i]):
                        mline_start = i + 1
                        mline_end = _find_block_end(lines, i)
                        body = '\n'.join(lines[mline_start - 1:mline_end])
                        calls = _extract_js_calls(body, method_name)
                        function_details.append({
                            "name": method_name, "calls": calls,
                            "line_start": mline_start, "line_end": mline_end,
                        })
                        func_names.append(method_name)
                        break

    all_calls = []
    for fd in function_details:
        all_calls.extend(fd["calls"])

    return {
        "file": filepath,
        "classes": classes,
        "functions": list(dict.fromkeys(func_names)),
        "function_details": function_details,
        "imports": imports,
        "calls": list(dict.fromkeys(all_calls)),
    }


# ── Repository traversal ─────────────────────────────────────────────────────

def parse_repository(repo_path: str) -> list[dict]:
    repo_path = Path(repo_path).resolve()
    results = []

    for root, dirs, files in os.walk(repo_path):
        # Prune skip dirs in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in files:
            ext = Path(fname).suffix
            if ext == ".py":
                parser_fn = parse_python_file
            elif ext in JS_EXTENSIONS:
                parser_fn = parse_javascript_file
            else:
                continue
            fpath = str(Path(root) / fname)
            parsed = parser_fn(fpath)
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
