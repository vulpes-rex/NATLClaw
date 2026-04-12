"""Build graphify knowledge graph for NATLClaw."""
from graphify.extract import collect_files, extract
from graphify.build import build
from graphify.analyze import god_nodes, surprising_connections
from networkx.readwrite import json_graph
from pathlib import Path
import json, sys

root = Path(__file__).resolve().parent
out_dir = root / "graphify-out"
out_dir.mkdir(exist_ok=True)

# 1. Collect code files
print("=== Collecting files...")
files = collect_files(root)
files = [f for f in files if "__pycache__" not in str(f) and ".pytest_cache" not in str(f)]
print(f"Found {len(files)} code files")

if not files:
    print("No files found!")
    sys.exit(1)

# 2. Extract AST (code-only, no LLM needed)
# extract() takes the full list of paths at once for cross-file resolution
print("\n=== Extracting AST...")
extraction = extract(files)
n_nodes = len(extraction.get("nodes", []))
n_edges = len(extraction.get("edges", []))
print(f"Extracted: {n_nodes} nodes, {n_edges} edges")

if not n_nodes:
    print("No nodes extracted!")
    sys.exit(1)

# 3. Build graph
print("\n=== Building graph...")
G = build([extraction])
print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# 4. Analyze
print("\n=== Analyzing...")
gods = god_nodes(G)
print(f"God nodes: {len(gods)}")
for g in gods[:10]:
    print(f"  {g['label']} (edges={g.get('edges', g.get('degree', '?'))})")

surprises = surprising_connections(G)
print(f"Surprising connections: {len(surprises)}")

# 5. Export graph.json
print("\n=== Exporting...")
graph_json_path = out_dir / "graph.json"
data = json_graph.node_link_data(G, edges="links")
graph_json_path.write_text(json.dumps(data, indent=2))
print(f"Wrote {graph_json_path}")

# 6. Write report
try:
    from graphify import report as _rmod
    _render = getattr(_rmod, "render_report", None) or getattr(_rmod, "render", None)
    if _render:
        report_text = _render(G, {
            "god_nodes": gods,
            "surprising_connections": surprises,
        })
        report_path = out_dir / "GRAPH_REPORT.md"
        report_path.write_text(report_text, encoding="utf-8")
        print(f"Wrote {report_path}")
    else:
        print("Report: no render function found, skipping")
except Exception as e:
    print(f"Report generation skipped: {e}")

print(f"\n=== Done! Graph: {graph_json_path}")
print(f"    {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
print(f"    Start MCP server: python -m graphify.serve {graph_json_path}")
