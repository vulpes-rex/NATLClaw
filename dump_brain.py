"""Quick script to dump brain contents."""
from second_brain import load_brain

b = load_brain("data/agent_state.json")
print(f"Notes: {len(b.notes)}, Connections: {len(b.connections)}, Reviews: {len(b.review_log)}\n")

for nid, n in b.notes.items():
    cat = n.get("category", "?")
    summary = n.get("summary", "")[:60]
    tags = n.get("tags", [])
    conns = n.get("connections", [])
    print(f"  {nid} [{cat}] {summary}")
    print(f"    tags={tags}  connections={conns}")

print()
for c in b.connections:
    print(f"  {c['from']} <-> {c['to']}: {c['reason'][:100]}")

print()
for r in b.review_log[-3:]:
    print(f"  Review {r['timestamp']}: {r['summary'][:120]}")
