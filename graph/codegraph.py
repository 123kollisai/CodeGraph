import json
import networkx as nx
from pathlib import Path


# ── Node/Edge type constants ──────────────────────────────────────────────────
FILE     = "File"
CLASS    = "Class"
FUNCTION = "Function"

IMPORTS    = "IMPORTS"
CALLS      = "CALLS"
DEPENDS_ON = "DEPENDS_ON"
EXTENDS    = "EXTENDS"
CONTAINS   = "CONTAINS"


class CodeGraph:
    def __init__(self):
        self.G = nx.DiGraph()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _file_id(self, file: str) -> str:
        return f"file::{file}"

    def _func_id(self, file: str, func: str) -> str:
        return f"func::{file}::{func}"

    def _class_id(self, file: str, cls: str) -> str:
        return f"class::{file}::{cls}"

    def _add_node(self, node_id: str, **attrs):
        self.G.add_node(node_id, **attrs)

    def _add_edge(self, src: str, dst: str, rel: str):
        if src in self.G and dst in self.G:
            self.G.add_edge(src, dst, relation=rel)

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self, parsed_data: list[dict]):
        """Build graph from repo_parser output."""

        # ── Pass 1: Register all nodes ────────────────────────────────────────
        for entry in parsed_data:
            file = entry["file"]
            fid  = self._file_id(file)
            self._add_node(fid, type=FILE, name=file)

            for func in entry.get("functions", []):
                self._add_node(self._func_id(file, func), type=FUNCTION, name=func, file=file)

            for cls in entry.get("classes", []):
                self._add_node(self._class_id(file, cls["name"]), type=CLASS, name=cls["name"], file=file)

        # ── Pass 2: Build a lookup  func_name → list of node_ids ─────────────
        # Used to resolve CALLS edges across files
        func_lookup: dict[str, list[str]] = {}
        for nid, data in self.G.nodes(data=True):
            if data.get("type") == FUNCTION:
                func_lookup.setdefault(data["name"], []).append(nid)

        # ── Pass 3: Add edges ─────────────────────────────────────────────────
        for entry in parsed_data:
            file = entry["file"]
            fid  = self._file_id(file)

            # CONTAINS: File → Function / Class
            for func in entry.get("functions", []):
                self._add_edge(fid, self._func_id(file, func), CONTAINS)
            for cls in entry.get("classes", []):
                cid = self._class_id(file, cls["name"])
                self._add_edge(fid, cid, CONTAINS)

                # EXTENDS: Class → Base class
                for base in cls.get("bases", []):
                    # Try to find base class node
                    for nid, data in self.G.nodes(data=True):
                        if data.get("type") == CLASS and data.get("name") == base:
                            self._add_edge(cid, nid, EXTENDS)

                # CONTAINS: Class → its methods
                for method in cls.get("methods", []):
                    mid = self._func_id(file, method)
                    if mid in self.G:
                        self._add_edge(cid, mid, CONTAINS)

            # IMPORTS + DEPENDS_ON: resolve import strings to file nodes
            for imp in entry.get("imports", []):
                # e.g. "core.workflow_engine.WorkflowEngine" → try matching file node
                imp_path = imp.replace(".", "/")
                matched_fid = None
                for nid, data in self.G.nodes(data=True):
                    if data.get("type") == FILE:
                        node_file = data["name"].replace("\\", "/").replace(".py", "")
                        # Exact match or import is a sub-path of the file
                        if imp_path == node_file or imp_path.startswith(node_file + "/"):
                            matched_fid = nid
                            break
                        # Try progressively shorter prefixes of the import path
                        # to match the tail of the file path.
                        # e.g. import "utils.logger_config.get_logger"
                        #   → try "utils/logger_config/get_logger", "utils/logger_config", "utils"
                        #   → "utils/logger_config" matches tail of "core/utils/logger_config" ✓
                        parts = imp_path.split("/")
                        for k in range(len(parts), 0, -1):
                            candidate = "/".join(parts[:k])
                            if node_file == candidate or node_file.endswith("/" + candidate):
                                matched_fid = nid
                                break
                        if matched_fid:
                            break
                if matched_fid and matched_fid != fid:
                    self._add_edge(fid, matched_fid, IMPORTS)
                    self._add_edge(fid, matched_fid, DEPENDS_ON)

            # CALLS: Function → Function (cross-file)
            for func_detail in entry.get("function_details", []):
                caller_id = self._func_id(file, func_detail["name"])
                if caller_id not in self.G:
                    continue
                for call in func_detail.get("calls", []):
                    # Normalize: "obj.method" → try "method" lookup
                    call_name = call.split(".")[-1]
                    candidates = func_lookup.get(call_name, [])
                    for callee_id in candidates:
                        if callee_id != caller_id:
                            self.G.add_edge(caller_id, callee_id, relation=CALLS)

    # ── Query API ─────────────────────────────────────────────────────────────

    def get_callers(self, func_name: str) -> list[str]:
        """Return all functions that call the given function."""
        results = []
        for nid, data in self.G.nodes(data=True):
            if data.get("type") == FUNCTION and data.get("name") == func_name:
                for pred in self.G.predecessors(nid):
                    if self.G[pred][nid].get("relation") == CALLS:
                        results.append(self.G.nodes[pred].get("name", pred))
        return results

    def get_callees(self, func_name: str) -> list[str]:
        """Return all functions called by the given function."""
        results = []
        for nid, data in self.G.nodes(data=True):
            if data.get("type") == FUNCTION and data.get("name") == func_name:
                for succ in self.G.successors(nid):
                    if self.G[nid][succ].get("relation") == CALLS:
                        results.append(self.G.nodes[succ].get("name", succ))
        return results

    def get_dependencies(self, file_name: str, depth: int = 3) -> list[str]:
        """BFS traversal from a file node up to given depth."""
        fid = self._file_id(file_name)
        if fid not in self.G:
            return []
        visited, queue = set(), [(fid, 0)]
        result = []
        while queue:
            node, d = queue.pop(0)
            if node in visited or d > depth:
                continue
            visited.add(node)
            result.append(node)
            for succ in self.G.successors(node):
                queue.append((succ, d + 1))
        return result

    def stats(self) -> dict:
        type_counts = {}
        for _, data in self.G.nodes(data=True):
            t = data.get("type", "Unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        rel_counts = {}
        for _, _, data in self.G.edges(data=True):
            r = data.get("relation", "Unknown")
            rel_counts[r] = rel_counts.get(r, 0) + 1
        return {"nodes": self.G.number_of_nodes(), "edges": self.G.number_of_edges(),
                "node_types": type_counts, "edge_types": rel_counts}

    # ── Persist ───────────────────────────────────────────────────────────────

    def save(self, path: str = "codegraph.json"):
        data = nx.node_link_data(self.G, edges="links")
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Graph saved -> {path}")

    def load(self, path: str = "codegraph.json"):
        with open(path) as f:
            data = json.load(f)
        self.G = nx.node_link_graph(data, edges="links")
        print(f"Graph loaded <- {path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    parser_output = sys.argv[1] if len(sys.argv) > 1 else "parser_output.json"

    with open(parser_output) as f:
        parsed = json.load(f)

    cg = CodeGraph()
    cg.build(parsed)
    cg.save()

    s = cg.stats()
    print(f"Nodes : {s['nodes']}  |  Edges: {s['edges']}")
    print(f"Node types : {s['node_types']}")
    print(f"Edge types : {s['edge_types']}")
