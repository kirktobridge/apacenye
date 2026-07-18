You are continuing Apacenyë, a personal Kalshi trading platform. This is 
Session 2 of 5. You have NO memory of Session 1 — before doing anything, 
read docs/initial-bootstrap/handoffs/stage1-foundations.md in full and confirm back to me in 2-3 
sentences what market categories and constraints you're building on.

ABOUT ME: See docs/initial-bootstrap/ABOUT_ME.md.

TASK — Strategy Design:
- For each promising category identified in Stage 1, propose 2-3 concrete strategy archetypes (fair-value modeling, mean-reversion, cross-market 
  arbitrage, news-driven momentum, etc.).
- For each: define signal/data source, fair-value probability computation,  entry/exit rules, position sizing (e.g., fractional Kelly), and risk limits. Explain the reasoning in plain terms, especially WHY sizing matters as much as being "right" — use a numeric example showing two strategies with the same win rate but different outcomes due to sizing.
- Design every strategy as a self-contained, swappable unit — assume multiple concurrent strategies from day one. Specify what data/interface contract each strategy must expose so Session 3 can design the orchestrator around it.
- Rank strategies them by confidence and implementation complexity, and recommend which ONE strategy we should build first as the reference implementation for Session 5.
- Acceptance criteria: I should be able to explain, in my own words, the difference between "being right" and "sizing correctly," and know which strategy we're building first and why.

OUTPUT REQUIREMENT: Write full findings to docs/initial-bootstrap/handoffs/stage2-strategies.md, including the strategy interface contract (inputs/outputs every worker must expose) since Session 3 depends on this to design the orchestrator. Include an "Open Decisions" section.

If a design decision requires trade-offs I haven't weighed in on, stop and ask.

---

## CROSS-REVIEW HARDENING (authoritative additions)
- **Workers never place orders directly.** A worker's interface EMITS an "order intent" (side, market, size, limit price, plus the explanation fields below). The orchestrator (Stage 3) owns actual order submission and may resize or reject an intent to honor portfolio risk limits and the kill switch. Design the contract as propose-then-approve, not self-execution — the Stage 3 risk layer cannot enforce anything otherwise.
- **Specify the contract semantically; the stack is Stage 3's call.** Define the lifecycle states each worker must support (init / start / pause / stop / update-config), the inputs it consumes, and the fields it emits — but explicitly defer the concrete language/runtime binding to Stage 3 (the stack is chosen there). Note that Stage 3 may refine the mechanical form.
- **Explanation fields every worker MUST expose** (Stage 3 builds explanation objects from these): model probability, market-implied probability, edge NET of costs, a confidence measure, and the key inputs behind the estimate.
- **Net-of-cost edge is mandatory.** All edge thresholds and entry/exit rules must be net of the round-trip transaction cost quantified in Stage 1 AND net of an explicit assumed slippage/spread cost. A trade qualifies only if net edge exceeds the threshold; state the slippage assumption you use.
- **Sizing safety.** Fractional Kelly is being fed the model's ESTIMATED probability, not a true one — say so explicitly. Apply a calibration/shrinkage factor and a hard per-position size cap that binds independently of Kelly, so a miscalibrated model cannot produce an oversized bet. Use the numeric example to show over-sizing risk, not only under-sizing.
- **Reference-implementation tie-break.** When confidence and implementation-complexity rankings conflict, pick the SIMPLER, most-representative strategy as the Stage-5 reference implementation (its job is to prove the architecture, not to be the best bet) and state the tradeoff.