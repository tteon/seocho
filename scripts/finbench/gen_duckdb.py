#!/usr/bin/env python3
"""Deterministic DuckDB synthetic FinBench-style financial graph generator.

Workstream B1. Produces an AML/financial graph that models the LDBC FinBench
schema (Person / Company / Account / Loan + transfer/own/deposit/repay edges)
without the Spark-based FinBench datagen. Two properties make it useful for the
scalability + agent-middleware showcase:

* **Scale is a parameter.** ``--sf`` (scale factor) multiplies row counts, so the
  same generator emits SF1 -> SF10 for scaling curves.
* **Patterns are planted.** Money-laundering cycles, fan-in/fan-out smurfing, and
  a flagged account with a known N-hop transfer neighborhood are injected with
  reserved IDs, so their *gold answers* are known exactly (written to gold.json).
  That lets us score agent-generated Cypher against ground truth.

Output layout (GraphAr-convention aligned, ADR-0149 Parquet):

    outputs/finbench/sf{SF}/
      nodes/{Label}.parquet         # Account, Person, Company, Loan
      edges/{type}.parquet          # transfer, own, deposit, repay
      manifest.json                 # counts, seed, checksums
      gold.json                     # planted-pattern ground truth

Deterministic: fixed seed -> byte-identical row counts and planted patterns.

Usage:
    python scripts/finbench/gen_duckdb.py --sf 1 --out outputs/finbench
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

# Reserved ID band for planted patterns — kept far above random account ids so a
# scale bump never collides with the ground-truth accounts.
PLANT_BASE = 9_000_000
CYCLE3 = [PLANT_BASE + 1, PLANT_BASE + 2, PLANT_BASE + 3]
CYCLE5 = [PLANT_BASE + 10 + i for i in range(5)]
FANIN_HUB = PLANT_BASE + 100
# Enough senders that the aggregate crosses the reporting threshold while every individual
# deposit stays inside the ordinary amount range. With legs averaging ~25,000 that needs a few
# hundred, not 25: at 25 the total came to 599,350 — a sixteenth of the threshold — so the
# typology's defining property ("each unreportable, the aggregate reportable") was absent.
# That property was previously satisfied by pricing each deposit at ~9.97M, which is also what
# made a one-line amount filter find them all, so fixing the giveaway removed the property and
# both had to be repaired together.
#
# Seated at offset 1000+ because 100..760 is fully allocated to the other typologies.
FANIN_SMURFS = [PLANT_BASE + 1000 + i for i in range(420)]
FLAGGED = PLANT_BASE + 200
# Flagged account's known transfer neighborhood by hop distance.
HOP1 = [PLANT_BASE + 201, PLANT_BASE + 202]
HOP2 = [PLANT_BASE + 203]
HOP3 = [PLANT_BASE + 204]
# Funnel account: where the collected structuring proceeds are wired onward.
FUNNEL_OUT = PLANT_BASE + 300
# Rapid pass-through triple: money enters and leaves within hours.
PASSTHRU_IN, PASSTHRU, PASSTHRU_OUT = PLANT_BASE + 401, PLANT_BASE + 402, PLANT_BASE + 403

# Loan-repayment laundering: proceeds arrive, are used to repay a loan, and the credit
# facility disburses to a different account, which moves the funds on. FATF describes
# lent funds being "repaid from the proceeds of crime" as layering/integration, and
# FFIEC flags loans that "tend to obscure the movement of funds" or are "paid on behalf
# of a third party". The point for this experiment is structural: the value path crosses
# FOUR different edge types (TRANSFER -> REPAY -> DEPOSIT -> TRANSFER), which is the
# case a single homogeneous edge table cannot recurse over without first being unioned
# into one.
LOAN_ORIGIN = PLANT_BASE + 501       # where the proceeds arrive
LOAN_REPAYER = PLANT_BASE + 502      # account that repays the loan
LOAN_ID = PLANT_BASE + 900_001       # the credit facility itself
LOAN_BENEFICIARY = PLANT_BASE + 503  # account the loan disburses to
LOAN_FINAL = PLANT_BASE + 504        # onward destination
LOAN_OWNER_PERSON = 7               # an existing Person id, so OWN starts the chain

# Nominee accounts (차명계좌 / 대포통장). One beneficial owner controls a fan of accounts,
# each receiving amounts drawn from the *ordinary* transfer distribution, whose per-owner
# aggregate crosses the reporting threshold. Two things make this the hardest pattern here:
#
# * Detection has to aggregate by *owner*, not by account. Per account nothing is unusual —
#   that is the entire design of the scheme — so a rule that never leaves the account layer
#   cannot see it at any scale. It needs the OWN edge, which is why this pattern was
#   unexpressible until the party layer existed.
# * The amounts are indistinguishable one at a time. The earlier structuring plant used
#   `CTR_THRESHOLD - 1000*i`, i.e. ~9.97M against an innocent population capped at 50,010:
#   measured, a single `amount > 1000000` filter separated every planted edge with 100%
#   precision. That made the pattern detectable without looking at structure at all, so any
#   detection score on it was measuring the giveaway rather than the detector.
NOMINEE_OWNER = 21                    # existing Person id, so OWN resolves to a real party
# Sized so the per-owner aggregate crosses the reporting threshold while every individual
# leg stays inside the innocent amount range. With legs averaging ~25,000 that needs a few
# hundred of them, which is what a real 대포통장 ring looks like: dozens of accounts, each
# taking many small deposits. An earlier attempt used 8 accounts and aggregated to 826,905 —
# a twelfth of the threshold — so the typology's defining property simply was not there.
NOMINEE_ACCTS = [PLANT_BASE + 700 + i for i in range(24)]
NOMINEE_LEGS_PER_ACCT = 18
NOMINEE_COLLECTOR = PLANT_BASE + 760  # where the fan aggregates

# Integration: laundered proceeds become an equity stake, the third stage after placement
# and layering. FATF treats conversion into legitimate ownership as the stage that makes
# proceeds usable, and it is the natural use of the INVEST edge — the money path ends in a
# company register rather than another account.
INTEGRATION_ACCT = PLANT_BASE + 730
INTEGRATION_OWNER = 22
INTEGRATION_COMPANY_OFFSET = 3        # company index the stake is taken in

# Common control across layers. Two accounts transfer to each other, their owners are
# linked by a guarantee, and both accounts sign in from the same device. Each layer alone
# is unremarkable — people transfer money, people guarantee each other, devices are shared
# in a household — and the conjunction is the signal. FATF and FFIEC both treat common
# control behind nominally unrelated parties as a core concealment pattern, and an FIU
# looks for exactly this overlap.
#
# Structurally this is the case an account-only graph cannot express at all: the path leaves
# the account layer for the party layer and the device layer and comes back. Our earlier
# loan chain crossed four edge types but stayed inside one layer of ownership; this crosses
# three layers, which is what "multi-layer" means.
CTRL_ACCT_A = PLANT_BASE + 601
CTRL_ACCT_B = PLANT_BASE + 602
CTRL_PERSON_A = 11          # existing Person ids, so OWN resolves into the party layer
CTRL_PERSON_B = 12
CTRL_MEDIUM = 0             # the shared login device

# Timeline anchor for planted patterns. Fixed so gold answers stay reproducible;
# AML questions are asked over windows ("within 7 days"), which needs real spacing.
T0 = 1_700_000_000
HOUR = 3_600
DAY = 86_400

# Per-SF base row counts (SF1). Everything scales linearly with sf.
BASE = {"persons": 1000, "companies": 100, "accounts": 2000, "transfers": 10000, "loans": 200,
        # The party and device layers. Sized so a multi-layer path exists without swamping
        # the account layer the scale curve is measured on.
        "mediums": 300, "sign_ins": 4000, "invests": 150, "guarantees": 400, "withdraws": 1500}

# ---------------------------------------------------------------------------
# Transaction channels — the "how was the money moved" dimension.
#
# Real financial graphs are not one homogeneous TRANSFER edge: the channel a
# transfer rides carries most of the AML signal, because channels differ in
# traceability, limits, and supervision. Sourced from:
#
# * 전자금융거래법 §2 (전자금융거래/전자적 장치/접근매체) — the statutory channel
#   inventory in Korea: 인터넷뱅킹, 폰(텔레)뱅킹, 모바일뱅킹, CD/ATM, 자동이체.
# * FATF (Professional Money Laundering 2018; ML using New Payment Methods 2010;
#   RBA for Money or Value Transfer Services) — coordinated ATM withdrawal by
#   money mules, immediate onward wiring of cheque/money-order deposits,
#   cross-border wires, hawala/MVTS settling outside the banking system, and
#   "wider geographic reach ⇒ higher ML/TF risk" for new payment methods.
# * FFIEC BSA/AML red flags & 금융감독원 — structuring under reporting
#   thresholds, funnel/대포통장 (nominee) accounts, 오픈뱅킹-enabled fast hops.
#
# FIBO note: FIBO's payment module (FND/ProductsAndServices/PaymentsAndSchedules)
# defines only Payment / Payer / Payee / PaymentEvent / PaymentObligation /
# PaymentSchedule — there is no PaymentMethod or channel taxonomy upstream. So
# channels anchor on fibo:Payment and are declared as a local extension, which is
# exactly the "anchor to the standard, declare locally when no term exists" rule.
#
# risk_weight is an ordinal AML risk hint (1 low … 5 high), not a probability.
CHANNELS = (
    # (code, label, risk_weight, share)  share = relative frequency
    ("INTERNET_BANKING", "인터넷뱅킹 / internet banking", 2, 24),
    ("MOBILE_BANKING", "모바일뱅킹 / mobile banking", 2, 22),
    ("OPEN_BANKING", "오픈뱅킹 즉시이체 / open-banking instant transfer", 3, 12),
    ("AUTO_DEBIT", "자동이체 / standing order & direct debit", 1, 10),
    ("ATM_CD", "CD/ATM 현금 입출금 / ATM cash in-out", 4, 9),
    ("CARD_PAYMENT", "카드결제 / card payment", 2, 7),
    ("TELE_BANKING", "폰(텔레)뱅킹 / telephone banking", 3, 5),
    ("BRANCH_CASH", "영업점 창구 현금 / over-the-counter cash", 4, 4),
    ("WIRE_CROSSBORDER", "국외 송금 / cross-border wire", 5, 3),
    ("PREPAID_GIFT", "선불·상품권 / prepaid & gift certificate", 4, 2),
    ("VIRTUAL_ASSET", "가상자산 이전 / virtual-asset transfer", 5, 1),
    ("MVTS_HAWALA", "환치기·비공식 송금 / MVTS-hawala", 5, 1),
)
CHANNEL_CODES = [c[0] for c in CHANNELS]
# Channels that laundering patterns prefer (low traceability / high reach).
LAUNDERING_CHANNELS = ("WIRE_CROSSBORDER", "VIRTUAL_ASSET", "MVTS_HAWALA", "ATM_CD")
STRUCTURING_CHANNELS = ("ATM_CD", "BRANCH_CASH", "PREPAID_GIFT")
# 고액 현금거래 보고(CTR) threshold: KRW 10,000,000. Structuring sits just under it.
CTR_THRESHOLD = 10_000_000

# The innocent transfer amount range, named once so planted amounts can be drawn from the
# *same* interval instead of a distinguishable one. Keeping these in sync is the difference
# between a detection benchmark and a lookup benchmark: with planted amounts outside the
# innocent range, `amount > 1000000` isolated every planted edge at 100% precision and no
# structural reasoning was ever required.
INNOCENT_AMOUNT_MIN = 10
INNOCENT_AMOUNT_MAX = 50_010
# Real transfer amounts are heavy-tailed: mostly small, with a thin tail of genuinely large
# legitimate payments. Capping the innocent population at 50,010 made *every* large amount a
# planted one, so `amount > 1000000` isolated planted edges with 100% precision — and for
# typologies where a large amount is intrinsic (a funnel wires the collected total; a loan
# disburses a principal) that could not be fixed by shrinking the plant without making the
# typology itself unrealistic. The fix belongs in the innocent distribution: a small share of
# ordinary transfers is large, so size alone stops being evidence.
INNOCENT_TAIL_SHARE = 0.03      # fraction of ordinary transfers drawn from the tail
INNOCENT_TAIL_MAX = 250_000_000  # above the largest planted amount, so no ceiling tell


def _scaled(sf: int) -> dict[str, int]:
    return {k: v * sf for k, v in BASE.items()}


def _account_ref(n_accounts: int, skew: float) -> str:
    """SQL expression sampling an account id, with a tunable degree tail.

    Uniform sampling (``skew == 1``) gives a *binomial* degree distribution: at SF1000
    it measured mean 10, max 31 — max/mean of 3.1, with no tail at all. Real payment
    graphs are nothing like that. Exchange, settlement, payroll and merchant-acquirer
    accounts are hubs with degrees orders of magnitude above the mean, and AML work
    lives around exactly those nodes.

    That difference is not cosmetic. A hub breaks the mechanism behind this
    experiment's headline result: an index seek still finds a node in O(1), but
    *expanding* it costs O(degree), so per-hop db hits stop being constant and the
    working set stops being a handful of pages. Any conclusion about page cache or
    memory sizing drawn on a degree-less graph is scoped to that graph.

    ``skew > 1`` pushes sampling mass onto low ids. Since
    ``P(id < m) = (m/N)**(1/skew)``, expected degree by rank falls off as
    ``r**(1/skew - 1)`` — a power law — and the top account's expected degree is
    ``edges * (1/N)**(1/skew)``. At SF1000 with skew 3 that is ~159k edges on one
    node, which is the regime a real settlement account sits in.

    ``skew == 1`` emits the original expression verbatim so previously published
    snapshots stay byte-identical.
    """
    if skew == 1.0:
        return f"CAST(floor(random()*{n_accounts}) AS BIGINT)"
    return f"CAST(floor({n_accounts} * pow(random(), {skew})) AS BIGINT)"


def _degree_profile(con: duckdb.DuckDBPyConnection) -> dict:
    """Publish the degree distribution, plus anchors curated by degree band.

    Two reasons this is generated rather than left implicit.

    *It is the axis the reader has to know.* A "no cliff under memory pressure" result
    means one thing on a graph whose max degree is 31 and something else entirely on one
    with a hub. Stating the profile beside the result is the difference between a scoped
    finding and an overclaim.

    *Anchors must be chosen, not stumbled into.* With a heavy tail, runtime for the same
    query template swings by orders of magnitude depending on which account you start
    from, so a single fixed anchor measures that anchor, not the system. LDBC solves this
    with parameter curation — picking bindings whose intermediate result sizes are
    comparable (Gubichev & Boncz, TPCTC 2014). Here we do the honest minimum: expose one
    anchor per degree band so a measurement declares the band it ran in and can sweep it.
    """
    deg = con.execute(
        """
        WITH d AS (
            SELECT src AS id FROM transfer UNION ALL SELECT dst FROM transfer
        ), c AS (
            SELECT id, count(*) AS deg FROM d GROUP BY id
        )
        SELECT count(*), avg(deg), max(deg),
               quantile_disc(deg, 0.5), quantile_disc(deg, 0.99),
               quantile_disc(deg, 0.999), quantile_disc(deg, 0.9999)
        FROM c
        """
    ).fetchone()
    bands = con.execute(
        """
        WITH d AS (
            SELECT src AS id FROM transfer UNION ALL SELECT dst FROM transfer
        ), c AS (
            SELECT id, count(*) AS deg FROM d GROUP BY id
        ), r AS (
            SELECT id, deg, row_number() OVER (ORDER BY deg DESC, id) AS rk,
                   count(*) OVER () AS n
            FROM c
        )
        SELECT 'hub' AS band, id, deg FROM r WHERE rk = 1
        UNION ALL SELECT 'p99.9', id, deg FROM r WHERE rk = CAST(n*0.001 AS BIGINT) + 1
        UNION ALL SELECT 'p99',   id, deg FROM r WHERE rk = CAST(n*0.01  AS BIGINT) + 1
        UNION ALL SELECT 'median',id, deg FROM r WHERE rk = CAST(n*0.5   AS BIGINT) + 1
        ORDER BY deg DESC
        """
    ).fetchall()
    return {
        "measured_on": "transfer edges (in + out)",
        "nodes_with_edges": deg[0],
        "mean": round(float(deg[1]), 3),
        "max": deg[2],
        "p50": deg[3], "p99": deg[4], "p999": deg[5], "p9999": deg[6],
        "max_over_mean": round(deg[2] / float(deg[1]), 2),
        "curated_anchors": [
            {"band": b, "account_id": int(i), "degree": int(d)} for b, i, d in bands
        ],
    }


def _multiplicity_profile(con: duckdb.DuckDBPyConnection) -> dict:
    """Publish edge multiplicity, the other property FinBench names and we omitted.

    FinBench distinguishes itself from the social-network benchmark by "having hub
    vertices with higher degrees **and allowing edge multiplicity**". Uniform pair
    sampling gives neither: measured over 10M edges at SF1000 it produced 14 duplicate
    pairs and a maximum multiplicity of 2, i.e. effectively a simple graph.

    Reporting it matters for a reason beyond fidelity. When redundancy is 1.0000x,
    ``count(dst)`` and ``count(DISTINCT dst)`` return the *same number*, so a question
    about counterparties and a question about transaction volume are indistinguishable and
    an agent that confuses them scores correct anyway. A whole class of semantic error is
    unmeasurable on a graph without multiplicity.
    """
    row = con.execute(
        """
        WITH p AS (SELECT src, dst, count(*) AS m FROM transfer GROUP BY src, dst)
        SELECT (SELECT count(*) FROM transfer), count(*), max(m),
               sum(CASE WHEN m > 1 THEN 1 ELSE 0 END),
               sum(CASE WHEN m > 1 THEN m ELSE 0 END)
        FROM p
        """
    ).fetchone()
    edges, pairs, mx, dup_pairs, dup_edges = row
    return {
        "edges": int(edges),
        "distinct_pairs": int(pairs),
        "redundancy": round(edges / pairs, 4) if pairs else None,
        "max_multiplicity": int(mx),
        "duplicate_pairs": int(dup_pairs or 0),
        "pct_edges_in_duplicate_pairs": round(100.0 * (dup_edges or 0) / edges, 3),
    }


def _build(con: duckdb.DuckDBPyConnection, sf: int, hub_skew: float = 1.0,
           dup_share: float = 0.0, closure_share: float = 0.0,
           cycle_share: float = 0.0) -> dict[str, int]:
    n = _scaled(sf)
    acct = _account_ref(n["accounts"], hub_skew)
    con.execute("SELECT setseed(0.42)")  # deterministic

    # ---- base random nodes ----
    con.execute(
        f"""
        CREATE TABLE person AS
        SELECT i AS id, 'person-' || i AS name,
               CAST(18 + (random()*60) AS INT) AS age,
               (['KR','US','JP','SG','GB'])[1 + CAST(random()*4 AS INT)] AS country
        FROM range({n['persons']}) t(i);
        """
    )
    con.execute(
        f"""
        CREATE TABLE company AS
        SELECT {n['persons']} + i AS id, 'company-' || i AS name,
               (['bank','fintech','trading','holding'])[1 + CAST(random()*3 AS INT)] AS sector
        FROM range({n['companies']}) t(i);
        """
    )
    # Accounts owned by a random person or company.
    owner_max = n["persons"] + n["companies"]
    con.execute(
        f"""
        CREATE TABLE account AS
        SELECT i AS id,
               'acct-' || i AS iban,
               CAST(random()*3 AS INT) AS acct_type,
               CAST(1 + random()*4 AS INT) AS risk_tier,
               false AS flagged,
               CAST(floor(random()*{owner_max}) AS BIGINT) AS owner_id
        FROM range({n['accounts']}) t(i);
        """
    )
    con.execute(
        f"""
        CREATE TABLE loan AS
        SELECT {n['accounts'] * 10} + i AS id,
               CAST(1000 + random()*100000 AS BIGINT) AS principal,
               CAST(1 + random()*10 AS INT) AS term_years
        FROM range({n['loans']}) t(i);
        """
    )

    # ---- channel dimension ----
    # A Channel node per code so the agent can traverse "which channel" as graph
    # structure (and the ontology can bound it), plus channel/risk properties
    # denormalized onto each transfer edge for single-hop filtering.
    channel_rows = ",".join(
        f"('{code}', '{label}', {risk}, {share})" for code, label, risk, share in CHANNELS
    )
    con.execute(
        "CREATE TABLE channel AS SELECT * FROM (VALUES " + channel_rows +
        ") AS t(code, label, risk_weight, share)"
    )
    # Weighted channel pick: expand shares into a lookup band, then sample.
    con.execute(
        """
        CREATE TABLE channel_band AS
        WITH ordered AS (
            SELECT code, risk_weight, share,
                   sum(share) OVER (ORDER BY code) AS hi,
                   sum(share) OVER (ORDER BY code) - share AS lo,
                   sum(share) OVER () AS total
            FROM channel
        )
        SELECT code, risk_weight, lo, hi, total FROM ordered;
        """
    )

    # ---- base random edges ----
    # `dup_share` of the transfer budget is reserved for repeats onto pairs that already
    # exist, rather than added on top, so the edge count stays exactly n['transfers'] and
    # multiplicity does not silently change the size of the graph.
    closure_transfers = int(round(n["transfers"] * closure_share))
    cycle_transfers = int(round(n["transfers"] * cycle_share))
    base_transfers = int(round(
        n["transfers"] * (1.0 - dup_share - closure_share - cycle_share)))
    repeat_transfers = (n["transfers"] - base_transfers - closure_transfers
                        - cycle_transfers)
    con.execute(
        f"""
        CREATE TABLE transfer AS
        WITH raw AS (
            SELECT {acct} AS src,
                   {acct} AS dst,
                   -- Log-uniform tail on a small share, so a large amount is unremarkable.
                   CASE WHEN random() < {INNOCENT_TAIL_SHARE}
                        THEN CAST({INNOCENT_AMOUNT_MAX} * pow(
                             {INNOCENT_TAIL_MAX} / {INNOCENT_AMOUNT_MAX}.0, random()) AS BIGINT)
                        ELSE CAST({INNOCENT_AMOUNT_MIN} + random()*{INNOCENT_AMOUNT_MAX - INNOCENT_AMOUNT_MIN} AS BIGINT)
                   END AS amount,
                   CAST(1700000000 + random()*30000000 AS BIGINT) AS ts,
                   random() AS pick
            FROM range({base_transfers}) t(i)
        )
        SELECT raw.src, raw.dst, raw.amount, raw.ts,
               b.code AS channel, b.risk_weight AS channel_risk,
               false AS cross_border
        FROM raw JOIN channel_band b
          ON raw.pick * b.total >= b.lo AND raw.pick * b.total < b.hi;
        """
    )
    if repeat_transfers > 0:
        # Repeats land on pairs that already exist and carry amounts just under the CTR
        # reporting threshold on cash-like channels. That is not decoration: repeated
        # sub-threshold transfers between the *same* counterparties is structuring, one of
        # the typologies this dataset is supposed to contain, and uniform pair sampling
        # produces essentially none of it (measured max multiplicity 2 over 10M edges).
        #
        # Sampling existing rows rather than re-drawing endpoints is what makes the
        # multiplicity controlled instead of accidental — re-drawing would mostly land on
        # fresh pairs again.
        con.execute(
            f"""
            INSERT INTO transfer
            SELECT src, dst,
                   CAST({CTR_THRESHOLD} - 1000 - random()*500000 AS BIGINT) AS amount,
                   CAST(ts + 3600 + random()*2592000 AS BIGINT) AS ts,
                   channel, channel_risk, cross_border
            FROM (SELECT src, dst, ts, channel, channel_risk, cross_border
                  FROM transfer USING SAMPLE {repeat_transfers} ROWS) s;
            """
        )
    if closure_transfers > 0:
        # Triadic closure. Uniform and preferential attachment both produce graphs with
        # essentially no triangles — measured average local clustering of *exactly 0.000*
        # over a 3,000-node sample at SF1000, against 0.1-0.5 in real payment and social
        # networks. That is not only a fidelity gap: with no incidental cycles, a
        # cycle-detection scenario has nothing to discriminate against. The planted
        # laundering ring is then the only ring in the graph, so the question measures
        # recall and receives precision for free, and the unanchored question an analyst
        # actually asks ("which rings here are suspicious") cannot be posed at all.
        #
        # Closure edges complete an existing two-path a->b->c with a->c, which is what
        # triadic closure means and yields both directed 3-cycles and undirected triangles.
        # The second hop is chosen by a hash-derived rank rather than a fixed one so the
        # closures are varied yet reproducible, and the join is bounded by the sample size
        # rather than by the full two-path product — the unbounded version of this join is
        # what exhausted a 10 GB limit on the power-law graph.
        con.execute(
            f"""
            CREATE TEMP TABLE _closure_seed AS
            SELECT src, dst, ts, channel, channel_risk, cross_border
            FROM transfer USING SAMPLE {closure_transfers} ROWS;

            CREATE TEMP TABLE _ranked AS
            SELECT src, dst,
                   row_number() OVER (PARTITION BY src ORDER BY hash(src * 1000003 + dst))
                     AS rn,
                   count(*) OVER (PARTITION BY src) AS od
            FROM (SELECT DISTINCT src, dst FROM transfer WHERE src <> dst) d;

            INSERT INTO transfer
            SELECT s.src, r.dst,
                   CAST(10 + random()*50000 AS BIGINT) AS amount,
                   CAST(s.ts + 600 + random()*604800 AS BIGINT) AS ts,
                   s.channel, s.channel_risk, s.cross_border
            FROM _closure_seed s
            JOIN _ranked r
              ON r.src = s.dst
             AND r.rn = 1 + (hash(s.src) % r.od)
            WHERE r.dst <> s.src;
            """
        )
    if cycle_transfers > 0:
        # Cyclic closure, which is a *different* property from the transitive kind above
        # and the one laundering-ring detection actually needs. Completing a->b->c with
        # a->c raises the clustering coefficient — measured 0.000 to 0.175 at
        # closure_share 0.10 — but creates no directed cycle, because a cycle requires
        # c->a. Measured directly: transitive closure moved undirected triangles from
        # none to 77% of sampled nodes while directed 3-cycles stayed in the same order of
        # magnitude (47 to 110). Two structural properties, two scenario families, and
        # conflating them would have left the ring scenario without distractors while the
        # clustering number looked healthy.
        #
        # The laundering channels are used deliberately: a ring that rides
        # low-traceability channels is the plausible distractor, not a random one.
        launder = ", ".join(f"'{c}'" for c in LAUNDERING_CHANNELS)
        con.execute(
            f"""
            CREATE TEMP TABLE _cycle_seed AS
            SELECT src, dst, ts FROM transfer USING SAMPLE {cycle_transfers} ROWS;

            INSERT INTO transfer
            SELECT r.dst AS src, s.src AS dst,
                   CAST(10 + random()*50000 AS BIGINT) AS amount,
                   CAST(s.ts + 1200 + random()*604800 AS BIGINT) AS ts,
                   c.code, c.risk_weight,
                   c.code IN ('WIRE_CROSSBORDER','VIRTUAL_ASSET','MVTS_HAWALA')
            FROM _cycle_seed s
            JOIN _ranked r
              ON r.src = s.dst
             AND r.rn = 1 + (hash(s.dst) % r.od)
            JOIN channel c
              ON c.code = ([{launder}])[1 + CAST(hash(s.src) % {len(LAUNDERING_CHANNELS)} AS INT)]
            WHERE r.dst <> s.src;
            """
        )
        # The join drops seeds whose second hop resolved back to the origin, so the edge
        # count can land under budget. Top up from the base distribution rather than
        # forcing the closure count, so "how many edges" stays a stated parameter.
    if closure_transfers > 0 or cycle_transfers > 0:
        shortfall = n["transfers"] - con.execute(
            "SELECT count(*) FROM transfer").fetchone()[0]
        if shortfall > 0:
            con.execute(
                f"""
                INSERT INTO transfer
                WITH raw AS (
                    SELECT {acct} AS src, {acct} AS dst,
                           CAST(10 + random()*50000 AS BIGINT) AS amount,
                           CAST(1700000000 + random()*30000000 AS BIGINT) AS ts,
                           random() AS pick
                    FROM range({shortfall}) t(i)
                )
                SELECT raw.src, raw.dst, raw.amount, raw.ts,
                       b.code, b.risk_weight, false
                FROM raw JOIN channel_band b
                  ON raw.pick * b.total >= b.lo AND raw.pick * b.total < b.hi;
                """
            )
    # Cross-border flag follows the channel semantics (wire/VA/hawala reach abroad).
    con.execute(
        "UPDATE transfer SET cross_border = true "
        "WHERE channel IN ('WIRE_CROSSBORDER','VIRTUAL_ASSET','MVTS_HAWALA')"
    )
    con.execute(
        "CREATE TABLE own AS SELECT owner_id AS src, id AS dst FROM account;"
    )
    con.execute(
        f"""
        CREATE TABLE deposit AS
        SELECT id AS src, {acct} AS dst,
               principal AS amount
        FROM loan;
        """
    )
    con.execute(
        f"""
        CREATE TABLE repay AS
        SELECT {acct} AS src, id AS dst,
               CAST(principal/term_years AS BIGINT) AS amount
        FROM loan;
        """
    )

    # ---- the party and device layers ----
    #
    # Everything above is the account layer, and it was the whole graph until now. Four of
    # FinBench's nine edge types were modelled and five were not, which meant a question could
    # never leave the accounts: the schema had no way to express "these two accounts are
    # controlled by parties that guarantee each other", which is where real financial
    # investigation spends most of its time. The missing edges are not decoration — they are
    # what makes a query *multi-layer*.
    #
    # Names, endpoints and properties follow the FinBench specification rather than being
    # invented, so the correspondence stays checkable:
    #
    #   apply      Person/Company -> Loan            timestamp, organization
    #   invest     Person/Company -> Company         timestamp, ratio
    #   guarantee  Person/Company -> Person/Company  timestamp, relationship
    #   withdraw   Account -> Account                timestamp, amount
    #   signIn     Medium -> Account                 timestamp, location
    #
    # `Medium` is FinBench's login device, and it is a *different* concept from the `Channel`
    # node this generator already had. Channel is a payment rail (전자금융거래법 §2 + FATF);
    # Medium is the device a session came from. Conflating them would have lost the strongest
    # multi-layer signal there is — two accounts sharing a login device — so both exist.
    owner_max = n["persons"] + n["companies"]
    con.execute(
        f"""
        CREATE TABLE medium AS
        SELECT i AS id,
               (['mobile_app','web_browser','atm_terminal','pos_terminal'])[1 + CAST(random()*3 AS INT)] AS type,
               CAST(1 + random()*4 AS INT) AS risk_level,
               false AS is_blocked
        FROM range({n['mediums']}) t(i);
        """
    )
    # A device is shared by a handful of accounts. That sharing is the point: an account-only
    # graph cannot express "same device, different owners", which is a first-order red flag.
    con.execute(
        f"""
        CREATE TABLE sign_in AS
        SELECT CAST(floor(random()*{n['mediums']}) AS BIGINT) AS src,
               {acct} AS dst,
               CAST({T0} + random()*30000000 AS BIGINT) AS ts,
               (['KR','US','JP','SG','GB'])[1 + CAST(random()*4 AS INT)] AS location
        FROM range({n['sign_ins']}) t(i);
        """
    )
    # Every loan now has an applicant, so the loan layer connects to the party layer. Without
    # it a loan floated free and the loan-repayment typology could not be traced to a person.
    con.execute(
        f"""
        CREATE TABLE apply AS
        SELECT CAST(floor(random()*{owner_max}) AS BIGINT) AS src,
               id AS dst,
               CAST({T0} - random()*10000000 AS BIGINT) AS ts,
               (['retail_bank','online_lender','credit_union','p2p_platform'])[1 + CAST(random()*3 AS INT)] AS organization
        FROM loan;
        """
    )
    # Corporate ownership. Company ids start at n['persons'], which is how `own` already
    # resolves an owner to either a Person or a Company.
    con.execute(
        f"""
        CREATE TABLE invest AS
        SELECT CAST(floor(random()*{owner_max}) AS BIGINT) AS src,
               {n['persons']} + CAST(floor(random()*{n['companies']}) AS BIGINT) AS dst,
               CAST({T0} - random()*20000000 AS BIGINT) AS ts,
               round(random(), 3) AS ratio
        FROM range({n['invests']}) t(i);
        """
    )
    con.execute(
        f"""
        CREATE TABLE guarantee AS
        SELECT CAST(floor(random()*{owner_max}) AS BIGINT) AS src,
               CAST(floor(random()*{owner_max}) AS BIGINT) AS dst,
               CAST({T0} - random()*20000000 AS BIGINT) AS ts,
               (['family','business_partner','employer','unrelated'])[1 + CAST(random()*3 AS INT)] AS relationship
        FROM range({n['guarantees']}) t(i);
        """
    )
    # Cash withdrawal, kept separate from transfer because FinBench does: a withdrawal is
    # money leaving the traceable system, which is the terminal step of most typologies and
    # therefore a different edge to reason about.
    con.execute(
        f"""
        CREATE TABLE withdraw AS
        SELECT {acct} AS src, {acct} AS dst,
               CAST(10 + random()*50000 AS BIGINT) AS amount,
               CAST({T0} + random()*30000000 AS BIGINT) AS ts
        FROM range({n['withdraws']}) t(i);
        """
    )
    con.execute("DELETE FROM guarantee WHERE src = dst")
    con.execute("DELETE FROM invest WHERE src = dst")

    _plant(con)
    # Channel usage edges: Account -[USES_CHANNEL]-> Channel. This lifts the
    # channel from a mere edge property into graph structure, so the schema's
    # relationship cardinality — not just its row count — grows with the domain.
    con.execute(
        """
        CREATE TABLE uses_channel AS
        -- GROUP BY + ORDER BY (not DISTINCT + window) so the row order — and thus
        -- the Parquet checksum — is deterministic.
        SELECT src, channel AS dst, count(*) AS tx_count
        FROM transfer
        GROUP BY src, channel
        ORDER BY src, channel;
        """
    )
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("person", "company", "account", "loan", "channel", "medium",
                      "transfer", "own", "deposit", "repay", "uses_channel",
                      "apply", "invest", "guarantee", "withdraw", "sign_in")}


def _plant(con: duckdb.DuckDBPyConnection) -> None:
    """Inject ground-truth AML patterns with reserved IDs."""
    planted_accounts = (CYCLE3 + CYCLE5 + [FANIN_HUB] + FANIN_SMURFS + [FLAGGED]
                        + HOP1 + HOP2 + HOP3 + [FUNNEL_OUT]
                        + [PASSTHRU_IN, PASSTHRU, PASSTHRU_OUT]
                        + [LOAN_ORIGIN, LOAN_REPAYER, LOAN_BENEFICIARY, LOAN_FINAL]
                        + [CTRL_ACCT_A, CTRL_ACCT_B]
                        + NOMINEE_ACCTS + [NOMINEE_COLLECTOR, INTEGRATION_ACCT])
    values = ",".join(
        f"({a}, 'plant-{a}', 0, 5, {'true' if a == FLAGGED else 'false'}, 0)"
        for a in planted_accounts
    )
    con.execute(
        "INSERT INTO account (id, iban, acct_type, risk_tier, flagged, owner_id) VALUES " + values
    )

    # Planted edges carry channel AND time semantics, not just topology. Real AML
    # detection is temporal — FinCEN defines structuring as transactions "on one or
    # more days for the purpose of evading" the reporting threshold, and rule-based
    # monitoring looks for repetition inside a window (the canonical example being
    # repeated sub-threshold deposits within about two weeks). A pattern with no time
    # structure cannot express that, so each typology gets a realistic timeline.
    edges: list[tuple[int, int, int, str, int]] = []  # (src, dst, amount, channel, ts)

    # Laundering rings: sequential hops hours apart, so the ring is traversable in
    # time order rather than being simultaneous (which no real chain is).
    for cyc in (CYCLE3, CYCLE5):
        for i in range(len(cyc)):
            channel = LAUNDERING_CHANNELS[i % len(LAUNDERING_CHANNELS)]
            edges.append((cyc[i], cyc[(i + 1) % len(cyc)], 99_999, channel,
                          T0 + i * 6 * HOUR))

    # Structuring / smurfing: each deposit sits just under the CTR threshold and the
    # set falls inside a 7-day window, so the aggregate crosses the threshold the
    # individual transactions were shaped to avoid. That aggregate-versus-individual
    # gap is the whole point of the typology.
    for idx, s in enumerate(FANIN_SMURFS):
        channel = STRUCTURING_CHANNELS[idx % len(STRUCTURING_CHANNELS)]
        # Drawn from the innocent interval, not from just under the threshold. The old
        # value (CTR_THRESHOLD - 1000*idx, ~9.97M against an innocent maximum of 50,010)
        # meant a one-line amount filter found every planted edge with 100% precision, so
        # the typology was detectable without looking at its structure at all. What makes
        # it suspicious is the *count* of sub-threshold senders inside a window, which is
        # what FinCEN and FFIEC actually describe.
        amount = INNOCENT_AMOUNT_MIN + (idx * 1_997) % (INNOCENT_AMOUNT_MAX - INNOCENT_AMOUNT_MIN)
        # Spread across the declared 7-day window regardless of how many senders there are.
        # The previous form advanced 3 hours per group of seven, which was fine for 25 senders
        # and stretched the span to 13.4 days at 420 — silently breaking the window the
        # typology is defined by. Sub-day spacing keeps the span inside 7 days at any count.
        edges.append((s, FANIN_HUB, amount, channel,
                      T0 + (idx % 7) * DAY + (idx // 7) * 20 * 60))

    # Funnel account: collected funds leave shortly after the last deposit arrives —
    # FATF describes small incoming transfers being "almost immediately wired to
    # another city or country". The 4-hour gap is what makes it a pass-through rather
    # than an account that merely receives money. Derived from the deposit timeline
    # rather than hardcoded, so the onward leg cannot land *before* the last deposit
    # when the smurf schedule changes.
    last_deposit_ts = max(ts for _s, dst, _a, _c, ts in edges if dst == FANIN_HUB)
    edges.append((FANIN_HUB, FUNNEL_OUT, CTR_THRESHOLD * 20, "WIRE_CROSSBORDER",
                  last_deposit_ts + 4 * HOUR))

    # The flagged account's chain, one hop per day so hop distance and elapsed time
    # agree and a "within N days" question is answerable.
    edges += [
        (FLAGGED, HOP1[0], 99_999, "WIRE_CROSSBORDER", T0 + 1 * DAY),
        (FLAGGED, HOP1[1], 99_999, "VIRTUAL_ASSET", T0 + 1 * DAY + 2 * HOUR),
        (HOP1[0], HOP2[0], 99_999, "MVTS_HAWALA", T0 + 2 * DAY),
        (HOP2[0], HOP3[0], 99_999, "ATM_CD", T0 + 3 * DAY),
    ]

    # Rapid pass-through: in and out inside two hours, the velocity signal that
    # separates a conduit from a normal balance-holding account.
    edges += [
        (PASSTHRU_IN, PASSTHRU, CTR_THRESHOLD * 5, "WIRE_CROSSBORDER", T0 + 10 * DAY),
        (PASSTHRU, PASSTHRU_OUT, CTR_THRESHOLD * 5 - 50_000, "VIRTUAL_ASSET",
         T0 + 10 * DAY + 2 * HOUR),
    ]

    # Loan-repayment laundering: the value path deliberately crosses edge types.
    # Only the two TRANSFER legs belong in the transfer table; the repay and deposit
    # legs are inserted into their own tables below, which is exactly what makes this
    # path un-recursable over a single table.
    edges += [
        (LOAN_ORIGIN, LOAN_REPAYER, CTR_THRESHOLD * 3, "WIRE_CROSSBORDER", T0 + 20 * DAY),
        (LOAN_BENEFICIARY, LOAN_FINAL, CTR_THRESHOLD * 3 - 200_000, "VIRTUAL_ASSET",
         T0 + 20 * DAY + 3 * DAY),
    ]

    risk = {code: rw for code, _l, rw, _s in CHANNELS}
    cross = {"WIRE_CROSSBORDER", "VIRTUAL_ASSET", "MVTS_HAWALA"}
    tvalues = ",".join(
        f"({s}, {d}, {amt}, {ts}, '{ch}', {risk[ch]}, {str(ch in cross).lower()})"
        for s, d, amt, ch, ts in edges
    )
    con.execute(
        "INSERT INTO transfer (src, dst, amount, ts, channel, channel_risk, cross_border) "
        "VALUES " + tvalues
    )

    # The heterogeneous legs of the loan-repayment chain. Each lives in its own table,
    # so a query following the whole path has to cross TRANSFER -> REPAY -> DEPOSIT ->
    # TRANSFER. That is the structural difference a channel column cannot capture.
    con.execute(
        f"INSERT INTO loan (id, principal, term_years) "
        f"VALUES ({LOAN_ID}, {CTR_THRESHOLD * 3}, 5)"
    )
    # Repayment made from the account that received the proceeds.
    con.execute(
        f"INSERT INTO repay (src, dst, amount) VALUES ({LOAN_REPAYER}, {LOAN_ID}, {CTR_THRESHOLD * 3})"
    )
    # The facility then disburses to a different account — FFIEC's "paid on behalf of a
    # third party" / "obscures the movement of funds".
    con.execute(
        f"INSERT INTO deposit (src, dst, amount) VALUES ({LOAN_ID}, {LOAN_BENEFICIARY}, {CTR_THRESHOLD * 3})"
    )
    # Ownership anchors the chain to a named person, so the question can start from a
    # human rather than an account number.
    con.execute(f"INSERT INTO own (src, dst) VALUES ({LOAN_OWNER_PERSON}, {LOAN_ORIGIN})")

    # ---- nominee-account structuring (차명계좌) ----
    #
    # Amounts come from the innocent interval, so no amount filter can separate a single
    # planted transfer from ordinary traffic. What separates the pattern is the *aggregate
    # per beneficial owner*, which only a query that traverses OWN can compute. Spread over
    # nine days so a naive "same day" window also misses it.
    amt = (f"CAST({INNOCENT_AMOUNT_MIN} + random()*"
           f"{INNOCENT_AMOUNT_MAX - INNOCENT_AMOUNT_MIN} AS BIGINT)")
    for i, acct in enumerate(NOMINEE_ACCTS):
        con.execute(f"INSERT INTO own (src, dst) VALUES ({NOMINEE_OWNER}, {acct})")
        # Many small inbound legs per nominee from ordinary accounts, spread over 18 days so
        # a same-day window misses it, then one forward to the collector.
        con.execute(
            f"""
            INSERT INTO transfer (src, dst, amount, ts, channel, channel_risk, cross_border)
            -- Channel drawn from the declared mix, not pinned to one rail. Pinning 432 legs
            -- to MOBILE_BANKING moved its share 3.2 points off the declared 22% and, worse,
            -- handed a detector a tell unrelated to the typology: the channel is not what
            -- makes nominee structuring suspicious. `pick` is materialised once per row —
            -- calling random() inside the band comparison evaluates it twice and can match
            -- zero bands or several.
            WITH legs AS (
                SELECT g.i AS i, random() AS pick FROM range({NOMINEE_LEGS_PER_ACCT}) g(i)
            )
            SELECT 1 + ((({i} * 37) + legs.i * 11) % 900), {acct}, {amt},
                   {T0} + (legs.i % {NOMINEE_LEGS_PER_ACCT}) * {DAY}
                        + CAST(random()*{12 * HOUR} AS BIGINT),
                   b.code, b.risk_weight, false
            FROM legs JOIN channel_band b
              ON legs.pick * b.total >= b.lo AND legs.pick * b.total < b.hi
            """
        )
        con.execute(
            f"""
            INSERT INTO transfer (src, dst, amount, ts, channel, channel_risk, cross_border)
            VALUES ({acct}, {NOMINEE_COLLECTOR}, {amt},
                    {T0} + {NOMINEE_LEGS_PER_ACCT} * {DAY} + {i} * {HOUR},
                    'MOBILE_BANKING', 2, false)
            """
        )
    con.execute(f"INSERT INTO own (src, dst) VALUES ({NOMINEE_OWNER}, {NOMINEE_COLLECTOR})")

    # ---- integration: proceeds become an equity stake ----
    #
    # The money path leaves the account layer for the company register. A detector has to
    # follow TRANSFER -> OWN -> INVEST, which no amount rule and no account-only traversal
    # can do. The stake ratio is deliberately ordinary.
    con.execute(f"INSERT INTO own (src, dst) VALUES ({INTEGRATION_OWNER}, {INTEGRATION_ACCT})")
    con.execute(
        f"""
        INSERT INTO transfer (src, dst, amount, ts, channel, channel_risk, cross_border)
        VALUES ({NOMINEE_COLLECTOR}, {INTEGRATION_ACCT},
                CAST({INNOCENT_AMOUNT_MIN} + random()*{INNOCENT_AMOUNT_MAX - INNOCENT_AMOUNT_MIN} AS BIGINT),
                {T0} + 10 * {DAY}, 'INTERNET_BANKING', 2, false)
        """
    )
    con.execute(
        f"""
        INSERT INTO invest (src, dst, ts, ratio)
        SELECT {INTEGRATION_OWNER},
               (SELECT min(id) + {INTEGRATION_COMPANY_OFFSET} FROM company),
               {T0} + 12 * {DAY}, 0.34
        """
    )

    # ---- common control across three layers ----
    #
    # Deliberately unremarkable one layer at a time. Two accounts transfer between
    # themselves at an ordinary amount over an ordinary channel; two people guarantee each
    # other, which families and business partners do; one device signs into both accounts,
    # which happens in a household. Only the conjunction is a finding, and only a query that
    # leaves the account layer can see it.
    #
    # This is the gap the schema comparison exposed: four of FinBench's nine edge types were
    # modelled, and the five missing ones were all that connected accounts to the parties
    # behind them. A question about common control could not be *expressed*, so it was never
    # measured, and the experiment quietly had no multi-layer case at all.
    con.execute(
        f"""
        INSERT INTO transfer (src, dst, amount, ts, channel, channel_risk, cross_border)
        VALUES ({CTRL_ACCT_A}, {CTRL_ACCT_B}, 4_250_000, {T0 + 3 * DAY},
                'MOBILE_BANKING', 2, false)
        """
    )
    # Party layer: each account has a named owner, and the owners guarantee each other.
    con.execute(f"INSERT INTO own (src, dst) VALUES ({CTRL_PERSON_A}, {CTRL_ACCT_A})")
    con.execute(f"INSERT INTO own (src, dst) VALUES ({CTRL_PERSON_B}, {CTRL_ACCT_B})")
    con.execute(
        f"""
        INSERT INTO guarantee (src, dst, ts, relationship)
        VALUES ({CTRL_PERSON_A}, {CTRL_PERSON_B}, {T0 - 40 * DAY}, 'business_partner')
        """
    )
    # Device layer: one Medium signs into both accounts, hours apart. Shared-device access by
    # nominally unrelated parties is the overlap an FIU looks for.
    for acct, offset in ((CTRL_ACCT_A, 2 * HOUR), (CTRL_ACCT_B, 5 * HOUR)):
        con.execute(
            f"""
            INSERT INTO sign_in (src, dst, ts, location)
            VALUES ({CTRL_MEDIUM}, {acct}, {T0 + 3 * DAY + offset}, 'KR')
            """
        )


def _gold() -> dict:
    """Ground truth for the B4 showcase scenarios."""
    return {
        "flagged_account": FLAGGED,
        "scenario_7_common_control": {
            "description": (
                "two accounts transferring between themselves whose owners guarantee each "
                "other and which share a login device — each layer is ordinary alone, the "
                "conjunction is the finding"),
            "accounts": [CTRL_ACCT_A, CTRL_ACCT_B],
            "owners": [CTRL_PERSON_A, CTRL_PERSON_B],
            "shared_medium": CTRL_MEDIUM,
            "layers_traversed": ["transfer (account)", "own + guarantee (party)",
                                 "signIn (device)"],
            "edge_types": ["TRANSFER", "OWN", "GUARANTEE", "SIGN_IN"],
            "source": (
                "FATF and FFIEC BSA/AML both treat common control behind nominally "
                "unrelated parties as a core concealment pattern"),
        },
        "scenario_1_nhop_from_flagged": {
            "description": "transfer-reachable accounts from the flagged account by hop",
            "hop1": HOP1, "hop2": HOP2, "hop3": HOP3,
            "within_3_hops": sorted(HOP1 + HOP2 + HOP3),
        },
        "scenario_5_laundering_cycles": {
            "description": "planted transfer cycles A->B->...->A",
            "cycles": [CYCLE3, CYCLE5],
        },
        "scenario_5_fan_in_smurfing": {
            "description": "one hub receiving from many distinct senders",
            "hub": FANIN_HUB, "senders": FANIN_SMURFS, "sender_count": len(FANIN_SMURFS),
        },
        # Detection-oriented ground truth. The typologies block below describes each
        # pattern; this block states, for each, the *exact answer set* and the population a
        # detector has to separate it from. Both halves are needed: an answer set alone
        # measures recall, and recall was the only thing this experiment could measure while
        # every question named its own anchor.
        "detection": {
            "nominee_structuring": {
                "answer": {"owner": NOMINEE_OWNER,
                           "accounts": NOMINEE_ACCTS,
                           "collector": NOMINEE_COLLECTOR},
                "requires": "aggregate over OWN — invisible per account",
                "why_hard": (
                    "every leg is drawn from the ordinary amount distribution and the "
                    "per-account total stays under the threshold, so the pattern exists only "
                    "in the per-owner aggregate. Ranking owners by total *misses* it: the "
                    "planted owner sat 100th of 103 owners above the threshold, because "
                    "innocent owners reach hundreds of millions on a handful of large "
                    "legitimate transfers while this ring reaches twelve million on 468 "
                    "small ones. The signal is count and account-spread, not size."),
                "source": ("FinCEN — transactions 'on one or more days for the purpose of "
                           "evading' the reporting threshold; 대포통장 / nominee-account "
                           "rings in 금융감독원 guidance"),
            },
            "layering_cycle": {
                "answer": {"cycles": [CYCLE3, CYCLE5]},
                "requires": "cycle detection with channel and time-window predicates",
                "why_hard": (
                    "on a power-law graph with triadic closure the same graph carries ~694,703 "
                    "incidental 3-cycles, so precision is the whole difficulty. On a uniform "
                    "graph there were 47 and precision came free."),
                "source": "FATF layering typology — funds returning to origin",
            },
            "funnel_account": {
                "answer": {"hub": FANIN_HUB, "senders": FANIN_SMURFS,
                           "onward": FUNNEL_OUT},
                "requires": "collection window followed by prompt onward movement",
                "why_hard": ("collection alone is ordinary for a merchant account; the "
                             "signal is the elapsed time to the onward wire."),
                "source": ("FATF Professional Money Laundering (2018) — small incoming "
                           "transfers 'almost immediately wired to another city or country'"),
            },
            "loan_integration": {
                "answer": {"path": [LOAN_ORIGIN, LOAN_REPAYER, LOAN_ID,
                                    LOAN_BENEFICIARY, LOAN_FINAL],
                           "edge_types": ["TRANSFER", "REPAY", "DEPOSIT", "TRANSFER"]},
                "requires": "a value path across four heterogeneous edge types",
                "why_hard": ("no single edge table can recurse over it without first being "
                             "unioned; each hop alone is a normal loan operation."),
                "source": ("FATF — lent funds 'repaid from the proceeds of crime'; FFIEC on "
                           "loans that obscure the movement of funds"),
            },
            "common_control": {
                "answer": {"accounts": [CTRL_ACCT_A, CTRL_ACCT_B],
                           "owners": [CTRL_PERSON_A, CTRL_PERSON_B],
                           "shared_medium": CTRL_MEDIUM},
                "requires": "conjunction across account, party and device layers",
                "why_hard": ("each layer is unremarkable alone — people transfer, partners "
                             "guarantee, households share devices — so only the conjunction "
                             "is evidence, and an account-only graph cannot express it."),
                "source": ("FATF and FFIEC — common control behind nominally unrelated "
                           "parties as a concealment pattern"),
            },
            "equity_integration": {
                "answer": {"account": INTEGRATION_ACCT, "owner": INTEGRATION_OWNER,
                           "company_offset": INTEGRATION_COMPANY_OFFSET},
                "requires": "TRANSFER -> OWN -> INVEST, leaving the account layer entirely",
                "why_hard": ("the money path ends in a company register rather than another "
                             "account, so any account-only traversal loses it at the last "
                             "hop, and no amount rule sees it at all."),
                "source": ("FATF three-stage model — integration, converting proceeds into "
                           "legitimate ownership"),
            },
        },
        # Amounts are drawn from the same interval as ordinary traffic, and ordinary traffic
        # has a heavy tail, so transaction size is not evidence. Measured: `amount > 50,010`
        # selects 321 transfers of which 18 are planted (5.6% precision), against 100%
        # before the innocent tail existed.
        "amount_separability": {
            "innocent_range": [INNOCENT_AMOUNT_MIN, INNOCENT_AMOUNT_MAX],
            "innocent_tail_share": INNOCENT_TAIL_SHARE,
            "innocent_tail_max": INNOCENT_TAIL_MAX,
            "note": ("planted amounts sit inside the innocent distribution; a size filter is "
                     "no longer a detector"),
        },
        "typologies": {
            "structuring_ctr_evasion": {
                # FinCEN: structuring is transactions "on one or more days for the
                # purpose of evading" the reporting threshold; rule-based monitoring
                # looks for repetition inside a window. Every deposit here is under
                # the KRW 10,000,000 CTR threshold while the 7-day aggregate is far
                # above it — the aggregate-versus-individual gap IS the typology.
                "beneficiary": FANIN_HUB,
                "depositor_count": len(FANIN_SMURFS),
                "window_days": 7,
                "each_below": CTR_THRESHOLD,
                "aggregate_above": CTR_THRESHOLD,
                "channels": list(STRUCTURING_CHANNELS),
                "source": "FinCEN structuring definition; FFIEC BSA/AML red flags",
            },
            "funnel_account": {
                # FATF (Professional Money Laundering): small incoming transfers are
                # "almost immediately wired to another city or country". Collection
                # followed by prompt onward transfer is what separates a funnel from
                # an account that merely receives money.
                "funnel": FANIN_HUB,
                "onward_beneficiary": FUNNEL_OUT,
                "onward_channel": "WIRE_CROSSBORDER",
                "hours_after_last_deposit": 4,
                "source": "FATF Professional Money Laundering (2018)",
            },
            "layering_cycle": {
                # Circular flow returning to origin, each hop 6 hours apart so the
                # ring is traversable in time order.
                "rings": [CYCLE3, CYCLE5],
                "hop_interval_hours": 6,
                "channels": list(LAUNDERING_CHANNELS),
                "source": "FATF layering typology",
            },
            "rapid_passthrough": {
                # Velocity signal: in and out within hours, retaining almost nothing.
                "conduit": PASSTHRU,
                "origin": PASSTHRU_IN,
                "destination": PASSTHRU_OUT,
                "hours_held": 2,
                "retained": 50_000,
                "source": "FFIEC BSA/AML red flags — pass-through / conduit accounts",
            },
            "high_risk_channel_chain": {
                # The flagged account's onward chain, one hop per day, riding
                # low-traceability rails.
                "origin": FLAGGED,
                "hop_days": {"1": HOP1, "2": HOP2, "3": HOP3},
                "channels_by_hop": {"1": ["WIRE_CROSSBORDER", "VIRTUAL_ASSET"],
                                    "2": ["MVTS_HAWALA"], "3": ["ATM_CD"]},
                "source": "FATF new payment methods — geographic reach ⇒ risk",
            },
            "timeline": {"t0_epoch": T0, "hour": HOUR, "day": DAY},
        },
        "scenario_6_channels": {
            "description": (
                "Channel is part of the ground truth: laundering rings ride "
                "low-traceability channels, smurfing uses cash-like channels under the "
                "CTR threshold. Schema cardinality (12 channels) is the second scale axis."
            ),
            "channel_count": len(CHANNELS),
            "channel_codes": CHANNEL_CODES,
            "high_risk_channels": [c for c, _l, rw, _s in CHANNELS if rw >= 5],
            "laundering_channels": list(LAUNDERING_CHANNELS),
            "structuring_channels": list(STRUCTURING_CHANNELS),
            "ctr_threshold": CTR_THRESHOLD,
            "flagged_hop1_channels": {str(HOP1[0]): "WIRE_CROSSBORDER",
                                      str(HOP1[1]): "VIRTUAL_ASSET"},
            "smurf_amounts_under_ctr": True,
        },
    }


def _export(con: duckdb.DuckDBPyConnection, out: Path, counts: dict[str, int]) -> dict:
    nodes = {"Account": "account", "Person": "person", "Company": "company",
             "Loan": "loan", "Channel": "channel", "Medium": "medium"}
    edges = ["transfer", "own", "deposit", "repay", "uses_channel",
             "apply", "invest", "guarantee", "withdraw", "sign_in"]
    (out / "nodes").mkdir(parents=True, exist_ok=True)
    (out / "edges").mkdir(parents=True, exist_ok=True)
    checksums: dict[str, str] = {}
    for label, table in nodes.items():
        path = out / "nodes" / f"{label}.parquet"
        con.execute(f"COPY {table} TO '{path}' (FORMAT PARQUET)")
        checksums[f"nodes/{label}.parquet"] = _sha256(path)
    for etype in edges:
        path = out / "edges" / f"{etype}.parquet"
        con.execute(f"COPY {etype} TO '{path}' (FORMAT PARQUET)")
        checksums[f"edges/{etype}.parquet"] = _sha256(path)
    return checksums


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return "sha256:" + h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sf", type=int, default=1, help="scale factor (>=1)")
    parser.add_argument("--out", type=Path, default=Path("outputs/finbench"))
    parser.add_argument(
        "--hub-skew", type=float, default=1.0,
        help="degree-distribution skew. 1.0 = uniform attachment (binomial degree, no "
             "tail — measured max/mean 3.1 at SF1000). >1 concentrates edges on low "
             "account ids, producing a power-law tail with hub accounts, which is the "
             "regime LDBC FinBench considers its distinguishing feature. 3.0 puts ~159k "
             "edges on the top account at SF1000.")
    parser.add_argument(
        "--dup-share", type=float, default=0.0,
        help="fraction of the transfer budget re-emitted onto pairs that already exist, "
             "with sub-CTR amounts. 0.0 (default) reproduces the historical simple graph "
             "-- measured max multiplicity 2 over 10M edges. FinBench treats edge "
             "multiplicity as a modelled property, and without it count() and "
             "count(DISTINCT) are indistinguishable so a whole class of semantic error "
             "cannot be scored.")
    parser.add_argument(
        "--closure-share", type=float, default=0.0,
        help="fraction of the transfer budget spent closing existing two-paths (a->b->c "
             "gains a->c). 0.0 (default) reproduces the historical near-zero clustering "
             "-- measured average local clustering of exactly 0.000 at SF1000, against "
             "0.1-0.5 in real networks. Without it a cycle-detection scenario has no "
             "distractors, so it measures recall and gets precision for free.")
    parser.add_argument(
        "--cycle-share", type=float, default=0.0,
        help="fraction of the transfer budget spent closing two-paths into directed "
             "*cycles* (a->b->c gains c->a). Distinct from --closure-share: transitive "
             "closure raises the clustering coefficient but creates no cycle, so a "
             "laundering-ring scenario still has no distractors. Uses the laundering "
             "channels, so the distractor rings are plausible rather than arbitrary.")
    parser.add_argument(
        "--tag", default=None,
        help="output directory suffix, e.g. --sf 1000 --tag hub -> sf1000-hub. Keeps a "
             "skewed dataset from overwriting the uniform snapshot it is compared against.")
    args = parser.parse_args()
    if args.sf < 1:
        raise SystemExit("--sf must be >= 1")
    if args.hub_skew < 1.0:
        raise SystemExit("--hub-skew must be >= 1.0")
    if not 0.0 <= args.dup_share < 1.0:
        raise SystemExit("--dup-share must be in [0.0, 1.0)")
    if not 0.0 <= args.closure_share < 1.0:
        raise SystemExit("--closure-share must be in [0.0, 1.0)")
    if not 0.0 <= args.cycle_share < 1.0:
        raise SystemExit("--cycle-share must be in [0.0, 1.0)")
    if args.dup_share + args.closure_share + args.cycle_share >= 1.0:
        raise SystemExit("shares must leave room for base edges")

    out = args.out / (f"sf{args.sf}-{args.tag}" if args.tag else f"sf{args.sf}")
    con = duckdb.connect()
    counts = _build(con, args.sf, hub_skew=args.hub_skew, dup_share=args.dup_share,
                    closure_share=args.closure_share, cycle_share=args.cycle_share)
    checksums = _export(con, out, counts)
    degree = _degree_profile(con)
    multiplicity = _multiplicity_profile(con)

    manifest = {
        "schema_version": "seocho.finbench.duckdb.v1",
        "scale_factor": args.sf,
        "seed": 0.42,
        "hub_skew": args.hub_skew,
        "dup_share": args.dup_share,
        "closure_share": args.closure_share,
        "cycle_share": args.cycle_share,
        "counts": counts,
        "checksums": checksums,
        "planted_id_base": PLANT_BASE,
        # FinBench publishes "factor tables" summarising statistical properties of the
        # generated data. This is the same idea at minimum viable size: the degree
        # profile is what any hub-sensitive result has to be read against, and the
        # curated anchors let a measurement pick a *stated* degree rather than
        # whichever node happened to be planted.
        "degree_profile": degree,
        "multiplicity_profile": multiplicity,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "gold.json").write_text(json.dumps(_gold(), indent=2) + "\n")
    print(json.dumps({"out": str(out), "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
