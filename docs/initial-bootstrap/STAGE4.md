You are continuing Apacenyë. This is Session 4 of 5. Read 
docs/initial-bootstrap/handoffs/stage1-foundations.md, 
docs/initial-bootstrap/handoffs/stage2-strategies.md, and 
docs/initial-bootstrap/handoffs/stage3-architecture.md in full before proceeding.

STRUCTURAL NOTE: Write this Stage's CLAUDE.md and skills based strictly on the architecture decisions already finalized in Stage 3 — flag any skill step that depends on a file/module Session 5 hasn't created yet, so it can be verified and corrected after implementation rather than assumed correct now.

TASK — Project Conventions & Reusable Skills:
- Create CLAUDE.md at repo root: architecture summary, coding standards, directory layout, always-apply rules. Keep it under ~250 words per section (overview, tech stack, current state) per Claude Code memory best practices — do not bloat it with workflow detail.
- Identify recurring workflows: adding a new strategy worker, running a backtest, reviewing a strategy's risk profile pre-live, onboarding a new market's data source.
- For each, create .claude/skills/<skill-name>/SKILL.md with frontmatter (name, description) and step-by-step instructions.
- After drafting each skill, add a "Verify After Scaffolding" checklist — a short list of assumptions this skill makes about file paths, interfaces, or naming conventions that must be confirmed once Session 5 actually builds the code.

OUTPUT REQUIREMENT: Write CLAUDE.md and all skill files directly to the repo structure. Additionally write docs/initial-bootstrap/handoffs/stage4-conventions-summary.md listing every skill created and its "Verify After Scaffolding" checklist, so Session 5 knows exactly what to sanity-check once real code exists.

---

## CROSS-REVIEW HARDENING (authoritative additions)
- **Paper-only + stop-and-ask apply here too.** This bootstrap is paper-only (this stage does not read ABOUT_ME, so it is restated here). If a convention is ambiguous: ask if I'm present; if unattended, record it in stage4-conventions-summary.md with a conservative default rather than inventing a convention silently.
- **CLAUDE.md "current state" and "directory layout" are PROVISIONAL** — no code exists until Stage 5. Mark both sections "provisional — reconcile in Stage 5," and add them to the Verify-After-Scaffolding items so Stage 5 corrects CLAUDE.md itself, not only the skills.
- **Safety rules are exempt from the ~250-word budget.** The always-apply section MUST include, regardless of length: paper-only default with live hard-disabled; workers never place orders directly (intents only); secrets via .env, never hardcoded; and the numeric risk guardrails. Trim workflow prose before trimming these.
- **The "review a strategy's risk profile pre-live" skill is the workflow that LEADS UP TO and triggers the Stage 3 concept-checkpoint** — not a second, separate gate. It must reference the same mechanism and append-only log.
- **The backtest skill must reference the Stage 3 backtest data source** and repeat its stated limitations.