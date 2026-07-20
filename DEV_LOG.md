# Dev log

Append-only journal of substantive changes; schema in `docs/SCHEMAS.md`.
Entries before 2026-07-19 are backfilled one-liners from the bootstrap
commits; full detail lives in `docs/initial-bootstrap/handoffs/`.

## 2026-07-18 — Stages 1–4: design bootstrap (docs)
Foundations, strategy design (W1-v0 picked), architecture (propose-then-approve,
G0–G10, kill switch, checkpoints), conventions + skills. No code.
Tests: —. Backlog: —. ODs: OD-1…OD-20 opened; 4/6/7 resolved. Ratification: OD-15…18 pending.

## 2026-07-19 — Stage 5: full platform implementation (platform)
All-Python monolith built and smoke-tested end-to-end against live read-only
Kalshi + NWS: domain math (tests-first), ledger, risk engine with reservation
accounting, paper simulator, double live hard-disable, checkpoint gates,
orchestrator, dashboard, replay harness, CLI. Verified live: OD-19 auth/rate
limits, KXHIGHNY strict-tail bracket semantics, KNYC grid OKX 34,45.
Tests: 98 passed. Backlog: —. ODs: OD-19 resolved, OD-3 (NYC) partial.
Ratification: OD-8/9/10/15/16/17/18 + D5-1…D5-14 pending (see implementation log §2).

## 2026-07-19 — Lifecycle skill suite + tracking docs (docs)
Added the four lifecycle skills (triage-idea, dev-cycle, operate-paper,
review-calibration) so the suite covers idea→backlog→ship→operate→calibrate;
seeded BACKLOG.md (B-1…B-12) and this DEV_LOG; doc schemas centralized in
docs/SCHEMAS.md; CLAUDE.md gained a Process section. Risk-config changes fold
into dev-cycle as a branch rather than a ninth skill (anti-bloat call).
Tests: 98 passed (docs-only change). Backlog: B-1…B-12 added. ODs: —.
Ratification: — (backlog item B-2 tracks the pending pass).

## 2026-07-19 — B-1: evidence-based W1 σ from forecast-error archive (strategy)
Replaced W1's placeholder `sigma_f: 3.0` with an evidence-based 3.2°F (OD-11).
Committed a reproducible offline study (`research/estimate_sigma_w1.py`): GFS
extended MOS ("MEX") daytime-max forecasts from the IEM archive vs observed KNYC
highs, σ = population std of the error at the shortest archived lead (≤48h) —
raw 3.14, n=183, bias −0.64°F, rounded UP to 3.2 (conservative). σ rises
monotonically with lead (3.1→6.9°F), which sanity-checks the method. Two archive
gotchas documented in the script: IEM's csv.php ignores sts/ets (must iterate
explicit `runtime=`), and n_x holds both max and min (max = the 00Z-valid row).
Owner decision: a true INTRADAY σ curve isn't buildable from IEM MOS (too sparse
sub-daily); deferred to shadow-forecast calibration (backlog B-13). Kept the
scalar `sigma_f` so no worker-signature change. Note: `sigma_f` is W1 strategy
config, not in the risk-relevant ack hash, so no paper ack was invalidated.
Tests: 105 passed (+7 new for the study math). Backlog: B-1 closed, B-13 added.
ODs: OD-11 resolved. Ratification: — (σ not in Always-Apply Rule 4's ratified set).

## 2026-07-19 — B-2: owner ratification pass (docs)
Owner attended and ratified the pending Stage 3/5 decisions, all as documented
— no value moved, so no risk.yaml/CLAUDE.md edit and no paper ack invalidated.
Ratified: OD-8 ($1,000 bankroll), OD-9 (λ=0.5, k=0.25 — kept as the pre-
calibration defaults; future changes still gated by the OD-9 evidence rule),
OD-10 (NYC-first, second city deferred to OD-2 liquidity data), OD-15 (`quote_seen`
kept on OrderIntent), OD-16 (50% portfolio cap), OD-17 (−5% portfolio auto-kill),
OD-18 (localhost/no-auth dashboard), and the D5-1…D5-14 implementation
deviations. Recorded here rather than in the Stage handoffs, which SCHEMAS.md
freezes as historical (their "awaiting ratification" lines stand as the record
of what was pending at the time). risk.yaml already matched CLAUDE.md — verified,
no drift. D5-14 reminder stands: owner still owes a personal
`apacenye ack --strategy W1 --gate paper` before W1 will START.
Tests: 105 passed (docs-only change). Backlog: B-2 closed. ODs: OD-8/9/10/15/16/17/18
ratified. Ratification: — (slate cleared).

## 2026-07-19 — B-3: wire METAR capture into serve (data)
The `metar.py` adapter existed but nothing polled it, so KNYC observations
weren't being recorded — future OD-12 data thrown away every day serve ran.
Added a `capture=` hook to `MetarAdapter.fetch_latest` (mirrors the NWS
adapter) so it writes to the `metar` channel, plus a capture-only
`run_capture(interval_s)` poll loop (default 300s) added to serve's task
gather. No worker consumes this feed yet (W2 is build-blocked on OD-12); it is
pure recording. Non-obvious decision: unlike a consumer's fetch, the loop must
NOT crash on a missed METAR (common on the free feed) — it logs and retries
next tick, where `fetch_latest` itself still raises loudly for a real consumer.
Station reused from W1 config (KNYC is the shared settlement station); interval
is a module constant, not money/strategy-tunable. Verified against live NWS:
fetched KNYC 75.9°F and confirmed the gzip record. Not money-touching — no gate,
sizing, or ack surface changed; safety invariants untouched.
Tests: 109 passed (+4 new: capture-write, no-capture, loud-failure, loop-survives-miss).
Backlog: B-3 closed; B-6 blocker note updated. ODs: — (feeds OD-12, unresolved).
Ratification: — (no risk-relevant value changed).

## 2026-07-20 — B-4: calibration report tooling (platform)
`apacenye calibration --strategy W1 [--since --until --json --backfill-settlements]`
turns the review-calibration skill's steps 2–6 into one audited computation:
provenance/coverage header, sample-size gate (< 100 scoreable rows ⇒ hard
INSUFFICIENT-DATA), Brier model-vs-market on the identical non-null-mid row
set, a p_model decile reliability table, the qualified/traded subset check
with an adverse-selection warning, and a mechanical verdict — all off pure
`domain/calibration.py`. The replay harness's inline Brier now calls the SAME
`brier_score` (a pin test locks its output at 0.1851/0.2809 so the extraction
is behavior-identical), so replay and the report can never drift. SQL stayed
in the ledger (`settled_evaluations`, `evaluation_coverage`,
`unsettled_evaluated_markets`, `market_has_open_position`, mark-only
`mark_market_settled`). D5 precondition verified: the W1 worker always logs
`model_probability`/`market_implied_probability` as P(YES) of the ticker (the
side-relative `p_win` drives the trade decision only, never the shadow log) —
no corruption to fix. D4 backfill marks ONLY positionless markets settled from
the read-only Kalshi API; markets with open positions are listed for attention,
never realized here (realization stays on serve's on_settlement path). The tool
reports only — it never recommends λ/k/σ (OD-9). Not risk-relevant: no
risk.yaml or rule-4 change, no ack invalidation. Verified: full suite green,
CLI exercised against a seeded ledger (text/json/windowing) then the throwaway
ledger removed.
Tests: 160 passed (+55 since B-3; new: test_calibration.py incl. golden-file +
backfill, ledger calibration reads, replay Brier pin). Backlog: B-4 closed,
B4-PLAN.md deleted; B-9/B-13 references de-dangled. ODs: — (feeds OD-9 evidence
loop). Ratification: — (no risk-relevant value changed).

## 2026-07-20 — B-5: ledger + capture backup routine (ops)
`apacenye backup` and a serve-side hourly loop take out-of-tree snapshots of the
SQLite ledger + `data/capture/` into `~/apacenye-backups` (BACKUP_DIR), keeping
the newest BACKUP_RETENTION (24). The ledger is copied with SQLite's ONLINE
backup API — not `cp` — so it stays transactionally consistent while serve holds
the db open in WAL, and the copy is converted out of WAL (`journal_mode=DELETE`)
so each snapshot is one standalone restorable file, no -wal/-shm sidecar to lose.
Each run writes one self-contained dir (`apacenye.sqlite` + `capture/`) so any
single directory restores wholesale; full (not incremental) snapshots are a
deliberate size-S simplicity choice, bounded by retention. The loop follows the
capture rule — a failed backup logs and continues, never taking down trading —
and is tied to orchestrator liveness (interval_s <= 0 disables it). Not
risk-relevant: no risk.yaml or rule-4 change, no ack invalidation. B-15 will
repoint BACKUP_DIR off-box. Verified: full suite green; `apacenye backup`
exercised for real (snapshot written, capture copied, retention prune to 1
confirmed) and LIVE still refuses to boot.
Tests: 162 passed (+6 new: online-backup consistency under an open WAL db,
no-db/no-capture, retention keep-newest-N + retention-0, loop-survives-failure,
loop-writes-then-prunes). Backlog: B-5 closed. ODs: —. Ratification: —.
