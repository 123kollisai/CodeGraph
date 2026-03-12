"""
app.py  —  FastAPI backend for CodeGraph RCA
Run: uvicorn app:app --reload --port 8000
"""
import sys, os, stat, subprocess, tempfile, shutil, json
from pathlib import Path
from collections import deque
from typing import Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(Path(_PROJECT_ROOT) / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from parser.repo_parser import parse_repository
from graph.codegraph import CodeGraph, FILE, FUNCTION, CLASS, CALLS, DEPENDS_ON, CONTAINS
from rca.rca_agent import RCAAgent

app = FastAPI(title="CodeGraph RCA API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATE: dict = {"parser_output": None, "graph": None, "graph_stats": None, "repo_path": ""}

def _rm_readonly(func, path, _excinfo):
    os.chmod(path, stat.S_IWRITE)
    func(path)

# ── Serve frontend ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    html_path = Path(_PROJECT_ROOT) / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"), status_code=200)

# ── Mermaid ────────────────────────────────────────────────────────────────────

def generate_mermaid_flow(graph, entry_func, max_depth=4):
    entry_nodes = [nid for nid, d in graph.G.nodes(data=True)
                   if d.get("type") == FUNCTION and d.get("name") == entry_func]
    if not entry_nodes: return None
    visited, edges = set(), []
    queue = deque([(nid, 0) for nid in entry_nodes])
    while queue:
        node, depth = queue.popleft()
        if node in visited or depth > max_depth: continue
        visited.add(node)
        for succ in graph.G.successors(node):
            if graph.G[node][succ].get("relation") == CALLS:
                edges.append((node, succ))
                if succ not in visited: queue.append((succ, depth + 1))
    if not edges: return None
    lines = ["flowchart TD"]; node_ids = {}; counter = 0
    def get_id(nid):
        nonlocal counter
        if nid not in node_ids: node_ids[nid] = f"N{counter}"; counter += 1
        return node_ids[nid]
    defined = set()
    for src, dst in edges:
        for nid in (src, dst):
            if nid not in defined:
                data = graph.G.nodes[nid]; name = data.get("name", nid)
                file = data.get("file","").replace("\\","/"); mid = get_id(nid)
                label = f"{name}\\n{file}" if file else name
                lines.append(f'    {mid}(["{ label}"]):::entry' if nid in entry_nodes else f'    {mid}["{label}"]')
                defined.add(nid)
    for src, dst in edges: lines.append(f"    {get_id(src)} --> {get_id(dst)}")
    lines.append("    classDef entry fill:#3b82f6,stroke:#2563eb,color:#fff")
    return "\n".join(lines)

def generate_file_dep_mermaid(graph, entry_file):
    fid = graph._file_id(entry_file)
    if fid not in graph.G: return None
    visited, edges = set(), []
    queue = deque([(fid, 0)])
    while queue:
        node, depth = queue.popleft()
        if node in visited or depth > 3: continue
        visited.add(node)
        for succ in graph.G.successors(node):
            if graph.G[node][succ].get("relation") == DEPENDS_ON and graph.G.nodes.get(succ,{}).get("type") == FILE:
                edges.append((node, succ))
                if succ not in visited: queue.append((succ, depth + 1))
    if not edges: return None
    lines = ["flowchart LR"]; node_ids = {}; counter = 0
    def get_id(nid):
        nonlocal counter
        if nid not in node_ids: node_ids[nid] = f"F{counter}"; counter += 1
        return node_ids[nid]
    defined = set()
    for src, dst in edges:
        for nid in (src, dst):
            if nid not in defined:
                name = graph.G.nodes[nid].get("name", nid).replace("\\","/"); mid = get_id(nid)
                lines.append(f'    {mid}(["{name}"]):::entry' if nid == fid else f'    {mid}["{name}"]')
                defined.add(nid)
    for src, dst in edges: lines.append(f"    {get_id(src)} -->|imports| {get_id(dst)}")
    lines.append("    classDef entry fill:#3b82f6,stroke:#2563eb,color:#fff")
    return "\n".join(lines)

def compute_impact(graph, func_name):
    target_nodes = [nid for nid, d in graph.G.nodes(data=True)
                    if d.get("type") == FUNCTION and d.get("name") == func_name]
    if not target_nodes: return None
    direct_callers = []
    for nid in target_nodes:
        for pred in graph.G.predecessors(nid):
            if graph.G[pred][nid].get("relation") == CALLS:
                pd = graph.G.nodes[pred]
                direct_callers.append({"name": pd.get("name", pred), "file": pd.get("file","").replace("\\","/")})
    all_callers = {}; queue = deque([(nid, 0) for nid in target_nodes]); visited = set(target_nodes)
    while queue:
        node, d = queue.popleft()
        for pred in graph.G.predecessors(node):
            if pred in visited: continue
            if graph.G[pred][node].get("relation") == CALLS:
                visited.add(pred); pd = graph.G.nodes[pred]
                if pd.get("type") == FUNCTION:
                    all_callers[pred] = {"name": pd.get("name",pred), "file": pd.get("file","").replace("\\","/"), "depth": d+1}
                    if d+1 < 4: queue.append((pred, d+1))
    target_files = set(graph.G.nodes[nid].get("file","") for nid in target_nodes if graph.G.nodes[nid].get("file"))
    caller_files = {v["file"] for v in all_callers.values() if v["file"]}
    dependent_files = set()
    for tf in target_files:
        fid = graph._file_id(tf)
        for pred in graph.G.predecessors(fid):
            pd = graph.G.nodes.get(pred, {})
            if pd.get("type") == FILE: dependent_files.add(pd.get("name","").replace("\\","/"))
    mermaid_lines = ["flowchart BT"]; node_map = {}; counter = 0
    def get_mid(nid):
        nonlocal counter
        if nid not in node_map: node_map[nid] = f"I{counter}"; counter += 1
        return node_map[nid]
    for nid in target_nodes:
        d = graph.G.nodes[nid]; label = (f"{d.get('name',nid)}\\n{d.get('file','').replace(chr(92),'/')}" if d.get('file') else d.get('name',nid))
        mermaid_lines.append(f'    {get_mid(nid)}(["{label}"]):::target')
    for nid, info in all_callers.items():
        label = f"{info['name']}\\n{info['file']}" if info["file"] else info["name"]
        mermaid_lines.append(f'    {get_mid(nid)}["{label}"]')
    added = set()
    for nid in target_nodes:
        for pred in graph.G.predecessors(nid):
            if pred in all_callers and (pred,nid) not in added:
                mermaid_lines.append(f"    {get_mid(pred)} -->|calls| {get_mid(nid)}"); added.add((pred,nid))
    for nid in all_callers:
        for pred in graph.G.predecessors(nid):
            if pred in all_callers and (pred,nid) not in added and graph.G[pred][nid].get("relation")==CALLS:
                mermaid_lines.append(f"    {get_mid(pred)} -->|calls| {get_mid(nid)}"); added.add((pred,nid))
    mermaid_lines.append("    classDef target fill:#ff2d55,stroke:#cc0033,color:#fff")
    mermaid = "\n".join(mermaid_lines) if all_callers else None
    return {
        "target_function": func_name,
        "target_files": sorted(f.replace("\\","/") for f in target_files),
        "direct_callers": direct_callers,
        "all_callers": sorted(all_callers.values(), key=lambda x: x["depth"]),
        "caller_files": sorted(caller_files),
        "dependent_files": sorted(dependent_files),
        "total_impacted_functions": len(all_callers),
        "total_impacted_files": len(caller_files | dependent_files),
        "mermaid": mermaid,
    }

# ── Models ─────────────────────────────────────────────────────────────────────

class ParseRequest(BaseModel):
    source: str
    path: str

class RCARequest(BaseModel):
    bug_description: str
    entry_point: Optional[str] = None
    depth: int = 3

class FlowRequest(BaseModel):
    func_name: Optional[str] = None
    file_name: Optional[str] = None

class ImpactRequest(BaseModel):
    func_name: str

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def status():
    return {"ready": STATE["graph"] is not None, "repo_path": STATE["repo_path"],
            "graph_stats": STATE["graph_stats"], "api_key_set": bool(os.getenv("GEMINI_API_KEY"))}

@app.post("/api/parse")
def parse_repo(req: ParseRequest):
    resolved_path = None
    if req.source == "github":
        url = req.path.strip().rstrip("/"); clone_url = url if url.endswith(".git") else url+".git"
        repo_name = url.split("/")[-1].replace(".git","")
        clone_base = os.path.join(tempfile.gettempdir(), "codegraph_repos"); os.makedirs(clone_base, exist_ok=True)
        clone_dir = os.path.join(clone_base, repo_name)
        if os.path.isdir(clone_dir): shutil.rmtree(clone_dir, onerror=_rm_readonly)
        result = subprocess.run(["git","clone","--depth","1",clone_url,clone_dir], capture_output=True, text=True, timeout=120)
        if result.returncode != 0: raise HTTPException(400, f"Git clone failed: {result.stderr.strip()}")
        resolved_path = clone_dir
    else:
        resolved_path = str(Path(req.path.strip()).resolve())
        if not Path(resolved_path).is_dir(): raise HTTPException(400, f"Directory not found: {resolved_path}")
    try:
        parsed = parse_repository(resolved_path); cg = CodeGraph(); cg.build(parsed)
        STATE.update({"parser_output": parsed, "graph": cg, "graph_stats": cg.stats(), "repo_path": resolved_path})
    except Exception as e: raise HTTPException(500, str(e))
    return {"files_parsed": len(parsed), "graph_stats": STATE["graph_stats"], "repo_path": resolved_path}

@app.post("/api/rca")
def run_rca(req: RCARequest):
    if not STATE["graph"]: raise HTTPException(400, "No graph loaded.")
    if not os.getenv("GEMINI_API_KEY"): raise HTTPException(400, "GEMINI_API_KEY not set.")
    try:
        agent = RCAAgent(STATE["graph"], STATE["repo_path"], STATE["parser_output"])
        ep = req.entry_point.strip() if req.entry_point and req.entry_point.strip() else None
        result = agent.analyze(req.bug_description, ep, req.depth)
        ep_func = result.get("entry_point", {}); ep_name = ep_func.get("function") if isinstance(ep_func, dict) else None
        if ep_name: result["mermaid_flow"] = generate_mermaid_flow(STATE["graph"], ep_name)
        return result
    except Exception as e: raise HTTPException(500, str(e))

@app.post("/api/flow")
def get_flow(req: FlowRequest):
    if not STATE["graph"]: raise HTTPException(400, "No graph loaded.")
    graph = STATE["graph"]; result = {}
    if req.func_name:
        result["func_mermaid"] = generate_mermaid_flow(graph, req.func_name)
        entry_nodes = [nid for nid, d in graph.G.nodes(data=True)
                       if d.get("type") == FUNCTION and d.get("name") == req.func_name]
        trace = []
        if entry_nodes:
            visited = set(); queue = deque([(nid, 0) for nid in entry_nodes])
            while queue:
                node, d = queue.popleft()
                if node in visited or d > 4: continue
                visited.add(node); data = graph.G.nodes[node]
                trace.append({"depth": d, "name": data.get("name",node), "file": data.get("file","").replace("\\","/")})
                for succ in graph.G.successors(node):
                    if graph.G[node][succ].get("relation") == CALLS and succ not in visited: queue.append((succ,d+1))
        result["trace"] = trace
    if req.file_name: result["file_mermaid"] = generate_file_dep_mermaid(graph, req.file_name)
    return result

@app.post("/api/impact")
def get_impact(req: ImpactRequest):
    if not STATE["graph"]: raise HTTPException(400, "No graph loaded.")
    impact = compute_impact(STATE["graph"], req.func_name)
    if not impact: raise HTTPException(404, f"Function '{req.func_name}' not found.")
    return impact

@app.get("/api/graph/functions")
def list_functions():
    if not STATE["graph"]: return {"functions": []}
    return {"functions": sorted(set(d["name"] for _, d in STATE["graph"].G.nodes(data=True)
                                    if d.get("type") == FUNCTION and not d["name"].startswith("__")))}

@app.get("/api/graph/files")
def list_files():
    if not STATE["graph"]: return {"files": []}
    return {"files": sorted(d["name"] for _, d in STATE["graph"].G.nodes(data=True) if d.get("type") == FILE)}

@app.get("/api/graph/lookup")
def lookup_function(name: str):
    if not STATE["graph"]: raise HTTPException(400, "No graph loaded.")
    return {"name": name, "callers": STATE["graph"].get_callers(name), "callees": STATE["graph"].get_callees(name)}

@app.get("/api/graph/file-deps")
def file_deps(name: str):
    if not STATE["graph"]: raise HTTPException(400, "No graph loaded.")
    graph = STATE["graph"]; fid = graph._file_id(name)
    return {
        "name": name,
        "depends_on": [graph.G.nodes[s]["name"] for s in graph.G.successors(fid) if graph.G.nodes.get(s,{}).get("type")==FILE],
        "depended_by": [graph.G.nodes[p]["name"] for p in graph.G.predecessors(fid) if graph.G.nodes.get(p,{}).get("type")==FILE],
        "functions": [d["name"] for _, d in graph.G.nodes(data=True) if d.get("type")==FUNCTION and d.get("file")==name],
    }

@app.get("/api/graph/d3data")
def get_d3_data():
    """Full graph as D3-compatible nodes+links for force visualization."""
    if not STATE["graph"]: raise HTTPException(400, "No graph loaded.")
    graph = STATE["graph"]
    allowed = {FILE, FUNCTION, CLASS}
    nodes = [{"id": nid, "name": d.get("name",nid), "type": d.get("type"), "file": d.get("file","")}
             for nid, d in graph.G.nodes(data=True) if d.get("type") in allowed]
    node_ids = {n["id"] for n in nodes}
    links = []; seen = set()
    for src, dst, data in graph.G.edges(data=True):
        if src not in node_ids or dst not in node_ids: continue
        rel = data.get("relation",""); key = (src, dst, rel)
        if key in seen: continue
        seen.add(key); links.append({"source": src, "target": dst, "relation": rel})
    return {"nodes": nodes, "links": links}

# ── Chat ──────────────────────────────────────────────────────────────────────

CHAT_SYSTEM_PROMPT = """\
You are an expert code assistant analyzing a codebase that has been parsed into a dependency graph.
You have access to the code structure, function call chains, file dependencies, and actual source code snippets.

When answering questions:
- Reference specific files, functions, and line numbers when relevant.
- Explain code behavior by tracing through the call graph.
- If you're unsure about something, say so rather than guessing.
- Use markdown formatting: headings, code blocks, bold, lists.
- Keep answers focused and practical.

Repository file structure:
{file_tree}

Graph statistics: {stats}
"""

def _build_chat_context(message: str, graph, parser_output: list[dict]) -> str:
    from retrieval.context_engine import ContextEngine
    message_lower = message.lower()
    all_funcs = set()
    for entry in parser_output:
        for f in entry.get("functions", []):
            all_funcs.add(f)
    all_files = set()
    for entry in parser_output:
        all_files.add(entry["file"].replace("\\", "/"))
    mentioned_funcs = [f for f in all_funcs
                       if len(f) > 2 and not f.startswith("__")
                       and (f.lower() in message_lower or f in message)]
    mentioned_files = [f for f in all_files
                       if f.split("/")[-1].lower() in message_lower
                       or f.split("/")[-1].rsplit(".", 1)[0].lower() in message_lower]
    if not mentioned_funcs and not mentioned_files:
        return ""
    engine = ContextEngine(graph, STATE["repo_path"], parser_output)
    parts = []
    for func in mentioned_funcs[:3]:
        r = engine.retrieve(func, depth=2)
        if "error" not in r:
            parts.append(engine.format_for_llm(r))
    for file in mentioned_files[:2]:
        r = engine.retrieve(file, depth=1)
        if "error" not in r:
            parts.append(engine.format_for_llm(r))
    return "\n\n".join(parts)

@app.post("/api/chat")
def chat(req: ChatRequest):
    if not STATE["graph"]:
        raise HTTPException(400, "No graph loaded. Parse a repository first.")
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(400, "GEMINI_API_KEY not set.")
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-2.0-flash")
        file_tree = sorted(e["file"].replace("\\", "/") for e in STATE["parser_output"])
        system = CHAT_SYSTEM_PROMPT.format(
            file_tree="\n".join(file_tree),
            stats=json.dumps(STATE["graph_stats"]),
        )
        code_context = _build_chat_context(req.message, STATE["graph"], STATE["parser_output"])
        first_msg = system + "\n\n"
        if code_context:
            first_msg += "=== RELEVANT CODE CONTEXT ===\n" + code_context + "\n\n"
        contents = []
        if req.history:
            contents.append({"role": "user", "parts": [{"text": first_msg + req.history[0].content}]})
            for msg in req.history[1:]:
                role = "user" if msg.role == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg.content}]})
            contents.append({"role": "user", "parts": [{"text": req.message}]})
        else:
            contents.append({"role": "user", "parts": [{"text": first_msg + req.message}]})
        response = model.generate_content(
            contents,
            generation_config=genai.types.GenerationConfig(temperature=0.3),
        )
        return {"response": response.text}
    except Exception as e:
        raise HTTPException(500, str(e))
