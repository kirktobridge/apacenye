You are acting as a quantitative strategy architect helping me build Apacenyë, a personal Kalshi trading platform. This is Session 1 of a 5-session project. 
There is no prior context — you are starting cold.

ABOUT ME: See docs/initial-bootstrap/ABOUT_ME.md.

TASK — Foundations & Market Research:
- Explain Kalshi's order book, contract pricing (price = implied probability), settlement, and fee structure — grounded in my STEM/data background, not trading jargon. Use concrete numeric examples, not abstractions.
- Identify 3-5 Kalshi market categories where a systematic edge is plausible. For each, explain WHY (data availability, inefficiency, liquidity, resolution clarity) with specific evidence, not general reasoning.
- Explicitly name 2-3 categories too thin, manipulated, or discretionary to automate, and why.
- Acceptance criteria: by the end of this session I should be able to answer, unaided: (1) how is contract price related to probability, (2) what makes a market "inefficient" in a way I could exploit, (3) which category I'm most likely to pursue first and why.

OUTPUT REQUIREMENT (critical — this session's memory will not persist):
Write your full findings to a file at docs/initial-bootstrap/handoffs/stage1-foundations.md, structured so Session 2 (Strategy Design) can read it without me re-explaining anything. Include a short "Open Decisions" section at the end listing anything ambiguous or unresolved that Session 2 should treat as a starting question.

If you hit a design decision I haven't addressed, stop and ask me rather than assuming. Do not proceed past this Stage's scope.

---

## CROSS-REVIEW HARDENING (authoritative additions)
- **No live data in this session — do not fabricate specifics.** You have no live Kalshi access here. Do not invent liquidity, volume, or spread numbers. Label each quantitative claim as either verifiable general knowledge or an estimate to confirm against live Kalshi; put any specific figure a later strategy would depend on into "Open Decisions" as "verify against live data before use."
- **Quantify transaction cost for reuse.** In the fees/settlement section, give the total round-trip cost per contract (entry + settlement fees) as a concrete formula and a worked number, because Stage 2 must require every trade's edge to exceed this cost. Make it copy-pasteable.
- **Acceptance is a handoff self-check.** Since this session may be unattended, treat the acceptance criteria as a checklist the handoff itself must satisfy: a reader with ONLY the handoff can answer all three questions. End the handoff with a short self-check confirming each is answerable from the text, and flag any you could not fully support.