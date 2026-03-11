import sys
import os
import stat
import subprocess
import tempfile
import shutil
from pathlib import Path


def _rm_readonly(func, path, _excinfo):
    """Handle read-only files on Windows when removing .git directories."""
    os.chmod(path, stat.S_IWRITE)
    func(path)

# Ensure project root is on sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(Path(_PROJECT_ROOT) / ".env")

import streamlit as st
import json

from parser.repo_parser import parse_repository
from collections import deque
from graph.codegraph import CodeGraph, FILE, FUNCTION, CLASS, CALLS, DEPENDS_ON, CONTAINS
from rca.rca_agent import RCAAgent


# ── Page Config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="CodeGraph RCA",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session State Defaults ────────────────────────────────────────────────────

for key, default in {
    "parser_output": None,
    "graph": None,
    "graph_stats": None,
    "repo_path": "",
    "rca_result": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ── Logic Flow Generator ──────────────────────────────────────────────────

def generate_mermaid_flow(graph: CodeGraph, entry_func: str, max_depth: int = 4) -> str | None:
    """
    Generate a Mermaid flowchart by tracing CALLS edges from an entry function.
    Returns a Mermaid string or None if no call chain is found.
    """
    # Find entry node(s)
    entry_nodes = [
        nid for nid, data in graph.G.nodes(data=True)
        if data.get("type") == FUNCTION and data.get("name") == entry_func
    ]
    if not entry_nodes:
        return None

    # BFS following CALLS edges
    visited = set()
    edges = []
    queue = deque([(nid, 0) for nid in entry_nodes])

    while queue:
        node, depth = queue.popleft()
        if node in visited or depth > max_depth:
            continue
        visited.add(node)

        for succ in graph.G.successors(node):
            if graph.G[node][succ].get("relation") == CALLS:
                edges.append((node, succ))
                if succ not in visited:
                    queue.append((succ, depth + 1))

    if not edges:
        return None

    # Build Mermaid flowchart
    lines = ["flowchart TD"]
    node_ids = {}
    counter = 0

    def get_id(nid):
        nonlocal counter
        if nid not in node_ids:
            node_ids[nid] = f"N{counter}"
            counter += 1
        return node_ids[nid]

    # Define nodes with labels
    defined = set()
    for src, dst in edges:
        for nid in (src, dst):
            if nid not in defined:
                data = graph.G.nodes[nid]
                name = data.get("name", nid)
                file = data.get("file", "").replace("\\", "/")
                mid = get_id(nid)
                label = f"{name}\\n{file}" if file else name
                if nid in entry_nodes:
                    lines.append(f'    {mid}(["{label}"]):::entry')
                else:
                    lines.append(f'    {mid}["{label}"]')
                defined.add(nid)

    # Add edges
    for src, dst in edges:
        lines.append(f"    {get_id(src)} --> {get_id(dst)}")

    # Style the entry node
    lines.append("    classDef entry fill:#2563eb,stroke:#1d4ed8,color:#fff")

    return "\n".join(lines)


def generate_file_dep_mermaid(graph: CodeGraph, entry_file: str) -> str | None:
    """
    Generate a Mermaid flowchart showing file-level DEPENDS_ON relationships.
    """
    fid = graph._file_id(entry_file)
    if fid not in graph.G:
        return None

    visited = set()
    edges = []
    queue = deque([(fid, 0)])

    while queue:
        node, depth = queue.popleft()
        if node in visited or depth > 3:
            continue
        visited.add(node)
        for succ in graph.G.successors(node):
            if graph.G[node][succ].get("relation") == DEPENDS_ON:
                succ_data = graph.G.nodes.get(succ, {})
                if succ_data.get("type") == FILE:
                    edges.append((node, succ))
                    if succ not in visited:
                        queue.append((succ, depth + 1))

    if not edges:
        return None

    lines = ["flowchart LR"]
    node_ids = {}
    counter = 0

    def get_id(nid):
        nonlocal counter
        if nid not in node_ids:
            node_ids[nid] = f"F{counter}"
            counter += 1
        return node_ids[nid]

    defined = set()
    for src, dst in edges:
        for nid in (src, dst):
            if nid not in defined:
                name = graph.G.nodes[nid].get("name", nid).replace("\\", "/")
                mid = get_id(nid)
                if nid == fid:
                    lines.append(f'    {mid}(["{name}"]):::entry')
                else:
                    lines.append(f'    {mid}["{name}"]')
                defined.add(nid)

    for src, dst in edges:
        lines.append(f"    {get_id(src)} -->|imports| {get_id(dst)}")

    lines.append("    classDef entry fill:#2563eb,stroke:#1d4ed8,color:#fff")

    return "\n".join(lines)


# ── Impact Analysis Engine ────────────────────────────────────────────────

def compute_impact(graph: CodeGraph, func_name: str) -> dict:
    """
    Compute the full blast radius when a function is modified.
    Traces BACKWARD through CALLS edges (who calls this?)
    and DEPENDS_ON edges at the file level.
    """
    # Find all node IDs for this function name
    target_nodes = [
        nid for nid, data in graph.G.nodes(data=True)
        if data.get("type") == FUNCTION and data.get("name") == func_name
    ]
    if not target_nodes:
        return None

    # --- Direct callers (depth 1) ---
    direct_callers = []
    for nid in target_nodes:
        for pred in graph.G.predecessors(nid):
            edge = graph.G[pred][nid]
            if edge.get("relation") == CALLS:
                pred_data = graph.G.nodes[pred]
                direct_callers.append({
                    "name": pred_data.get("name", pred),
                    "file": pred_data.get("file", "").replace("\\", "/"),
                })

    # --- Transitive callers (BFS backward through CALLS, up to depth 4) ---
    all_callers = {}  # nid -> {name, file, depth}
    queue = deque([(nid, 0) for nid in target_nodes])
    visited = set(target_nodes)

    while queue:
        node, d = queue.popleft()
        for pred in graph.G.predecessors(node):
            if pred in visited:
                continue
            edge = graph.G[pred][node]
            if edge.get("relation") == CALLS:
                visited.add(pred)
                pred_data = graph.G.nodes[pred]
                if pred_data.get("type") == FUNCTION:
                    all_callers[pred] = {
                        "name": pred_data.get("name", pred),
                        "file": pred_data.get("file", "").replace("\\", "/"),
                        "depth": d + 1,
                    }
                    if d + 1 < 4:
                        queue.append((pred, d + 1))

    # --- Impacted files ---
    target_files = set()
    for nid in target_nodes:
        f = graph.G.nodes[nid].get("file", "")
        if f:
            target_files.add(f)

    caller_files = set()
    for info in all_callers.values():
        if info["file"]:
            caller_files.add(info["file"])

    # Files that DEPENDS_ON the target file (reverse file deps)
    dependent_files = set()
    for tf in target_files:
        fid = graph._file_id(tf)
        for pred in graph.G.predecessors(fid):
            pred_data = graph.G.nodes.get(pred, {})
            if pred_data.get("type") == FILE:
                dependent_files.add(pred_data.get("name", "").replace("\\", "/"))

    # --- Build reverse Mermaid (impact graph) ---
    mermaid_lines = ["flowchart BT"]
    node_map = {}
    counter = 0

    def get_mid(nid):
        nonlocal counter
        if nid not in node_map:
            node_map[nid] = f"I{counter}"
            counter += 1
        return node_map[nid]

    # Add target nodes
    for nid in target_nodes:
        data = graph.G.nodes[nid]
        name = data.get("name", nid)
        file = data.get("file", "").replace("\\", "/")
        label = f"{name}\\n{file}" if file else name
        mid = get_mid(nid)
        mermaid_lines.append(f'    {mid}(["{label}"]):::target')

    # Add caller nodes and edges
    for nid, info in all_callers.items():
        label = f"{info['name']}\\n{info['file']}" if info["file"] else info["name"]
        mid = get_mid(nid)
        mermaid_lines.append(f'    {mid}["{label}"]')

    # Add edges (caller → target direction, bottom-to-top)
    added_edges = set()
    for nid in target_nodes:
        for pred in graph.G.predecessors(nid):
            if pred in all_callers and (pred, nid) not in added_edges:
                mermaid_lines.append(f"    {get_mid(pred)} -->|calls| {get_mid(nid)}")
                added_edges.add((pred, nid))
    # Transitive edges between callers
    for nid in all_callers:
        for pred in graph.G.predecessors(nid):
            if pred in all_callers and (pred, nid) not in added_edges:
                edge = graph.G[pred][nid]
                if edge.get("relation") == CALLS:
                    mermaid_lines.append(f"    {get_mid(pred)} -->|calls| {get_mid(nid)}")
                    added_edges.add((pred, nid))

    mermaid_lines.append("    classDef target fill:#e74c3c,stroke:#c0392b,color:#fff")

    mermaid = "\n".join(mermaid_lines) if all_callers else None

    return {
        "target_function": func_name,
        "target_files": sorted(f.replace("\\", "/") for f in target_files),
        "direct_callers": direct_callers,
        "all_callers": sorted(all_callers.values(), key=lambda x: x["depth"]),
        "caller_files": sorted(caller_files),
        "dependent_files": sorted(dependent_files),
        "total_impacted_functions": len(all_callers),
        "total_impacted_files": len(caller_files | dependent_files),
        "mermaid": mermaid,
    }


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔍 CodeGraph RCA")
    st.caption("AI-Powered Root Cause Analysis")
    st.divider()

    st.subheader("Repository")
    source_mode = st.radio("Source", ["GitHub URL", "Local Path"], horizontal=True)

    if source_mode == "GitHub URL":
        repo_input = st.text_input(
            "GitHub repository URL",
            placeholder="https://github.com/user/repo",
            help="Public GitHub repo URL. The repo will be cloned and analyzed.",
        )
    else:
        repo_input = st.text_input(
            "Local repository path",
            value="sample_test_app",
            help="Relative or absolute path to a Python repo on your machine.",
        )

    parse_clicked = st.button("⚙️ Parse & Build Graph", use_container_width=True, type="primary")

    if parse_clicked:
        if not repo_input.strip():
            st.error("Please enter a repository URL or path.")
        else:
            resolved_path = None

            if source_mode == "GitHub URL":
                url = repo_input.strip().rstrip("/")
                clone_url = url if url.endswith(".git") else url + ".git"
                repo_name = url.split("/")[-1].replace(".git", "")

                with st.status("Cloning & building graph...", expanded=True) as status:
                    try:
                        clone_base = os.path.join(tempfile.gettempdir(), "codegraph_repos")
                        os.makedirs(clone_base, exist_ok=True)
                        clone_dir = os.path.join(clone_base, repo_name)
                        if os.path.isdir(clone_dir):
                            shutil.rmtree(clone_dir, onerror=_rm_readonly)

                        status.write(f"📥 Cloning `{repo_name}`...")
                        result = subprocess.run(
                            ["git", "clone", "--depth", "1", clone_url, clone_dir],
                            capture_output=True, text=True, timeout=120,
                        )
                        if result.returncode != 0:
                            status.update(label="🚨 Clone failed", state="error")
                            st.error(f"Git clone failed:\n{result.stderr.strip()}")
                            st.stop()

                        resolved_path = clone_dir
                        status.write("✅ Clone complete.")
                    except subprocess.TimeoutExpired:
                        status.update(label="🚨 Clone timed out", state="error")
                        st.error("Git clone timed out after 120s. Check the URL or your network.")
                        st.stop()
                    except Exception as e:
                        status.update(label="🚨 Failed", state="error")
                        st.error(f"Error: {e}")
                        st.stop()
            else:
                resolved_path = str(Path(repo_input.strip()).resolve())
                if not Path(resolved_path).is_dir():
                    st.error(f"Directory not found: {resolved_path}")
                    st.stop()

            if resolved_path:
                with st.status("Building code graph...", expanded=True) as status:
                    try:
                        status.write("📂 Parsing repository...")
                        parsed = parse_repository(resolved_path)
                        st.session_state.parser_output = parsed
                        st.session_state.repo_path = resolved_path

                        status.write(f"✅ Parsed {len(parsed)} files.")
                        status.write("🔗 Building dependency graph...")

                        cg = CodeGraph()
                        cg.build(parsed)
                        st.session_state.graph = cg
                        st.session_state.graph_stats = cg.stats()

                        status.update(label="✅ Graph ready!", state="complete", expanded=False)
                    except Exception as e:
                        status.update(label="🚨 Build failed", state="error")
                        st.error(f"Error: {e}")
                        st.stop()

                st.session_state.rca_result = None
                st.rerun()

    # Show graph stats if available
    if st.session_state.graph_stats:
        st.divider()
        st.subheader("Graph Stats")
        stats = st.session_state.graph_stats
        c1, c2 = st.columns(2)
        c1.metric("Nodes", stats["nodes"])
        c2.metric("Edges", stats["edges"])

        st.markdown("**Node types**")
        for ntype, count in stats["node_types"].items():
            st.text(f"  {ntype}: {count}")

        st.markdown("**Edge types**")
        for etype, count in stats["edge_types"].items():
            st.text(f"  {etype}: {count}")

    st.divider()
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        st.success("Gemini API key loaded", icon="🔑")
    else:
        st.warning("GEMINI_API_KEY not set in .env")


# ── Main Area ─────────────────────────────────────────────────────────────────

tab_rca, tab_flow, tab_impact, tab_explorer = st.tabs(["🔬 RCA Analysis", "📊 Logic Flow", "💥 Impact Analysis", "🗺️ Graph Explorer"])

# ═══════════════════════════════════════════════════════════════════════════════
# Tab 1 — RCA Analysis
# ═══════════════════════════════════════════════════════════════════════════════

with tab_rca:
    if not st.session_state.graph:
        st.info("👈 Parse a repository first using the sidebar.")
    else:
        st.subheader("Describe the bug")
        bug_desc = st.text_area(
            "Bug description",
            height=120,
            placeholder="e.g., The workflow engine crashes when a node has no agent path configured",
        )
        entry_point = st.text_input(
            "Entry point (optional)",
            placeholder="e.g., execute, run, WorkflowEngine — leave blank for auto-detect",
        )

        run_clicked = st.button("🚀 Run Root Cause Analysis", use_container_width=True, type="primary")

        if run_clicked:
            if not bug_desc.strip():
                st.warning("Please describe the bug.")
            elif not os.getenv("GEMINI_API_KEY"):
                st.error("GEMINI_API_KEY is not set. Add it to your .env file.")
            else:
                with st.status("Running RCA pipeline...", expanded=True) as status:
                    try:
                        status.write("🤖 Initializing RCA Agent...")
                        agent = RCAAgent(
                            st.session_state.graph,
                            st.session_state.repo_path,
                            st.session_state.parser_output,
                        )

                        ep = entry_point.strip() if entry_point.strip() else None
                        if not ep:
                            status.write("🔎 Auto-detecting entry point...")
                        else:
                            status.write(f"📍 Entry point: `{ep}`")

                        status.write("📦 Retrieving code context from graph...")
                        status.write("🧠 Sending to Gemini for analysis...")

                        result = agent.analyze(bug_desc, ep)
                        st.session_state.rca_result = result

                        status.update(label="✅ Analysis complete!", state="complete", expanded=False)
                    except Exception as e:
                        status.update(label="🚨 Analysis failed", state="error")
                        st.error(f"Error: {e}")

        # ── Display RCA Report ────────────────────────────────────────────────
        if st.session_state.rca_result:
            rca = st.session_state.rca_result

            if "parse_error" in rca:
                st.warning("Could not parse structured JSON from Gemini.")
                st.code(rca.get("raw_response", ""), language="text")
            else:
                st.divider()
                st.header("📋 Root Cause Analysis Report")

                # Confidence badge
                conf = rca.get("confidence", "N/A")
                conf_color = {"High": "🟢", "Medium": "🟡", "Low": "🔴"}.get(conf, "⚪")
                st.markdown(f"**Confidence:** {conf_color} {conf}")

                # Feature Intent
                with st.expander("💡 Feature Intent", expanded=True):
                    st.write(rca.get("feature_intent", "N/A"))

                # Entry Point
                with st.expander("📍 Entry Point", expanded=True):
                    ep_data = rca.get("entry_point", {})
                    if isinstance(ep_data, dict):
                        st.code(f"File     : {ep_data.get('file', 'N/A')}\nFunction : {ep_data.get('function', 'N/A')}", language="text")
                    else:
                        st.write(str(ep_data))

                # Execution Flow
                with st.expander("🔄 Execution Flow", expanded=True):
                    flow = rca.get("execution_flow", [])
                    for i, step in enumerate(flow, 1):
                        st.markdown(f"**{i}.** {step}")

                    # Graph-based flow diagram
                    ep_func = rca.get("entry_point", {})
                    ep_name = ep_func.get("function") if isinstance(ep_func, dict) else None
                    if ep_name:
                        rca_mermaid = generate_mermaid_flow(st.session_state.graph, ep_name)
                        if rca_mermaid:
                            st.markdown("---")
                            st.markdown("**Call Graph:**")
                            st.markdown(f"```mermaid\n{rca_mermaid}\n```")

                # Dependencies
                with st.expander("📦 Dependencies"):
                    deps = rca.get("dependencies", {})
                    for key, vals in deps.items():
                        if vals:
                            st.markdown(f"**{key}:**")
                            for v in vals:
                                st.markdown(f"- `{v}`")

                # Edge Cases
                with st.expander("⚠️ Edge Cases"):
                    for ec in rca.get("edge_cases", []):
                        st.markdown(f"- {ec}")

                # Failure Point
                with st.expander("❌ Failure Point", expanded=True):
                    fp = rca.get("failure_point", {})
                    if isinstance(fp, dict):
                        st.code(
                            f"File      : {fp.get('file', 'N/A')}\n"
                            f"Function  : {fp.get('function', 'N/A')}\n"
                            f"Condition : {fp.get('condition', 'N/A')}",
                            language="text",
                        )
                    else:
                        st.write(str(fp))

                # Root Cause
                with st.expander("🔎 Root Cause", expanded=True):
                    rc = rca.get("root_cause", {})
                    if isinstance(rc, dict):
                        st.markdown(f"**Why:** {rc.get('why', 'N/A')}")
                        st.markdown(f"**When:** {rc.get('when', 'N/A')}")
                        st.markdown(f"**Propagation:** {rc.get('how_it_propagates', 'N/A')}")
                    else:
                        st.write(str(rc))

                # Impact Analysis
                with st.expander("💥 Impact Analysis"):
                    ia = rca.get("impact_analysis", {})
                    for key, vals in ia.items():
                        if vals:
                            st.markdown(f"**{key}:**")
                            for v in vals:
                                st.markdown(f"- `{v}`")

                # Suggested Fix
                with st.expander("🛠️ Suggested Fix", expanded=True):
                    sf = rca.get("suggested_fix", {})
                    st.markdown(sf.get("description", "N/A"))
                    for change in sf.get("code_changes", []):
                        st.markdown(f"**File:** `{change.get('file', '?')}`")
                        col_orig, col_fixed = st.columns(2)
                        with col_orig:
                            st.markdown("**Original:**")
                            st.code(change.get("original", ""), language="python")
                        with col_fixed:
                            st.markdown("**Fixed:**")
                            st.code(change.get("fixed", ""), language="python")


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 2 — Logic Flow
# ═══════════════════════════════════════════════════════════════════════════════

with tab_flow:
    if not st.session_state.graph:
        st.info("👈 Parse a repository first using the sidebar.")
    else:
        graph_flow: CodeGraph = st.session_state.graph

        st.subheader("Execution Flow Generator")
        st.caption("Select an entry point to visualize the call chain across functions and files.")

        flow_col1, flow_col2 = st.columns(2)

        with flow_col1:
            # Function-level call flow
            st.markdown("#### Function Call Flow")
            all_flow_funcs = sorted(set(
                data["name"]
                for _, data in graph_flow.G.nodes(data=True)
                if data.get("type") == FUNCTION and not data["name"].startswith("__")
            ))

            flow_entry = st.selectbox(
                "Entry point function",
                [""] + all_flow_funcs,
                key="flow_entry_func",
            )

            if flow_entry:
                mermaid = generate_mermaid_flow(graph_flow, flow_entry)
                if mermaid:
                    st.markdown(f"```mermaid\n{mermaid}\n```")
                else:
                    st.caption(f"`{flow_entry}` does not call any other tracked functions.")

        with flow_col2:
            # File-level dependency flow
            st.markdown("#### File Dependency Flow")
            all_flow_files = sorted(
                data["name"]
                for _, data in graph_flow.G.nodes(data=True)
                if data.get("type") == FILE
            )

            flow_file = st.selectbox(
                "Entry point file",
                [""] + all_flow_files,
                key="flow_entry_file",
            )

            if flow_file:
                file_mermaid = generate_file_dep_mermaid(graph_flow, flow_file)
                if file_mermaid:
                    st.markdown(f"```mermaid\n{file_mermaid}\n```")
                else:
                    st.caption(f"`{flow_file}` has no tracked file dependencies.")

        # ── Full execution trace ─────────────────────────────────────────────
        if flow_entry:
            st.divider()
            st.markdown("#### Step-by-Step Execution Trace")

            # BFS to build ordered call list
            entry_nodes = [
                nid for nid, data in graph_flow.G.nodes(data=True)
                if data.get("type") == FUNCTION and data.get("name") == flow_entry
            ]
            if entry_nodes:
                visited = set()
                trace_steps = []
                queue = deque([(nid, 0) for nid in entry_nodes])

                while queue:
                    node, d = queue.popleft()
                    if node in visited or d > 4:
                        continue
                    visited.add(node)
                    data = graph_flow.G.nodes[node]
                    name = data.get("name", node)
                    file = data.get("file", "").replace("\\", "/")
                    trace_steps.append((d, name, file))

                    for succ in graph_flow.G.successors(node):
                        if graph_flow.G[node][succ].get("relation") == CALLS:
                            if succ not in visited:
                                queue.append((succ, d + 1))

                for depth, name, file in trace_steps:
                    indent = "→ " * depth
                    st.markdown(f"`{indent}`**{name}** — `{file}`" if file else f"`{indent}`**{name}**")


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 3 — Impact Analysis
# ═══════════════════════════════════════════════════════════════════════════════

with tab_impact:
    if not st.session_state.graph:
        st.info("👈 Parse a repository first using the sidebar.")
    else:
        graph_ia: CodeGraph = st.session_state.graph

        st.subheader("Impact Analysis Engine")
        st.caption("Select a function you plan to modify — see everything that could break.")

        all_ia_funcs = sorted(set(
            data["name"]
            for _, data in graph_ia.G.nodes(data=True)
            if data.get("type") == FUNCTION and not data["name"].startswith("__")
        ))

        ia_func = st.selectbox(
            "Function to modify",
            [""] + all_ia_funcs,
            key="ia_func_select",
        )

        if ia_func:
            impact = compute_impact(graph_ia, ia_func)

            if not impact:
                st.warning(f"Function `{ia_func}` not found in the graph.")
            else:
                # ── Blast radius metrics ─────────────────────────────────
                st.divider()
                st.markdown("#### Blast Radius")
                ia_m1, ia_m2, ia_m3, ia_m4 = st.columns(4)
                ia_m1.metric("Direct Callers", len(impact["direct_callers"]))
                ia_m2.metric("Total Impacted Functions", impact["total_impacted_functions"])
                ia_m3.metric("Impacted Files", impact["total_impacted_files"])
                ia_m4.metric("Target Files", len(impact["target_files"]))

                # ── Impact graph ─────────────────────────────────────────
                if impact["mermaid"]:
                    st.divider()
                    st.markdown("#### Impact Graph")
                    st.caption("Red = modified function. Arrows show what calls it (bottom → top).")
                    st.markdown(f"```mermaid\n{impact['mermaid']}\n```")

                # ── Detailed breakdown ───────────────────────────────────
                st.divider()
                ia_col1, ia_col2 = st.columns(2)

                with ia_col1:
                    st.markdown("#### Direct Callers")
                    if impact["direct_callers"]:
                        for c in impact["direct_callers"]:
                            st.markdown(f"- **{c['name']}** — `{c['file']}`")
                    else:
                        st.caption("No direct callers — this function is a root entry point.")

                    st.markdown("#### All Transitive Callers")
                    if impact["all_callers"]:
                        for c in impact["all_callers"]:
                            depth_marker = "↑" * c["depth"]
                            st.markdown(f"- {depth_marker} **{c['name']}** — `{c['file']}`")
                    else:
                        st.caption("No transitive callers found.")

                with ia_col2:
                    st.markdown("#### Impacted Files")
                    all_impacted = sorted(set(impact["caller_files"]) | set(impact["dependent_files"]))
                    if all_impacted:
                        for f in all_impacted:
                            st.markdown(f"- `{f}`")
                    else:
                        st.caption("No other files impacted.")

                    st.markdown("#### Files That Import Target")
                    if impact["dependent_files"]:
                        for f in impact["dependent_files"]:
                            st.markdown(f"- `{f}`")
                    else:
                        st.caption("No file-level dependents.")

                # ── Refactoring guidance ─────────────────────────────────
                st.divider()
                st.markdown("#### Safe Refactoring Notes")

                n_callers = impact["total_impacted_functions"]
                n_files = impact["total_impacted_files"]

                if n_callers == 0:
                    st.success(
                        f"**Low risk.** `{ia_func}` has no callers — safe to modify freely.",
                        icon="✅",
                    )
                elif n_callers <= 3:
                    st.info(
                        f"**Moderate risk.** `{ia_func}` is called by {n_callers} function(s) "
                        f"across {n_files} file(s). Review each caller before changing the signature or return type.",
                        icon="📋",
                    )
                else:
                    st.warning(
                        f"**High risk.** `{ia_func}` is called by {n_callers} function(s) "
                        f"across {n_files} file(s). Consider:",
                        icon="⚠️",
                    )
                    st.markdown(
                        "- Add the new behavior in a **new function** and migrate callers incrementally\n"
                        "- If changing the signature, update **all callers** in the same commit\n"
                        "- Add tests for each caller listed above before modifying"
                    )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 4 — Graph Explorer
# ═══════════════════════════════════════════════════════════════════════════════

with tab_explorer:
    if not st.session_state.graph:
        st.info("👈 Parse a repository first using the sidebar.")
    else:
        graph: CodeGraph = st.session_state.graph
        stats = st.session_state.graph_stats

        st.subheader("Graph Overview")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Nodes", stats["nodes"])
        m2.metric("Total Edges", stats["edges"])
        m3.metric("Files", stats["node_types"].get("File", 0))
        m4.metric("Functions", stats["node_types"].get("Function", 0))

        st.divider()

        col_func, col_file = st.columns(2)

        # ── Function Lookup ───────────────────────────────────────────────────
        with col_func:
            st.subheader("🔍 Function Lookup")
            # Gather all function names
            all_funcs = sorted(set(
                data["name"]
                for _, data in graph.G.nodes(data=True)
                if data.get("type") == FUNCTION and not data["name"].startswith("__")
            ))

            selected_func = st.selectbox("Select a function", [""] + all_funcs)

            if selected_func:
                callers = graph.get_callers(selected_func)
                callees = graph.get_callees(selected_func)

                st.markdown(f"**Callers** (functions that call `{selected_func}`):")
                if callers:
                    for c in callers:
                        st.markdown(f"- `{c}`")
                else:
                    st.caption("None found")

                st.markdown(f"**Callees** (functions called by `{selected_func}`):")
                if callees:
                    for c in callees:
                        st.markdown(f"- `{c}`")
                else:
                    st.caption("None found")

        # ── File Dependencies ─────────────────────────────────────────────────
        with col_file:
            st.subheader("📁 File Dependencies")
            all_files = sorted(
                data["name"]
                for _, data in graph.G.nodes(data=True)
                if data.get("type") == FILE
            )

            selected_file = st.selectbox("Select a file", [""] + all_files)

            if selected_file:
                fid = graph._file_id(selected_file)

                # Outgoing: what this file depends on
                depends_on = []
                for succ in graph.G.successors(fid):
                    edge_data = graph.G[fid][succ]
                    node_data = graph.G.nodes.get(succ, {})
                    if node_data.get("type") == FILE:
                        depends_on.append(node_data["name"])

                st.markdown(f"**Depends on** (this file imports):")
                if depends_on:
                    for d in depends_on:
                        st.markdown(f"- `{d}`")
                else:
                    st.caption("No file dependencies")

                # Incoming: what depends on this file
                depended_by = []
                for pred in graph.G.predecessors(fid):
                    pred_data = graph.G.nodes.get(pred, {})
                    if pred_data.get("type") == FILE:
                        depended_by.append(pred_data["name"])

                st.markdown(f"**Depended on by** (files that import this):")
                if depended_by:
                    for d in depended_by:
                        st.markdown(f"- `{d}`")
                else:
                    st.caption("No incoming dependencies")

                # Functions in this file
                st.markdown(f"**Functions in this file:**")
                funcs_in_file = [
                    data["name"]
                    for _, data in graph.G.nodes(data=True)
                    if data.get("type") == FUNCTION and data.get("file") == selected_file
                ]
                if funcs_in_file:
                    for f in funcs_in_file:
                        st.markdown(f"- `{f}`")
                else:
                    st.caption("No functions")
