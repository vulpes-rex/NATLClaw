# NATLClaw — Customer-Facing Portal: Commercial Lines Insurance

## Vision

Expose NATLClaw through the company portal as an **AI insurance specialist** — a persistent, knowledgeable agent that each customer (or agent/broker) interacts with like a dedicated account team member. It knows their policies, understands their industry, remembers past conversations, works on requests asynchronously, and proactively surfaces risks and opportunities.

In commercial lines, customers don't want a chatbot. They want a **knowledgeable person who knows their account**. NATLClaw's second brain and coworker architecture deliver exactly that.

---

## 1  Why Commercial Lines Is a Perfect Fit

Commercial lines insurance has characteristics that play directly to NATLClaw's strengths:

| Domain characteristic | Why NATLClaw fits |
|---|---|
| **Complex, multi-policy accounts** | Second brain maintains a rich model of each customer's risk profile across all lines |
| **Long relationship cycles** | Brain persists for months/years — the agent remembers every conversation and renewal |
| **Heavy documentation** | Heartbeat loop can ingest and analyse policies, endorsements, loss runs, audits |
| **Regulatory complexity** | Knowledge base accumulates compliance rules, state-specific requirements, filing deadlines |
| **Broker/agent intermediation** | The agent serves both the insured and their broker — different personas, same brain |
| **Seasonal/cyclical workflows** | Hybrid triggers fire on renewal dates, audit deadlines, policy anniversaries |
| **High-value, low-frequency interactions** | Each interaction matters — memory and context are critical |

---

## 2  Architecture: Portal Integration

```
┌─────────────────────────────────────────────────────────────────┐
│  Company Portal (React / Angular)                               │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Customer  │  │ Broker   │  │ Claims   │  │ Admin    │       │
│  │ Dashboard │  │ Portal   │  │ Portal   │  │ Dashboard│       │
│  └─────┬────┘  └─────┬────┘  └────┬─────┘  └────┬─────┘       │
│        │              │            │              │             │
│        └──────────────┼────────────┼──────────────┘             │
│                       │            │                            │
│                       ▼            ▼                            │
│              ┌─────────────────────────────┐                    │
│              │  Chat / Task Panel          │                    │
│              │  "Ask your account team"    │                    │
│              └────────────┬────────────────┘                    │
└───────────────────────────┼─────────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────────────────┐
│  API Gateway (FastAPI / Azure API Management)                     │
│  - Auth (Azure AD B2C / OAuth)                                    │
│  - Rate limiting, tenant isolation                                │
│  - Route to correct customer brain                                │
└───────────────────┬───────────────────────────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────────────────────────┐
│  NATLClaw Multi-Tenant Layer                                      │
│                                                                   │
│  ┌─────────────────────────┐  ┌─────────────────────────┐       │
│  │  Customer Brain A       │  │  Customer Brain B       │       │
│  │  (Acme Manufacturing)   │  │  (Summit Construction)  │       │
│  │                         │  │                         │       │
│  │  Policies: GL, Prop,    │  │  Policies: GL, WC,     │       │
│  │    WC, Auto, Cyber      │  │    Builder's Risk, Auto │       │
│  │  Industry: Manufacturing│  │  Industry: Construction │       │
│  │  Last renewal: Oct 2025 │  │  Last renewal: Mar 2026 │       │
│  │  Open claims: 2         │  │  Open claims: 0         │       │
│  │  Conversations: 34      │  │  Conversations: 12      │       │
│  └─────────────────────────┘  └─────────────────────────┘       │
│                                                                   │
│  ┌─────────────────────────┐                                     │
│  │  Shared Knowledge Brain │                                     │
│  │  - Industry risk models │                                     │
│  │  - Regulatory database  │                                     │
│  │  - Coverage guides      │                                     │
│  │  - Claims patterns      │                                     │
│  └─────────────────────────┘                                     │
└───────────────────────────────────────────────────────────────────┘
```

Each customer gets their own second brain (account memory). A shared brain holds industry knowledge, regulatory rules, and coverage expertise.

---

## 3  Use Cases

### 3.1  Policy Questions and Coverage Guidance

**The most common interaction.** A business owner or risk manager has a question about their coverage.

```
Customer: "We're expanding into a new warehouse on 5th Street.
           Does our current property policy cover it automatically?"

Agent (knows the account):
  "Your current Commercial Property policy (CP-2024-4412) includes an
   Automatic Acquisition clause under Coverage Extension E, which provides
   temporary coverage for newly acquired property for up to 60 days, capped
   at $500,000 or 25% of Blanket Building coverage — whichever is less.

   Your current blanket limit is $3.2M, so the cap would be $800,000 for
   the 60-day window.

   However: your policy has an exclusion for buildings with flat roofs older
   than 20 years. Before you sign the lease, I'd recommend confirming the
   roof type.

   Want me to start a change request to schedule the warehouse permanently?
   I'll need the address, square footage, construction type, and occupancy."
```

**Why NATLClaw is better than a chatbot here:**
- The agent knows the specific policy (brain has ingested the dec pages and endorsements)
- It cross-references the relevant extension *and* the relevant exclusion
- It proactively flags a risk (flat roof exclusion) the customer didn't ask about
- It offers to start the next step (change request) — coworker behavior

---

### 3.2  Certificate of Insurance Requests

The highest-volume task in commercial lines. Customers constantly need COIs for clients, landlords, and contract compliance.

```
Customer: "I need a cert for a new client — Meridian Properties.
           They want to be listed as additional insured and need
           $2M per occurrence GL with a waiver of subrogation."

Agent: "I'll prepare the certificate request. A few things to confirm:

  1. Your current GL limit is $1M per occurrence / $2M aggregate.
     Meridian is asking for $2M per occurrence — that exceeds your
     current limit. Options:
     a) Issue the cert showing your actual $1M limit (some clients accept this)
     b) Request a limit increase to $2M (I can start that quote)
     c) Issue cert showing $1M GL + $1M Umbrella (if the contract allows
        follow-form umbrella to count)

  2. Your policy already has a blanket additional insured endorsement
     (CG 20 10 04 13), so Meridian qualifies automatically for ongoing
     operations. Written contract required — do you have one?

  3. Waiver of subrogation: your policy has a blanket waiver endorsement.
     No issue there.

  Which option for the limit question? Once confirmed I'll submit the
  request to your underwriter."

  📎 I've attached your current GL declarations page for reference.
```

**Why this works:**
- Instant policy knowledge — no "let me look that up"
- Flags the limit gap *before* issuing an incorrect cert
- Offers three resolution paths (coworker judgment, not just data retrieval)
- Knows the endorsement forms by number
- Attaches the relevant document from the customer's file

---

### 3.3  Renewal Management

**Proactive, calendar-driven workflow** — the hybrid trigger architecture fires 90/60/30 days before renewal.

```
[90 days before renewal — automatic alert]

Agent: "📋 Renewal notice: Your Commercial Package policy (PKG-2024-1187)
        renews on July 1, 2026.

        Changes since last renewal I've noticed:
        • You added the 5th Street warehouse (property value +$1.2M)
        • Revenue reported on last audit was $14.2M vs $12M estimated
        • 1 auto claim ($8,400) closed in January
        • No new GL or WC claims
        • Your industry (SIC 3599) saw a 6% average rate increase in Q4 2025

        I'll start preparing renewal submission materials. A few questions:
        1. Any changes to operations, products, or headcount?
        2. Any planned acquisitions or new locations?
        3. Want to quote cyber liability this year? You don't currently
           carry it, and 73% of manufacturers your size do.

        Your broker (Smith & Associates) will receive a copy of this renewal
        summary."
```

**90 → 60 → 30 day cadence:**
- At 90 days: Account review, gather updated exposure data, flag changes
- At 60 days: Submission to underwriting with updated ACORD applications
- At 30 days: Quote received, comparison to expiring, present options
- At renewal: Bind confirmation, updated COIs, new policy documents

Each step is a task in the coworker task system, with automatic triggers from the hybrid scheduler.

---

### 3.4  Claims Support and FNOL

First Notice of Loss (FNOL) is time-sensitive and stressful for the insured.

```
Customer: "One of our delivery trucks was rear-ended on I-95 this morning.
           Driver says he's OK but the truck is not drivable."

Agent: "I'm sorry to hear that. Let me start the claim process right now.

        Based on your Commercial Auto policy (CA-2024-0892):
        • Collision coverage: ✅ ($1,000 deductible)
        • Rental reimbursement: ✅ ($75/day, 30-day max)
        • MedPay: ✅ ($5,000 per person)

        I need a few details to file:
        1. Driver name (I have 4 drivers on your policy — which one?)
        2. Date, time, and exact location
        3. Other vehicle's insurance info (if available)
        4. Police report number (if filed)
        5. Photos of the damage (you can upload here)

        Immediate next steps I'll handle:
        • File FNOL with the carrier
        • Request rental vehicle authorization
        • Notify your broker (Smith & Associates)
        • Flag MedPay if the driver reports any soreness later

        Want me to also check if your Business Auto policy has a
        hired/non-owned extension in case you need a temporary vehicle
        from a different fleet?"
```

**Why this matters:**
- Instant coverage confirmation (customer doesn't have to wonder "am I covered?")
- Structured data collection — agent knows what the carrier needs
- Proactive next steps (rental auth, broker notification)
- Suggests things the customer wouldn't think to ask (hired/non-owned)
- Everything captured in the brain for future reference during the claim lifecycle

---

### 3.5  Risk Management Advisory

**Background heartbeat work** — the agent proactively analyses the customer's risk profile and surfaces recommendations.

```
[Proactive alert — no customer prompt]

Agent: "🔔 Risk Advisory for Acme Manufacturing

        I noticed a trend in your account that's worth discussing:

        1. Workers' Comp Experience Mod: Your current mod is 1.12 (above
           benchmark). Looking at your loss runs, 4 of 6 WC claims in the
           last 3 years involved hand/wrist injuries in the assembly area.

           Recommendation: A machine guarding assessment could reduce claims
           frequency. I can connect you with our loss control team for a
           free on-site assessment. Companies in your NAICS code that
           implemented similar controls saw an average 23% reduction in
           WC claims.

        2. Cyber Exposure: You process customer POs via email and store
           them in an on-premises file server. You currently carry no cyber
           liability coverage. Given recent MFG-sector ransomware trends
           (up 40% YoY per our industry data), I'd recommend at minimum
           a $1M cyber policy.

           Estimated premium: $4,200–$6,800/year based on your revenue
           and employee count.

        Want me to schedule the loss control visit or start a cyber quote?"
```

**This is the killer feature.** No chatbot does this. A human account manager might do it once a year at renewal. NATLClaw does it continuously because the heartbeat loop is always running, always cross-referencing account data against industry trends stored in the shared knowledge brain.

---

### 3.6  Audit Preparation

Premium audits are dreaded by insureds. The coworker makes them painless.

```
[60 days before audit — automatic trigger]

Agent: "📊 Audit preparation: Your annual premium audit for Workers' Comp
        and GL is scheduled for August 15, 2026.

        The auditor will need:
        ☐ Payroll records by classification code (last 12 months)
        ☐ Subcontractor certificates of insurance
        ☐ Gross sales/receipts by revenue category
        ☐ Vehicle schedule with VINs (if auto audit)

        Based on your prior audit, here's what to watch for:
        • Last year you had $180K in clerical payroll misclassified as
          shop floor (Code 3632). This caused a $2,400 additional premium.
          Make sure your payroll system separates clerical (Code 8810)
          from manufacturing (Code 3632).

        • You used 3 subcontractors last year without COIs on file.
          Uninsured sub costs were added to your payroll. I can see
          you've used 2 new subs this year — do you have their certs?

        I can generate a pre-audit checklist based on your specific
        classifications. Want me to also estimate your audit outcome
        based on current payroll data?"
```

---

### 3.7  Broker/Agent Portal

The same system serves brokers — with a different persona and access level.

```
Broker: "Pull up the Acme Manufacturing account. I need to market their
         GL and Umbrella to three carriers."

Agent (broker persona): "Here's the Acme submission package:

        Account summary:
        • Insured: Acme Manufacturing, LLC
        • SIC: 3599 | NAICS: 332710
        • Revenue: $14.2M (audited) | Employees: 87
        • Locations: 2 (Main St + 5th St warehouse, added Feb 2026)

        Current GL program:
        • $1M/$2M GL (current carrier: Hartford, 3 years)
        • $5M Umbrella (follow-form, Hartford)
        • Loss ratio: 32% (3-year), 28% (5-year)
        • Claims: 2 GL claims in 5 years (both < $15K, both closed)

        Submission-ready documents:
        📎 ACORD 125 (pre-filled)
        📎 ACORD 126 (pre-filled)
        📎 5-year loss summary
        📎 Current dec pages
        📎 Narrative supplement (operations, products, contracts)

        Recommended markets for manufacturing GL in this class:
        1. Travelers (strong appetite, competitive on SIC 3599)
        2. CNA (good fit for revenue size, recently expanded MFG appetite)
        3. Zurich (will want the loss control report — available from May 2025)

        Want me to generate the submission emails?"
```

**Why this works for brokers:**
- Account knowledge is instant (no digging through files)
- ACORD forms are pre-filled from the brain's account model
- Market recommendations come from the shared knowledge brain (carrier appetite data)
- Loss history is pre-formatted for submission
- The broker's time goes to relationship work, not data assembly

---

### 3.8  Policy Comparison and Coverage Gap Analysis

```
Customer: "We're getting quotes from three carriers for our renewal.
           Can you help me compare them?"

Agent: "Upload the quotes and I'll break them down. But first, here's
        what I'd look for based on your account:

        Critical coverages for your operation (don't sacrifice these):
        ☑ Products-completed operations (your #1 exposure as a manufacturer)
        ☑ Blanket additional insured (you have 12 contract requirements)
        ☑ Blanket waiver of subrogation (same)
        ☑ Employee benefits liability (you self-administer benefits)

        Red flags to watch for:
        ⚠ Absolute pollution exclusion (your facility uses cutting fluids)
        ⚠ Professional services exclusion (you provide installation services)
        ⚠ Sub-limited product recall (current limit $250K — may need more)

        Upload the quotes and I'll do a line-by-line comparison with
        your current program, highlighting where each carrier is better
        or worse."
```

---

### 3.9  Compliance and Regulatory Alerts

The shared knowledge brain monitors regulatory changes relevant to the customer's industry and state.

```
[Proactive alert — regulatory change]

Agent: "⚡ Regulatory update affecting your account:

        Effective July 1, 2026, your state is increasing minimum auto
        liability limits for commercial vehicles over 10,000 GVW from
        $500K CSL to $750K CSL.

        Your current Commercial Auto policy carries $1M CSL, so you're
        already compliant. No action needed.

        However: your umbrella's auto underlying limit schedule shows
        $500K. This should be updated at renewal to match the new
        minimum, or the umbrella may have an impaired underlying
        limits condition.

        I've added this to your renewal checklist (t_renewal_2026_acme)."
```

---

### 3.10  Self-Service Policy Changes

```
Customer: "We just hired 3 new employees. Two are warehouse workers
           and one is a sales rep who'll be driving a company vehicle."

Agent: "I'll process the updates:

        Workers' Comp:
        • 2 warehouse workers → Class Code 8018 (Wholesale/Warehouse)
        • Current payroll in 8018: $312,000
        • Estimated additional premium: ~$2,100 for remainder of policy term

        Commercial Auto:
        • 1 new driver → I need their name, DOB, license #, and MVR consent
        • Which vehicle will they drive? Your current schedule has 6 vehicles,
          one unassigned (2024 Ford Transit, VIN ending ...4827)

        GL:
        • No change needed — rated on revenue, not headcount

        I'll submit the WC endorsement request now. For auto, reply with
        the driver details and I'll add them. Expected turnaround: 24-48h
        for the endorsement from the carrier."
```

---

## 4  Customer Brain Model

Each customer's brain contains structured knowledge built over time:

```python
# Per-customer brain contents

# Account profile
{"note_type": "account", "content": "Acme Manufacturing LLC",
 "data": {"sic": "3599", "naics": "332710", "revenue": 14200000,
          "employees": 87, "locations": 2, "years_insured": 5}}

# Policy inventory
{"note_type": "policy", "content": "Commercial Package Policy",
 "data": {"number": "PKG-2024-1187", "carrier": "Hartford",
          "effective": "2025-07-01", "expiry": "2026-07-01",
          "lines": ["GL", "Property", "Inland Marine"],
          "premium": 48750}}

# Coverage details (from ingested dec pages and endorsements)
{"note_type": "coverage", "content": "GL: $1M occ / $2M aggregate",
 "data": {"line": "GL", "per_occurrence": 1000000, "aggregate": 2000000,
          "endorsements": ["CG 20 10", "CG 24 04", "CG 20 37"],
          "exclusions": ["absolute_pollution", "EIFS"]}}

# Claims history
{"note_type": "claim", "content": "WC claim - hand laceration, assembly",
 "data": {"number": "WC-2025-0041", "date": "2025-03-12",
          "status": "closed", "paid": 8400, "type": "medical_only",
          "body_part": "hand", "location": "assembly_floor"}}

# Conversation history
{"note_type": "conversation", "content": "Customer wants cyber quote at renewal",
 "source": {"type": "portal_chat", "user": "jsmith@acme.com",
            "timestamp": "2026-04-07"}}

# Preferences and directives
{"note_type": "preference", "content": "Always CC the CFO on renewal docs",
 "data": {"contact": "cfo@acmemfg.com", "scope": "renewals"}}

# Risk observations
{"note_type": "risk", "content": "4 of 6 WC claims are hand injuries in assembly",
 "data": {"trend": "hand_injuries", "location": "assembly", "confidence": 0.91,
          "recommendation": "machine_guarding_assessment"}}
```

---

## 5  Persona Mapping for Insurance

| Persona | Portal role | Use case |
|---|---|---|
| `account_manager` | Customer-facing specialist | Policy questions, coverage guidance, general account service |
| `claims_specialist` | FNOL and claims support | Intake, status updates, document collection, settlement tracking |
| `underwriting_assistant` | Broker-facing | Submission prep, account analysis, market recommendations |
| `risk_advisor` | Proactive risk management | Risk alerts, loss control recommendations, industry trends |
| `compliance_monitor` | Background regulatory watcher | Regulatory changes, filing deadlines, audit preparation |
| `renewal_coordinator` | Renewal workflow manager | 90/60/30 day cadence, exposure updates, quote comparison |

These run as different personas against the same customer brain, each contributing their domain expertise.

---

## 6  Hybrid Triggers in Insurance Context

| Trigger | Source | Insurance action |
|---|---|---|
| **Time-based** (heartbeat) | Scheduler | Background risk analysis, knowledge maintenance, regulatory monitoring |
| **Calendar-driven** | Power Automate / n8n | Renewal cadence (90/60/30 days), audit dates, filing deadlines |
| **Event-driven** | Portal API | Customer message, document upload, cert request |
| **System event** | Policy admin system | Endorsement issued, claim opened/closed, payment received |
| **External data** | Industry feeds, regulatory APIs | Rate changes, new regulations, carrier appetite updates |
| **Threshold-based** | Claims analysis | Loss ratio exceeds benchmark, experience mod projected to change |

---

## 7  Value Proposition

### For customers (insureds)

- **24/7 account access** — coverage questions answered instantly, not next business day
- **Proactive risk management** — the agent flags exposures before they become claims
- **Faster service** — cert requests in minutes, not hours; FNOL filed immediately
- **No repeat conversations** — the agent remembers everything, every time
- **Audit prep made easy** — guided preparation with account-specific checklists

### For brokers/agents

- **Instant submission prep** — ACORD forms pre-filled, loss runs formatted, narrative written
- **Market intelligence** — carrier appetite and rate trends from the shared brain
- **Account review at a glance** — the agent surfaces changes, gaps, and opportunities
- **Renewal assembly automated** — 80% of renewal legwork done by the agent
- **Differentiated service** — "Your account has a dedicated AI specialist" as a selling point

### For the carrier/company

- **Reduced service cost** — high-volume low-complexity work (certs, endorsements, questions) handled by the agent
- **Higher retention** — customers who feel known and proactively served renew more
- **Better risk selection** — the agent identifies risks and coverage gaps before losses occur
- **Faster audit turnaround** — pre-prepared audit packages reduce disputes
- **Data enrichment** — every interaction enriches the customer brain, making underwriting decisions better
- **Scale without headcount** — serve 10x the accounts with the same team, using the agent for routine work and humans for judgment calls

### By the numbers (estimated impact)

| Metric | Before | With NATLClaw |
|---|---|---|
| Cert request turnaround | 4–8 hours | < 5 minutes |
| Renewal prep time (per account) | 6–10 hours | 1–2 hours (review only) |
| FNOL filing time | 30+ minutes (phone) | 5–10 minutes (portal chat) |
| Account questions answered after-hours | 0% | 100% |
| Coverage gaps identified per account/year | 1–2 (at renewal) | Continuous |
| Policy change processing | 2–3 days | Same day |

---

## 8  Security and Compliance Considerations

Commercial insurance data is sensitive. Key requirements:

| Requirement | Implementation |
|---|---|
| **Tenant isolation** | Separate brain per customer; no cross-tenant data leakage |
| **Authentication** | Azure AD B2C or SSO integration with the company portal |
| **Role-based access** | Customer sees their account only; broker sees their book; underwriter sees all |
| **Audit trail** | Every brain note has provenance (who, when, what triggered it) |
| **Data retention** | Comply with state record-retention rules (typically 5–7 years for claims records) |
| **PII handling** | Personally identifiable information (driver's license, SSN for WC) encrypted at rest |
| **SOC 2 / SOX** | Structured logging, access controls, change tracking |
| **State-specific regulations** | Shared brain includes state regulatory database; agent cites applicable rules |
| **Human-in-the-loop** | Coverage opinions include disclaimer; binding authority requires human approval |
| **LLM guardrails** | Agent cannot give legal advice, make coverage determinations, or settle claims — it assists, it doesn't decide |

---

## 9  Implementation Path

### Phase 1: Internal account assistant (weeks 1–4)

Deploy for internal staff first. Underwriters and account managers use the portal agent to answer their own coverage questions faster.

- Ingest policy data (dec pages, endorsements, loss runs)
- Build per-account brains
- `account_manager` persona handles questions
- Validate accuracy against real account scenarios

### Phase 2: Broker portal (weeks 5–8)

Expose to agents and brokers with read-only access to their book of business.

- Submission prep and account summary tools
- ACORD form pre-fill
- Market recommendation from shared brain
- Broker-specific persona and access controls

### Phase 3: Customer self-service (weeks 9–12)

Open to insureds through the company portal.

- Policy questions and coverage guidance
- Certificate requests
- FNOL intake
- Document upload and processing
- Proactive alerts and renewal notifications

### Phase 4: Proactive intelligence (weeks 13+)

Enable background heartbeat for each account.

- Risk trend analysis
- Regulatory monitoring
- Renewal cadence automation
- Coverage gap detection
- Industry benchmarking

---

## 10  Relation to Other Docs

| Document | Connection |
|---|---|
| `docs/coworker-vision.md` | The customer-facing agent IS a coworker — just the customer's coworker instead of yours |
| `docs/comprehensive-hybrid-trigger-architecture.md` | Power Automate triggers for calendar events, n8n for system integrations, custom agents for intelligence |
| `docs/tiered-memory.md` | Atomic notes (individual interactions) consolidate into wiki pages (account summaries, risk profiles) |
| `docs/knowledge-quality.md` | Lint catches stale policy data, source citations track which dec page a coverage answer came from |
| `docs/codebase-learner.md` | Same pattern — but instead of learning a codebase, it learns an insurance account |
| `docs/improvements.md` | Semantic search critical for finding relevant coverage across multi-line accounts |
