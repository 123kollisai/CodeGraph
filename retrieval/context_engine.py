import json
from pathlib import Path
from collections import deque

from graph.codegraph import CodeGraph, FUNCTION, CLASS, FILE, CALLS, CONTAINS, DEPENDS_ON


class ContextEngine:
    def __init__(self, graph: CodeGraph, repo_path: str, parser_output: list[dict]):
        self.graph     = graph
        self.repo_path = Path(repo_path)
        # Index: file_relative_path -> parsed entry
        self._file_index = {e["file"]: e for e in parser_output}

    # ── Snippet extraction ────────────────────────────────────────────────────

    def _read_snippet(self, file_rel: str, line_start: int, line_end: int) -> str:
        path = self.repo_path / file_rel
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            return "\n".join(lines[line_start - 1: line_end])
        except Exception:
            return "# <source unavailable>"

    def _get_snippet_for_node(self, node_id: str) -> str | None:
        data = self.graph.G.nodes.get(node_id)
        if not data:
            return None
        ntype = data.get("type")
        file  = data.get("file")
        name  = data.get("name")
        if not file:
            return None

        entry = self._file_index.get(file)
        if not entry:
            return None

        if ntype == FUNCTION:
            for fd in entry.get("function_details", []):
                if fd["name"] == name:
                    return self._read_snippet(file, fd["line_start"], fd["line_end"])

        elif ntype == CLASS:
            for cls in entry.get("classes", []):
                if cls["name"] == name:
                    return self._read_snippet(file, cls["line_start"], cls["line_end"])

        elif ntype == FILE:
            # Return imports section only (first 30 lines max)
            path = self.repo_path / name
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                return "\n".join(lines[:30])
            except Exception:
                return None

        return None

    # ── Graph traversal ───────────────────────────────────────────────────────

    def _find_entry_node(self, entry_point: str) -> str | None:
        """Find node by function/class/file name."""
        # Exact file match first
        fid = self.graph._file_id(entry_point)
        if fid in self.graph.G:
            return fid
        # Function or class match
        for nid, data in self.graph.G.nodes(data=True):
            if data.get("name") == entry_point and data.get("type") in (FUNCTION, CLASS):
                return nid
        return None

    def _bfs(self, start_node: str, depth: int) -> list[str]:
        """BFS traversal following CALLS and DEPENDS_ON edges."""
        visited = {}          # node_id -> depth reached
        queue   = deque([(start_node, 0)])
        order   = []

        while queue:
            node, d = queue.popleft()
            if node in visited or d > depth:
                continue
            visited[node] = d
            order.append(node)

            for succ in self.graph.G.successors(node):
                rel = self.graph.G[node][succ].get("relation", "")
                if rel in (CALLS, DEPENDS_ON, CONTAINS) and succ not in visited:
                    queue.append((succ, d + 1))

        return order

    # ── Public API ────────────────────────────────────────────────────────────

    def retrieve(self, entry_point: str, depth: int = 3) -> dict:
        """
        Retrieve relevant code context starting from entry_point.
        Returns structured dict with flow path and code snippets.
        """
        start = self._find_entry_node(entry_point)
        if not start:
            return {"error": f"Entry point '{entry_point}' not found in graph."}

        nodes_in_order = self._bfs(start, depth)

        context_items = []
        seen_files = set()

        for nid in nodes_in_order:
            data    = self.graph.G.nodes[nid]
            ntype   = data.get("type")
            name    = data.get("name")
            file    = data.get("file", name if ntype == FILE else "")
            snippet = self._get_snippet_for_node(nid)

            if snippet:
                context_items.append({
                    "node_id": nid,
                    "type":    ntype,
                    "name":    name,
                    "file":    file,
                    "snippet": snippet,
                })
                seen_files.add(file)

        return {
            "entry_point":  entry_point,
            "depth":        depth,
            "total_nodes":  len(nodes_in_order),
            "files_touched": sorted(seen_files),
            "context":      context_items,
        }

    def format_for_llm(self, retrieval_result: dict) -> str:
        """Format retrieval result as a compact string for LLM prompts."""
        if "error" in retrieval_result:
            return retrieval_result["error"]

        lines = [
            f"Entry Point : {retrieval_result['entry_point']}",
            f"Depth       : {retrieval_result['depth']}",
            f"Files       : {', '.join(retrieval_result['files_touched'])}",
            "=" * 60,
        ]
        for item in retrieval_result["context"]:
            lines.append(f"\n[{item['type']}] {item['name']}  ({item['file']})")
            lines.append("-" * 40)
            lines.append(item["snippet"])

        return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    repo_path      = sys.argv[1] if len(sys.argv) > 1 else "sample_test_app"
    entry_point    = sys.argv[2] if len(sys.argv) > 2 else "execute"
    depth          = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    with open("parser_output.json") as f:
        parsed = json.load(f)
    cg = CodeGraph()
    cg.load("codegraph.json")

    engine = ContextEngine(cg, repo_path, parsed)
    result = engine.retrieve(entry_point, depth)

    print(f"Entry : {result.get('entry_point')}")
    print(f"Nodes : {result.get('total_nodes')}")
    print(f"Files : {result.get('files_touched')}")
    print()
    print(engine.format_for_llm(result))
