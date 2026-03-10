import sys
import os
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(Path(_PROJECT_ROOT) / ".env")

import streamlit as st
import json

from parser.repo_parser import parse_repository
from graph.codegraph import CodeGraph, FILE, FUNCTION, CLASS, CALLS, DEPENDS_ON
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


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔍 CodeGraph RCA")
    st.caption("AI-Powered Root Cause Analysis")
    st.divider()

    st.subheader("Repository")
    repo_path = st.text_input(
        "Repository path",
        value="sample_test_app",
        help="Relative or absolute path to the Python repo to analyze.",
    )
    parse_clicked = st.button("⚙️ Parse & Build Graph", use_container_width=True, type="primary")

    if parse_clicked:
        resolved = str(Path(repo_path).resolve())
        if not Path(resolved).is_dir():
            st.error(f"Directory not found: {resolved}")
        else:
            with st.status("Building code graph...", expanded=True) as status:
                status.write("📂 Parsing repository...")
                parsed = parse_repository(resolved)
                st.session_state.parser_output = parsed
                st.session_state.repo_path = resolved

                status.write(f"✅ Parsed {len(parsed)} files.")
                status.write("🔗 Building dependency graph...")

                cg = CodeGraph()
                cg.build(parsed)
                st.session_state.graph = cg
                st.session_state.graph_stats = cg.stats()

                status.update(label="✅ Graph ready!", state="complete", expanded=False)

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

tab_rca, tab_explorer = st.tabs(["🔬 RCA Analysis", "🗺️ Graph Explorer"])

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
# Tab 2 — Graph Explorer
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
