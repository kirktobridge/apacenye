You are continuing Apacenyë. This is Session 3 of 5. You have NO memory of prior sessions — read docs/initial-bootstrap/handoffs/stage1-foundations.md and docs/initial-bootstrap/handoffs/stage2-strategies.md in full before proceeding. Confirm back the strategy interface contract from Stage 2 before designing around it.

ABOUT ME: read docs/initial-bootstrap/ABOUT_ME.md

STACK:
Choose the stack based on:
1. Library maturity for the actual problem (e.g. backtesting engines, async market-data streaming, numerical/probability computation - not exhaustive list, consider different problems we'll have to solve).
2. My ability to and reason about the architecture well enough to review correctness in financial logic.
3. Long-term single-maintainer complexity (fewer languages/runtimes = fewer things that silently rot between sessions).

Explicitly name 2-3 viable stack options, state the trade-off of each against these three criteria, and recommend one. Explain what I'd need to be able to read (not write) to stay a competent reviewer.

TASK — Platform Architecture:
- Orchestrator-workers model: independent strategy workers scoped to one market/strategy combo, start/pause/stop/update independently.
- Portfolio-level risk layer (aggregate exposure, correlation checks, kill switch) owned by orchestrator, not workers.
- Service/API layer: persistent backend (REST + WebSocket) for live state, positions, signals.
- Browser dashboard: core views — live positions/P&L, per-strategy status/controls, signal/reasoning feed, portfolio risk indicators.
- Explanation objects: structured per-trade/signal explanation (model estimate vs. market-implied probability, edge size, confidence, key inputs).
- Concept checkpoints: specify the EXACT acknowledgment mechanism (CLI prompt? dashboard modal? required text confirmation stored in a log?) — don't leave this abstract, since Session 5 needs to implement it precisely.
- Security requirement: specify credential/secrets handling — API keys, strategy configs — must never be hardcoded; document the .env structure and required .gitignore entries.
- Recommend a stack given the requirements above (note: settlement is minutes-to-months, so raw execution speed is not the binding constraint). Justify explicitly against my stack comfort — don't default to novelty.
- Note where a single unified codebase vs. split stack changes maintenance complexity, given this is a single-user, single-maintainer project.

OUTPUT REQUIREMENT: Write full architecture to docs/initial-bootstrap/handoffs/stage3-architecture.md, including the finalized concept-checkpoint mechanism and the .env/secrets convention, since Session 4 and 5 both depend on these being concrete, not conceptual. Include an "Open Decisions" section.

If you hit an ambiguity Sessions 1-2 didn't resolve, stop and ask.

---

## CROSS-REVIEW HARDENING (authoritative additions)
- **Stack: the user reviews only Python** (see ABOUT_ME). Weight criterion #2 accordingly — keep all money-touching/financial logic in Python and choose a dashboard approach that needs little or no hand-written JS the user must review. Justify any non-Python component as unavoidable.
- **Orchestrator sits IN the order path** (this is what makes the risk layer real): workers emit order intents (Stage 2); the orchestrator validates each intent against portfolio risk limits and the kill switch BEFORE it reaches the Kalshi client, and may resize or reject. Workers never call the order API directly. Specify this data flow concretely.
- **Name concrete, configurable risk limits** — max aggregate exposure, max per-strategy exposure, max single-order size, max daily loss — sourced from config/.env with conservative defaults, and specify that the risk layer REJECTS or RESIZES any intent that would breach them. State how per-strategy limits (Stage 2) compose with portfolio limits: the portfolio cap always dominates.
- **Concept-checkpoint CONTENT and trigger, not just mechanism.** Trigger: a request to enable real capital for a specific strategy. The checkpoint requires the user to type an acknowledgment demonstrating understanding of, at minimum: implied vs. true probability; that sizing runs on an ESTIMATED not true probability; that strategy's worst-case drawdown; round-trip fees + slippage; and paper-vs-live fidelity limits. Persist each acknowledgment (timestamp, exact strategy + parameters, concepts acknowledged) to an append-only log. Specify the exact acknowledgment mechanism as already required.
- **Explanation objects** are populated from the Stage 2 worker fields (model prob, market prob, net edge, confidence, key inputs). If a needed field is missing from the contract, record it as a contract amendment in Open Decisions — do not silently work around it.
- **Backtest data provenance.** Specify WHERE historical Kalshi data comes from (documented API endpoint, recorded live capture, or a named external dataset), its known limitations, and the on-disk format the harness consumes. If no reliable source exists, say so plainly and mark backtests as illustrative-only, NOT a basis for live confidence — an Open Decision, never synthetic data presented as real.
- **Run-mode flag + out-of-band kill switch.** Include a global run-mode flag (DRY_RUN/PAPER vs LIVE); LIVE is hard-disabled in this bootstrap (paper-only — see ABOUT_ME). The kill switch must have an OUT-OF-BAND trigger (e.g., a sentinel file or standalone CLI command) that halts new-order submission even if the dashboard/orchestrator API is down; define what "halt" does to open positions.