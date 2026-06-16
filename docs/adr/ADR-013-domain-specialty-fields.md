# ADR-013: Domain & Specialty Fields for Role Frontmatter

## Status

Accepted — locks B-route platform positioning at the role layer.
Companion to ARCHITECTURE.md §0 row 15 revision ("领域无关——软件工程是
flagship 示范场景,角色生态向其他领域开放").

## Context

§0 establishes TerraWorks as a general-purpose multi-agent orchestration
platform with software engineering as flagship scenario. Engine layer
(Session / Guide / Sandbox / Executor) is already domain-agnostic. Only the
role layer and Guide's future assignment routing need to *express* domain.

Terraria NPC names are persona containers — a Merchant sells code utilities
in engineering context, may sell pitch templates in sales context. Profession
mapping is free; the persona supplies narrative + idle behavior, the system
prompt supplies actual expertise.

## Decision

Add two optional frontmatter fields to role .md files:

```yaml
domain: engineering    # default if omitted
specialty: general     # free text; default if omitted
```

**Allowed `domain` values (M1.x/M2):**

`engineering | design | product | sales | research | content | general`

**Default tool whitelist by domain** (loader applies when role omits `tools`):

- `engineering`: `[read, write, bash]`
- all others: `[read, write]`  (M3+ may introduce domain-specific tools)

Per-role `tools:` field always overrides domain defaults.

**Backward compatibility:** existing `roles/guide.md` and `roles/merchant.md`
remain valid unchanged. Loader applies defaults (`domain=engineering`,
`specialty=general`) when fields are absent. No migration required.

**Assignment routing (deferred to M2):** Guide prefers roles where `domain`
matches the task card's stated domain (if any), then prefers `specialty`
substring match against task description. Tie-break by least-loaded NPC
instance. No effect on M1.3-4 / M1.4.

## Consequences

**Positive:**

- Engine stays domain-agnostic; adding a new domain = writing role .md files
  + (optionally) new tools, not modifying core
- Terraria NPC naming preserved across domains (persona ≠ profession binding)
- 3rd-party agent packs (e.g. contains-studio agents style) plug in as role
  .md bundles with explicit domain field, no engine modification

**Negative:**

- Loader complexity rises slightly (default lookup + whitelist resolution)
- `specialty` as free text → no central registry, minor duplication risk
  ("frontend" vs "front_end"); canonicalization deferred to M3+ if needed

**Out of scope:**

- Verification paradigms beyond current schema (`machine_verifiable` +
  `hitl_escalation`) — HITL handles non-engineering domains for now
- Role marketplace UI (M4 GUI work; see future-directions.md)
- Domain-specific tool implementations (M3+)
