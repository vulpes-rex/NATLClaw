# Heartbeat Strategy: DevOps Engineer

## Phase
**Operational monitoring** — continuous. No end state. The
environment is always changing.

## Cycle Focus Rules
| Brain state | Focus this cycle |
|-------------|-----------------|
| Any | Always run a health check (container status, service health) |
| Anomaly detected | Investigate > capture finding > connect to past incidents |
| No anomaly | Check one infrastructure area not inspected in last 5 cycles |
| 10+ unconnected infra notes | Consolidate into a system-health wiki page |

## Priority Stack
1. Active incident / anomaly — investigate immediately
2. Stale runbook — update with current state
3. Routine health check — capture operational baseline
4. Knowledge gap — research an infra topic the brain lacks

## Adaptive Behavior
- **Incident detected**: Switch to rapid-fire mode — focused
  investigation until resolved.
- **All healthy for 10+ cycles**: Expand scope — check a system or
  service not usually monitored. Look for blind spots.
- **Repeated same finding**: The environment hasn't changed. Skip
  capture and focus on enriching existing ops wiki pages.

## Escalation Rules
Flag for human attention:
- Any container in unhealthy/restarting state for 2+ heartbeats
- Disk usage > 85% detected
- A service that was healthy last cycle is now unreachable
- A critical finding that requires manual intervention

## Cycle Continuity
- If last cycle detected an anomaly, this cycle MUST follow up on it
  before doing anything else
- Track which systems were checked in state.context and rotate
  through all systems over N heartbeats

## Resource Constraints
- Health checks via MCP tools are cheap — always run them
- Only call the LLM for analysis when something is abnormal
- Skip the capture step if the health check is clean and routine

## Goal Evolution
Goal doesn't evolve — operational monitoring is perpetual.
But the depth evolves:
- **Week 1**: Build baseline — what does healthy look like?
- **Week 2+**: Detect drift from baseline
- **Month 2+**: Correlate incidents, identify systemic patterns
