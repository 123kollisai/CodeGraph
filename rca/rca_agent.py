import json
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so sibling packages (graph, retrieval) resolve
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(Path(_PROJECT_ROOT) / ".env")

import google.generativeai as genai

from graph.codegraph import CodeGraph, FUNCTION, FILE, CALLS
from retrieval.context_engine import ContextEngine


# ── System prompt encoding the 9-step RCA process ────────────────────────────

SYSTEM_PROMPT = """\
You are an expert AI debugger performing Root Cause Analysis (RCA) on a codebase.

You will receive:
1. A bug description reported by a developer.
2. Relevant source code snippets retrieved from the dependency graph.
3. Dependency & impact data (callers, callees, dependent files).

Follow the 9-step structured debugging process below STRICTLY and produce a
complete RCA report.

─── STEP 1 — Understand Feature Intent ───
Explain what the feature is supposed to do functionally.
Focus on EXPECTED behaviour, not just implementation.

─── STEP 2 — Identify Entry Point ───
Locate where execution begins (UI event, API controller, service handler, etc.).

─── STEP 3 — Trace Execution Flow ───
Trace execution across functions and files step-by-step.
Show the call chain with file names.

─── STEP 4 — Dependency Analysis ───
List: dependent modules, called functions, external services, database interactions.

─── STEP 5 — Edge Case Detection ───
Check for: null inputs, invalid parameters, incorrect conditional logic,
missing validations, concurrency issues, improper exception handling.

─── STEP 6 — Identify Failure Point ───
Locate where system behavior diverges from expectations.
Explain: failing condition, incorrect logic, missing validation.

─── STEP 7 — Root Cause Identification ───
Explain: WHY the bug occurs, WHEN it occurs, HOW it propagates.

─── STEP 8 — Impact Analysis ───
List: impacted files, impacted functions, dependent modules, possible side effects.

─── STEP 9 — Suggest Safe Fix ───
DO NOT: suppress exceptions, hide errors, return default values to mask failures.
INSTEAD: preserve error visibility, maintain validation logic, raise meaningful exceptions.
Provide the actual corrected code snippet(s).

─── OUTPUT FORMAT ───
You MUST respond with a single valid JSON object with these exact keys:

{
  "feature_intent": "...",
  "entry_point": {"file": "...", "function": "..."},
  "execution_flow": ["step1", "step2", ...],
  "dependencies": {"modules": [...], "functions": [...], "external_services": [...], "database_interactions": [...]},
  "edge_cases": ["...", "..."],
  "failure_point": {"file": "...", "function": "...", "condition": "..."},
  "root_cause": {"why": "...", "when": "...", "how_it_propagates": "..."},
  "impact_analysis": {"impacted_files": [...], "impacted_functions": [...], "dependent_modules": [...], "side_effects": [...]},
  "suggested_fix": {"description": "...", "code_changes": [{"file": "...", "original": "...", "fixed": "..."}]},
  "confidence": "High | Medium | Low"
}
"""


class RCAAgent:
    def __init__(
        self,
        graph: CodeGraph,
        repo_path: str,
        parser_output: list[dict],
        model_name: str = "gemini-2.0-flash",
    ):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set.")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

        self.graph = graph
        self.repo_path = repo_path
        self.parser_output = parser_output
        self.context_engine = ContextEngine(graph, repo_path, parser_output)

    # ── Impact helpers ────────────────────────────────────────────────────────

    def _gather_impact_data(self, entry_point: str) -> dict:
        """Use CodeGraph query API to gather caller/callee/dependency info."""
        callers = self.graph.get_callers(entry_point)
        callees = self.graph.get_callees(entry_point)

        # Find which file contains the entry point
        entry_file = None
        for nid, data in self.graph.G.nodes(data=True):
            if data.get("name") == entry_point and data.get("type") == FUNCTION:
                entry_file = data.get("file")
                break

        dependent_files = []
        if entry_file:
            dep_nodes = self.graph.get_dependencies(entry_file, depth=2)
            for nid in dep_nodes:
                node_data = self.graph.G.nodes.get(nid, {})
                if node_data.get("type") == FILE and node_data.get("name") != entry_file:
                    dependent_files.append(node_data["name"])

        # Reverse impact: files that DEPEND ON the entry file
        impacted_by = []
        if entry_file:
            fid = self.graph._file_id(entry_file)
            for pred in self.graph.G.predecessors(fid):
                pred_data = self.graph.G.nodes.get(pred, {})
                if pred_data.get("type") == FILE:
                    impacted_by.append(pred_data["name"])

        return {
            "callers": callers,
            "callees": callees,
            "dependent_files": dependent_files,
            "impacted_by": impacted_by,
        }

    # ── Core analysis ─────────────────────────────────────────────────────────

    def analyze(self, bug_description: str, entry_point: str | None = None, depth: int = 3) -> dict:
        """
        Perform full RCA given a bug description and optional entry point.
        Returns the parsed RCA report as a dict.
        """
        # If no entry point, ask Gemini to identify one from the file list
        if not entry_point:
            entry_point = self._identify_entry_point(bug_description)

        # Step 1: Retrieve relevant code context via the Context Engine
        retrieval = self.context_engine.retrieve(entry_point, depth)
        code_context = self.context_engine.format_for_llm(retrieval)

        # Step 2: Gather impact data from the graph
        impact = self._gather_impact_data(entry_point)

        # Step 3: Build the user prompt
        user_prompt = self._build_user_prompt(bug_description, entry_point, code_context, impact)

        # Step 4: Call Gemini
        response = self.model.generate_content(
            [
                {"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\n" + user_prompt}]},
            ],
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )

        # Step 5: Parse the response
        try:
            rca_report = json.loads(response.text)
        except json.JSONDecodeError:
            rca_report = {"raw_response": response.text, "parse_error": "Failed to parse JSON from Gemini response"}

        return rca_report

    def _identify_entry_point(self, bug_description: str) -> str:
        """Ask Gemini to identify the most likely entry point function from the bug description."""
        file_list = [e["file"] for e in self.parser_output]
        func_list = []
        for entry in self.parser_output:
            for f in entry.get("functions", []):
                if not f.startswith("_"):
                    func_list.append(f"{entry['file']}::{f}")

        prompt = f"""Given this bug description and the list of files/functions in the repository,
identify the single most relevant function name to start the analysis from.
Respond with ONLY the function name (e.g. "execute" or "run"), nothing else.

Bug description: {bug_description}

Files: {json.dumps(file_list)}

Functions: {json.dumps(func_list[:50])}"""

        response = self.model.generate_content(prompt)
        return response.text.strip().strip('"').strip("'")

    def _build_user_prompt(self, bug_description: str, entry_point: str, code_context: str, impact: dict) -> str:
        """Assemble the full user prompt with all context."""
        return f"""
═══════════════════════════════════════════════════════════════
BUG REPORT
═══════════════════════════════════════════════════════════════
{bug_description}

═══════════════════════════════════════════════════════════════
ENTRY POINT: {entry_point}
═══════════════════════════════════════════════════════════════

═══════════════════════════════════════════════════════════════
RETRIEVED CODE CONTEXT (from dependency graph traversal)
═══════════════════════════════════════════════════════════════
{code_context}

═══════════════════════════════════════════════════════════════
DEPENDENCY & IMPACT DATA
═══════════════════════════════════════════════════════════════
Functions that CALL {entry_point}: {json.dumps(impact['callers'])}
Functions CALLED BY {entry_point}: {json.dumps(impact['callees'])}
Files that depend on entry file: {json.dumps(impact['dependent_files'])}
Files IMPACTED if entry file changes: {json.dumps(impact['impacted_by'])}

Analyze this bug following the 9-step process and return the JSON report.
"""

    # ── Report formatting ─────────────────────────────────────────────────────

    @staticmethod
    def format_report(rca: dict) -> str:
        """Format the RCA dict into a human-readable report."""
        if "parse_error" in rca:
            return f"⚠ Could not parse structured report.\n\n{rca.get('raw_response', '')}"

        lines = []
        lines.append("=" * 60)
        lines.append("         ROOT CAUSE ANALYSIS REPORT")
        lines.append("=" * 60)

        lines.append("\nFeature Intent")
        lines.append("-" * 40)
        lines.append(rca.get("feature_intent", "N/A"))

        ep = rca.get("entry_point", {})
        lines.append("\nEntry Point")
        lines.append("-" * 40)
        if isinstance(ep, dict):
            lines.append(f"File     : {ep.get('file', 'N/A')}")
            lines.append(f"Function : {ep.get('function', 'N/A')}")
        else:
            lines.append(str(ep))

        lines.append("\nExecution Flow")
        lines.append("-" * 40)
        flow = rca.get("execution_flow", [])
        for i, step in enumerate(flow, 1):
            lines.append(f"  {i}. {step}")

        lines.append("\nDependencies")
        lines.append("-" * 40)
        deps = rca.get("dependencies", {})
        for key, vals in deps.items():
            if vals:
                lines.append(f"  {key}: {', '.join(str(v) for v in vals)}")

        lines.append("\nEdge Cases")
        lines.append("-" * 40)
        for ec in rca.get("edge_cases", []):
            lines.append(f"  - {ec}")

        fp = rca.get("failure_point", {})
        lines.append("\nFailure Point")
        lines.append("-" * 40)
        if isinstance(fp, dict):
            lines.append(f"File      : {fp.get('file', 'N/A')}")
            lines.append(f"Function  : {fp.get('function', 'N/A')}")
            lines.append(f"Condition : {fp.get('condition', 'N/A')}")
        else:
            lines.append(str(fp))

        rc = rca.get("root_cause", {})
        lines.append("\nRoot Cause")
        lines.append("-" * 40)
        if isinstance(rc, dict):
            lines.append(f"Why            : {rc.get('why', 'N/A')}")
            lines.append(f"When           : {rc.get('when', 'N/A')}")
            lines.append(f"Propagation    : {rc.get('how_it_propagates', 'N/A')}")
        else:
            lines.append(str(rc))

        ia = rca.get("impact_analysis", {})
        lines.append("\nImpact Analysis")
        lines.append("-" * 40)
        for key, vals in ia.items():
            if vals:
                lines.append(f"  {key}: {', '.join(str(v) for v in vals)}")

        sf = rca.get("suggested_fix", {})
        lines.append("\nSuggested Fix")
        lines.append("-" * 40)
        lines.append(sf.get("description", "N/A"))
        for change in sf.get("code_changes", []):
            lines.append(f"\n  File: {change.get('file', '?')}")
            lines.append(f"  Original:\n    {change.get('original', '').replace(chr(10), chr(10) + '    ')}")
            lines.append(f"  Fixed:\n    {change.get('fixed', '').replace(chr(10), chr(10) + '    ')}")

        lines.append(f"\nConfidence: {rca.get('confidence', 'N/A')}")
        lines.append("=" * 60)

        return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    repo_path   = sys.argv[1] if len(sys.argv) > 1 else "sample_test_app"
    bug_desc    = sys.argv[2] if len(sys.argv) > 2 else "The workflow engine crashes when a node has no agent path configured"
    entry_point = sys.argv[3] if len(sys.argv) > 3 else None
    depth       = int(sys.argv[4]) if len(sys.argv) > 4 else 3

    with open("parser_output.json") as f:
        parsed = json.load(f)

    cg = CodeGraph()
    cg.load("codegraph.json")

    agent = RCAAgent(cg, repo_path, parsed)
    print(f"Analyzing: \"{bug_desc}\"")
    if entry_point:
        print(f"Entry point: {entry_point}")
    print(f"Depth: {depth}")
    print()

    result = agent.analyze(bug_desc, entry_point, depth)

    report = RCAAgent.format_report(result)
    # Handle Windows console encoding limitations
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(report)
