# Stage 4 Handoff — Conventions & Skills Summary

**Project:** Apacenyë — personal Kalshi trading platform (PAPER-ONLY bootstrap; live remains hard-disabled — restated here per the hardening instruction, since this stage does not read ABOUT_ME).
**Written:** 2026-07-18 (Session 4 of 5)
**Audience:** Session 5 (Implementation). Read this *after* `stage3-architecture.md`. Its purpose: tell you exactly what conventions/skills now exist and **what to sanity-check once real code exists**, because everything below was written against the Stage 3 *plan*, not against code.

---

## 1. What this session created

| File | Purpose |
|---|---|
| `CLAUDE.md` (repo root) | Overview, tech stack, provisional directory layout & current state, coding standards, always-apply safety rules |
| `.gitignore` | Stage 3 §10 verbatim entries (see §4 below for why this session created it) |
| `.claude/skills/add-strategy-worker/SKILL.md` | Workflow: adding a new strategy worker |
| `.claude/skills/run-backtest/SKILL.md` | Workflow: running a replay backtest honestly |
| `.claude/skills/review-risk-pre-live/SKILL.md` | Workflow: pre-live risk review → triggers the Stage 3 §11 concept checkpoint (same gate, same log — not a second gate) |
| `.claude/skills/onboard-data-source/SKILL.md` | Workflow: onboarding a new market's data source |
| this file | Master verify list for Session 5 |

No code, no config files, no `.env`, no secrets were created. The repo remains docs + conventions only.

---

## 2. CLAUDE.md items Session 5 must reconcile (not only the skills)

Per the hardening instruction, CLAUDE.md itself carries provisional content:

- [ ] **"Directory layout" section** is marked *PROVISIONAL* — it restates Stage 3 §12. After scaffolding, edit it to match the real tree (or confirm it matches).
- [ ] **"Current state" section** is marked *PROVISIONAL* and says "docs-only" — rewrite it once code exists (what's implemented, what's stubbed, how to run).
- [ ] Entry-point spellings in CLAUDE.md (`apacenye serve | kill | unkill | ack | enable-live | status`) — confirm against the real `cli.py`.
- [ ] The always-apply rules restate Stage 2/3 numbers (caps, λ=0.5, k=0.25, −2%/−5% stops, 4-pt floor). If any OD ratification changes a number, CLAUDE.md must be updated in the same commit as `config/risk.yaml`.

## 3. Per-skill "Verify After Scaffolding" checklists

(Duplicated from each SKILL.md so this file is the single master list.)

### add-strategy-worker
- [ ] Lifecycle ABC exists at `src/apacenye/workers/base.py`; real class/method spellings for `INIT/START/PAUSE/STOP/UPDATE_CONFIG`.
- [ ] Contract models in `src/apacenye/contract/`; exact `OrderIntent` field names; whether OD-15 `quote_seen` was ratified and added.
- [ ] Strategy configs at `config/strategies/<id>.yaml`; actual config-loading/validation mechanism.
- [ ] Worker registration/discovery mechanism and `TickScheduler` cadence declaration.
- [ ] Shadow-evaluation emission API and heartbeat mechanics.
- [ ] CLI spelling `apacenye ack --strategy <id> --gate paper`; DRY_RUN behavior as described.
- [ ] Reference worker actually named `w1_forecast.py`.
- [ ] Real home for strategy one-pagers (skill assumes `docs/strategies/`).

### run-backtest
- [ ] Actual backtest entry point (CLI subcommand vs. module) and real flags.
- [ ] Capture layout/schema match `data/capture/YYYY-MM-DD/<channel>.jsonl.gz` + line format; real channel names.
- [ ] Coverage-gap detection: automatic or manual (and with what tool).
- [ ] Where calibration outputs (Brier/reliability) are computed.
- [ ] OD-14: Kalshi historical endpoints — existence, granularity, terms.
- [ ] Replay drives the same `TickScheduler` tick objects (no parallel scheduler).
- [ ] Where results are written, so the illustrative-only label attaches at the source.

### review-risk-pre-live
- [ ] CLI spellings/flags: `apacenye enable-live --strategy <id>`, `apacenye ack --strategy <id> --gate live`, `apacenye ack --verify-log`.
- [ ] Ack log path `data/acks/acknowledgments.jsonl`; hash-chain fields as implemented.
- [ ] Risk-relevant config-hash param list as implemented (Stage 3 §11.2: bankroll, exposure caps, k, λ, edge floor).
- [ ] Endpoints exist as named: `/api/evaluations`, `/api/intents`, `/api/risk`, `/api/acks`.
- [ ] Where shadow-forecast calibration metrics are computed.
- [ ] `enable-live` really terminates at `LiveDisabledError` and records the refusal `outcome_note`.
- [ ] Ledger table names (`evaluations`, dispositions) match real DDL.

### onboard-data-source
- [ ] Adapter home `src/apacenye/dataadapters/`; actual shared adapter interface.
- [ ] Capture-writer registration API; real channel-naming convention.
- [ ] Catalog at `src/apacenye/marketdata/catalog.py`; how ticker→event mappings are declared.
- [ ] How the S1 monitor discovers bracket sets.
- [ ] Where staleness windows live in config; exact key names G4 reads.
- [ ] `.env` naming convention for new credentials; secret-redaction hook.
- [ ] Whether a shared rate-limit/backoff helper exists.

---

## 4. Decisions made unattended this session (conservative defaults; flag to user, do not silently change)

- **D4-1 — `.gitignore` created now, by Stage 4.** Stage 3 §10 requires the ignore rules to exist *before any secret or `data/` content exists on disk* and assigned git-init sequencing to Stage 4 by default. The repo was already git-initialized (docs-only commits), so the conservative reading is: commit `.gitignore` in this stage's commit, before Session 5 creates `.env`/`secrets/`/`data/`. Entries are Stage 3 §10 verbatim. **Session 5: do not create `.env` or `secrets/` content until this `.gitignore` is committed.**
- **D4-2 — Skill names** chosen as `add-strategy-worker`, `run-backtest`, `review-risk-pre-live`, `onboard-data-source`. Pure naming; no user input needed unless preferred otherwise.
- **D4-3 — CLI/endpoint spellings in skills** are taken from Stage 3 verbatim where Stage 3 fixed them (`apacenye ack`, `enable-live`, `--verify-log`, API routes) and *invented conservatively* where it didn't (the backtest entry point `apacenye backtest --strategy --from --to` is a **guess**, explicitly flagged in the skill). Session 5 should implement to match the fixed spellings and is free to choose the backtest entry point, then update the skill.
- **D4-4 — Strategy one-pager location** `docs/strategies/<id>.md` is a new convention this session invented (Stage 3 defined no home for per-strategy design docs). Low stakes; relocate freely and update the skill.
- **No ambiguity requiring a stop-and-ask arose.** The only judgment calls are D4-1…D4-4 above, all recorded here per the unattended protocol.

## 5. Carried-forward items unchanged (for completeness)

- **Awaiting user ratification:** OD-15 (`quote_seen` field), OD-16 (50% portfolio cap), OD-17 (−5% portfolio auto-kill), OD-18 (dashboard localhost/no-auth default), OD-8/9/10 (bankroll, sizing hyperparameters, city list).
- **Still [verify] before use:** OD-1 (fees), OD-2 (liquidity), OD-3 (listings), OD-5 (demo env, optional), OD-11 (IEM archive), OD-12 (W2 precondition), OD-13 (E1 data path), OD-14 (historical endpoints), OD-19 (Kalshi auth/rate limits — **blocks `kalshi.py`**).
- **Binding honesty rule** repeated in CLAUDE.md and the backtest skill: backtests are illustrative-only until weeks of own capture exist; never citable toward live enablement.

## 6. Handoff self-check

**(1) Can Session 5 find every assumption to verify?** §2 (CLAUDE.md's own provisional sections) + §3 (all four skill checklists, duplicated verbatim) form one master list. **Supported.**
**(2) Are conventions traceable?** Every rule in CLAUDE.md cites Stage 2/3 sections; unattended choices are isolated in §4 with reasoning. **Supported.**
**(3) Safety restated?** Paper-only + hard-disable, intents-only, secrets, and numeric guardrails appear in CLAUDE.md's always-apply section (exempt from the word budget, per the hardening instruction) and in the relevant skills. **Supported.**
