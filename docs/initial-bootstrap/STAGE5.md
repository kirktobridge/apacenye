You are continuing Apacenyë. This is Session 5 of 5 — the implementation session. Read ALL prior docs first in docs/initial-bootstrap/handoffs/: 
- stage1-foundations.md
- stage2-strategies.md
- stage3-architecture.md
- stage4-conventions-summary.md
- and Read plus the repo's CLAUDE.md and .claude/skills/ files. 
Confirm back a one-paragraph summary of the approved strategy, architecture, and stack before writing any code.

TASK — Implementation:
- Scaffold: Kalshi API client (auth, market data, order placement), backtesting harness, one strategy worker (the one ranked highest priority in Stage 2), orchestrator, API/WebSocket service layer, minimal dashboard shell.
- Default to dry-run/paper-trading. This bootstrap is PAPER-ONLY: the live-order code path may be scaffolded, but live enablement MUST be hard-disabled behind a flag that cannot be turned on in this session. Implement the concept-checkpoint gate (with the content defined in Stage 3) and the live code path, but leave real capital unreachable, and document exactly what must be true before a later dedicated hardening session could enable it.
- Include realistic error handling and Kalshi API rate-limit handling.
- Follow the credential/secrets convention from Stage 3 exactly — no hardcoded keys.
- Gap to address (from Stage 4): as you scaffold, go through each skill's "Verify After Scaffolding" checklist and correct any assumption that turned out wrong once real file paths/interfaces exist. Update the skill files in place if needed.
- Test-first constraint (gap fix): write unit tests for all core financial logic — probability calculations, Kelly sizing, P&L math — before wiring them into the orchestrator.
- Be explicit in comments/docs about capital-at-risk, worst-case drawdown scenarios for the chosen strategy, and the specific limits of the backtest data used (time period, market conditions, sample size).
- Constraint: full compliance with Kalshi ToS and applicable regulations — no manipulation, no illegal activity.

OUTPUT REQUIREMENT: After scaffolding, write docs/initial-bootstrap/handoffs/stage5-implementation-log.md summarizing what was built, what deviated from the Stage 3/4 plans and why, and an explicit list of what remains before real capital could responsibly be enabled.

If you hit an ambiguity not resolved in any prior stage doc, stop and ask rather than assuming.

---

## CROSS-REVIEW HARDENING (authoritative additions)
- **This bootstrap is PAPER-ONLY.** Scaffold the live-order path but keep live enablement hard-disabled behind a flag that cannot be turned on in this session. Build the concept-checkpoint gate and the live code path, but leave real capital unreachable, and document precisely what must be true before a later dedicated hardening session could enable it.
- **Make the risk layer functional and tested, not a stub.** The orchestrator must enforce guardrails on order intents (reject/resize) and honor the kill switch before any (paper) submission. Because only one worker exists, unit-test aggregate-exposure, correlation, and daily-loss logic with SYNTHETIC multi-position scenarios — do not leave it unexercised.
- **Define paper-fill semantics.** Document the exact paper fill model: fills at the current best OPPOSING quote or worse, including the assumed spread/slippage cost; never assume free mid-price fills. State in comments that paper P&L is an optimistic bound.
- **Idempotent order submission.** Use client-generated order IDs so retries / rate-limit backoff can never double-submit; unit-test the retry path against duplicate submission.
- **Guardrails exercised even in paper mode.** Enforce max-order-size, max-position, and max-daily-loss pre-submission from .env with conservative defaults; clamp any Kelly-sized intent that exceeds the per-position cap.
- **Financial-logic tests must cover** net-of-fee edge, fractional Kelly with the shrinkage discount and the hard cap (assert the cap binds when Kelly would exceed it), and P&L math — before wiring into the orchestrator.
- **Out-of-band kill switch** from Stage 3: implement it and test that it halts new (paper) order submission when the orchestrator API is unreachable.
- **Reconcile CLAUDE.md too** — correct its provisional directory-layout and current-state sections to match what you actually built, not only the skill files.
- **Backtest honesty in the log.** State the actual historical data source used and its limits; if none is reliable, label all backtest results illustrative-only and explicitly NOT a basis for enabling real capital.