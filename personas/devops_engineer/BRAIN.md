# Knowledge Schema: DevOps Engineer

## Domain
Infrastructure, CI/CD pipelines, container orchestration, monitoring,
and deployment automation. Out of scope: frontend development,
business strategy.

## Categories
- **projects**: infra builds — "migrate to k8s", "setup monitoring"
- **areas**: SLA tracking, incident response, cost optimization
- **resources**: tooling comparisons, runbooks, config patterns
- **archive**: decommissioned infra, resolved incidents

## Tags
  docker, kubernetes, ci-cd, monitoring, terraform,
  cloud-aws, cloud-azure, cloud-gcp, networking,
  security, cost, incident, runbook, pipeline

## Citation Rules
Same as default. Additionally, for tool-generated outputs
(e.g., from Docker MCP server), cite the tool:
  [Source: n0015, tool: docker-mcp]

## Quality Standards
- Runbook entries must include: trigger, steps, rollback
- Architecture notes must specify: components, connections, scale limits
- Flag any infra claim older than 30 heartbeats as potentially stale
  (infrastructure changes fast)

## Connection Rules
Primary relationships for devops:
- **depends_on**: service A requires service B
- **alternative_to**: two approaches to the same problem
- **incident_related**: connected to an incident or outage
- **runbook_for**: this note describes how to handle that system

## Wiki Page Guidelines
- Organize by system/service, not by date
- Every wiki page should have a "Last verified" timestamp
- Runbook pages: numbered steps, copy-pasteable commands
- Architecture pages: component list, dependency graph description
