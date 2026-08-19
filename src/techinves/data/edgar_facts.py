"""Normalizes SEC EDGAR XBRL `companyfacts` into PeriodFacts.

This is the boundary between "what a company chose to tag" and the rest of the
scoring engine, which only ever sees RawFinancialFacts -- the same role
raw_facts.py plays for FMP's JSON. It is the expensive half of ADR 0001, and
the reasons are structural, not incidental:

1. **Concept mapping is not 1:1.** The same line item carries different tags at
   different companies, and often changes tag at the *same* company across
   years (ASU 606 moved most tech revenue from `Revenues` to
   `RevenueFromContractWithCustomerExcludingAssessedTax`). Every field is
   therefore a *candidate chain* resolved per period, not a single tag: see
   _US_GAAP below.

2. **Quarterly income and cash-flow figures are cumulative.** A 10-Q tags
   year-to-date spans, so Q3 must be derived as YTD9M - YTD6M and Q4 as
   FY - YTD9M. Many filers *also* tag the discrete 3-month span; when they do
   it is used directly. Balance-sheet items are instants and are exempt.

3. **The same period appears in several filings.** A restated figure shows up
   under a new `accn`; the latest `filed` date wins.

Both `us-gaap` and `ifrs-full` are supported -- ADR 0001 flagged SPOT and TSM
as IFRS filers with zero us-gaap concepts.

4. **Some filers report in a currency other than USD.** ASML tags its
   statements only in EUR. ADR 0007 (`reports/decisions/`) translates those
   facts to USD at this same boundary -- see `_TRANSLATABLE_UNITS`,
   `_translate_entry`, and `data/fx_rates.py` -- rather than rejecting them
   outright as ADR 0001 originally did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from techinves.data.fx_rates import SupportsFxYearSeries, average_rate, closing_rate
from techinves.models import FxTranslation, PeriodFacts

# --- Period-length classification -------------------------------------------
#
# Fiscal calendars are messy: 52/53-week retail calendars, leap years, and
# companies that shift their year end all put real spans a few days off the
# nominal length. These windows are deliberately loose enough to absorb that
# and still stay disjoint.
_ANNUAL_DAYS = (340, 400)
_NINE_MONTH_DAYS = (240, 310)
_SIX_MONTH_DAYS = (150, 210)
_QUARTER_DAYS = (75, 115)

# Only periodic reports. companyfacts also carries facts first tagged in 8-Ks
# and registration statements, which can disagree with the audited figure for
# the same period; restricting the source keeps the `filed`-wins dedupe from
# preferring an earnings-release number over the 10-K it was later restated in.
# 20-F/40-F matter specifically for the foreign private issuers on the
# watchlist (ASML, TSM, SPOT) -- they never file a 10-K.
_ACCEPTED_FORM_PREFIXES = ("10-K", "10-Q", "20-F", "40-F")

# Units this mapping will read outright, in preference order. A concept
# reported under no listed unit -- and not in _TRANSLATABLE_UNITS below, or
# translatable but with no fx_client supplied -- is skipped entirely, and that
# exclusion is load-bearing:
#
# Foreign private issuers tag their statements in their reporting currency.
# TSM files both TWD and USD, so preferring USD picks the right one and it
# never touches translation. A plausible wrong number is worse than an absent
# one, so a currency this mapping cannot resolve to USD is treated as
# unavailable and the company falls into the insufficient-data state ADR 0001
# clause 6 already defines.
_ACCEPTED_UNITS = ("USD", "shares")

# Currencies translated to USD at this boundary via data/fx_rates.py (ADR
# 0007), rather than accepted outright or rejected outright. ASML and (the
# now-removed) SPOT file *only* EUR -- accepting that unit as if it were
# dollars silently made every EV ratio wrong by the exchange rate, which is
# why ADR 0001 originally rejected it outright. ADR 0007 replaces that
# rejection with translation, keyed by currency rather than by ticker so a
# future non-USD, non-EUR filer only needs a new entry here plus a new ECB
# series key, not a new code path (ADR 0007 §5). Only exercised for EUR today.
_TRANSLATABLE_UNITS = ("EUR",)


@dataclass(frozen=True)
class _Fact:
    start: date | None  # None for instants (balance-sheet items)
    end: date
    value: float
    filed: date
    accn: str


@dataclass
class ConceptStore:
    """Deduplicated facts for one company, indexed by concept name.

    `durations` holds income-statement and cash-flow spans keyed by
    (start, end); `instants` holds balance-sheet items keyed by their single
    date. Splitting them is what makes the cumulative-quarter derivation
    below expressible at all -- it is only ever valid for durations.
    """

    taxonomy: str
    durations: dict[str, dict[tuple[date, date], float]] = field(default_factory=dict)
    instants: dict[str, dict[date, float]] = field(default_factory=dict)
    # Set when any concept was read from a _TRANSLATABLE_UNITS currency rather
    # than USD (ADR 0007). None means every fact in this store is USD-native
    # -- the ordinary, untranslated path.
    source_currency: str | None = None

    def has_any(self) -> bool:
        return bool(self.durations or self.instants)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _in_window(days: int, window: tuple[int, int]) -> bool:
    return window[0] <= days <= window[1]


def _span_days(start: date, end: date) -> int:
    return (end - start).days


def build_concept_store(
    company_facts: dict[str, Any],
    taxonomy: str,
    fx_client: SupportsFxYearSeries | None = None,
) -> ConceptStore:
    """Flattens companyfacts' concept -> unit -> [fact] nesting into a store,
    keeping exactly one value per (concept, period).

    companyfacts only ever returns consolidated, undimensioned facts, so there
    is no segment/axis filtering to do here -- the only ambiguity is the same
    period reported by several filings, resolved by latest `filed`.

    `fx_client` is the ADR 0007 boundary: when a concept's only usable unit is
    one of `_TRANSLATABLE_UNITS` (EUR today) and a client is supplied, each
    entry is translated to USD individually -- using its *own* start/end for
    the duration-fact average-rate lookup, or its own end for the
    instant-fact closing-rate lookup -- before it is stored. Everything above
    this function therefore only ever sees USD, exactly as it did before ADR
    0007. Translating per-entry (rather than per assembled period) is
    deliberate: a derived quarter (Q3 = YTD9M - YTD6M) subtracts two entries
    that each span a different window, and each must be translated at its own
    window's average rate before the subtraction, not at one blended rate
    after it, or the result would not equal what ASML's own EUR-denominated
    Q3 actually was.

    An entry that cannot be translated (no rate available anywhere in the
    ADR's lookback window) is dropped, not defaulted -- it becomes absent from
    the store exactly like an entry under an unrecognized unit would.
    """
    store = ConceptStore(taxonomy=taxonomy)
    concepts = (company_facts.get("facts") or {}).get(taxonomy) or {}

    for concept_name, concept_body in concepts.items():
        units = (concept_body or {}).get("units") or {}
        unit_key = next((u for u in _ACCEPTED_UNITS if u in units), None)
        translate_currency: str | None = None
        if unit_key is None and fx_client is not None:
            translate_currency = next((c for c in _TRANSLATABLE_UNITS if c in units), None)
            unit_key = translate_currency
        if unit_key is None:
            continue

        best_duration: dict[tuple[date, date], _Fact] = {}
        best_instant: dict[date, _Fact] = {}

        for entry in units.get(unit_key) or []:
            if not isinstance(entry, dict):
                continue
            form = str(entry.get("form") or "")
            if not form.startswith(_ACCEPTED_FORM_PREFIXES):
                continue
            end = _parse_date(entry.get("end"))
            filed = _parse_date(entry.get("filed"))
            raw_value = entry.get("val")
            if end is None or filed is None or not isinstance(raw_value, (int, float)):
                continue
            start = _parse_date(entry.get("start"))

            value = float(raw_value)
            if translate_currency is not None:
                translated = _translate_entry(fx_client, translate_currency, value, start, end)
                if translated is None:
                    # No published rate anywhere in the lookback window for
                    # this entry's date(s) -- ADR 0007 §3: refuse, don't
                    # fabricate. Drop just this one entry.
                    continue
                value = translated
                store.source_currency = translate_currency

            fact = _Fact(start=start, end=end, value=value, filed=filed, accn=str(entry.get("accn") or ""))

            if fact.start is None:
                current = best_instant.get(end)
                if current is None or _supersedes(fact, current):
                    best_instant[end] = fact
            else:
                key = (fact.start, end)
                current = best_duration.get(key)
                if current is None or _supersedes(fact, current):
                    best_duration[key] = fact

        if best_duration:
            store.durations[concept_name] = {k: f.value for k, f in best_duration.items()}
        if best_instant:
            store.instants[concept_name] = {k: f.value for k, f in best_instant.items()}

    return store


def _translate_entry(
    fx_client: SupportsFxYearSeries,
    currency: str,
    value: float,
    start: date | None,
    end: date,
) -> float | None:
    """USD value for one raw XBRL entry, per ADR 0007 §2: average rate over
    the entry's own [start, end] if it is a duration fact, closing rate at
    `end` (with §3's bounded carry-forward) if it is an instant fact.
    """
    if start is None:
        rate = closing_rate(fx_client, currency, end)
        if rate is None:
            return None
        return value * rate[0]

    rate_value = average_rate(fx_client, currency, start, end)
    if rate_value is None:
        return None
    return value * rate_value


def _supersedes(candidate: _Fact, incumbent: _Fact) -> bool:
    """Later filing wins (restatements). `accn` breaks the tie so the result is
    deterministic when a company files two documents the same day, rather than
    depending on dict iteration order.
    """
    if candidate.filed != incumbent.filed:
        return candidate.filed > incumbent.filed
    return candidate.accn > incumbent.accn


def detect_taxonomy(company_facts: dict[str, Any]) -> str | None:
    """Whichever of us-gaap / ifrs-full the company actually reports under.

    Picked by concept count rather than mere presence: a US filer can carry a
    stray ifrs-full tag (and vice versa), and the real taxonomy is the one with
    hundreds of concepts, not one.
    """
    facts = company_facts.get("facts") or {}
    counts = {tax: len(facts.get(tax) or {}) for tax in ("us-gaap", "ifrs-full")}
    best = max(counts, key=lambda t: counts[t])
    return best if counts[best] > 0 else None


# --- Concept candidate chains ------------------------------------------------
#
# Order matters: the first concept with a value for the period being resolved
# wins. More specific / more modern tags come first, broader legacy tags last.

_US_GAAP: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "SalesRevenueServicesNet",
    ),
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": (
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfServices",
        "CostOfGoodsSold",
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": (
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ),
    "interest_expense": (
        "InterestExpenseNonoperating",
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestAndDebtExpense",
    ),
    "income_tax_expense": ("IncomeTaxExpenseBenefit",),
    "weighted_avg_diluted_shares": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasicAndDiluted",
    ),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "liabilities_and_equity": ("LiabilitiesAndStockholdersEquity",),
    "total_current_assets": ("AssetsCurrent",),
    "total_current_liabilities": ("LiabilitiesCurrent",),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    "total_stockholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "cash_and_equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "long_term_debt": (
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
        "ConvertibleDebtNoncurrent",
        "ConvertibleLongTermNotesPayable",
    ),
    "debt_current": ("DebtCurrent",),
    "long_term_debt_current": (
        "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
        # Software companies frequently carry no bank debt at all and fund
        # themselves with convertible notes -- it is Shopify's only borrowing.
        "ConvertibleDebtCurrent",
        "ConvertibleNotesPayableCurrent",
    ),
    "short_term_borrowings": ("ShortTermBorrowings", "OtherShortTermBorrowings"),
    "commercial_paper": ("CommercialPaper",),
    "debt_combined": ("DebtLongtermAndShorttermCombinedAmount", "LongTermDebt"),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
    ),
    "stock_based_compensation": (
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
    ),
    "depreciation_and_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ),
    "depreciation": ("Depreciation",),
    "amortization": ("AmortizationOfIntangibleAssets",),
}

_IFRS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenue", "RevenueFromContractsWithCustomers"),
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": ("CostOfSales",),
    "operating_income": ("ProfitLossFromOperatingActivities",),
    "net_income": ("ProfitLoss",),
    "interest_expense": ("FinanceCosts", "InterestExpense"),
    "income_tax_expense": ("IncomeTaxExpenseContinuingOperations",),
    # `AdjustedWeightedAverageShares` is the diluted figure under IFRS naming;
    # `WeightedAverageShares` is the basic one and is the fallback.
    "weighted_avg_diluted_shares": (
        "AdjustedWeightedAverageShares",
        "WeightedAverageShares",
    ),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "liabilities_and_equity": ("EquityAndLiabilities",),
    "total_current_assets": ("CurrentAssets",),
    "total_current_liabilities": ("CurrentLiabilities",),
    "retained_earnings": ("RetainedEarnings",),
    "total_stockholders_equity": ("Equity", "EquityAttributableToOwnersOfParent"),
    "cash_and_equivalents": ("CashAndCashEquivalents",),
    "long_term_debt": (
        "LongtermBorrowings",
        "BorrowingsNoncurrent",
        "NoncurrentPortionOfNoncurrentBondsIssued",
    ),
    "debt_current": ("CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",),
    "long_term_debt_current": (
        "CurrentPortionOfLongtermBorrowings",
        "CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued",
    ),
    "short_term_borrowings": ("ShorttermBorrowings",),
    "commercial_paper": (),
    "debt_combined": ("Borrowings",),
    "operating_cash_flow": ("CashFlowsFromUsedInOperatingActivities",),
    "capital_expenditure": (
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets",
    ),
    "stock_based_compensation": ("ShareBasedPaymentsExpense",),
    "depreciation_and_amortization": (
        "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
        "DepreciationAndAmortisationExpense",
    ),
    # The `AdjustmentsFor...` spellings are the cash-flow-statement add-backs,
    # which is where IFRS filers such as SPOT tag these.
    "depreciation": (
        "DepreciationExpense",
        "AdjustmentsForDepreciationExpense",
        "DepreciationPropertyPlantAndEquipment",
    ),
    "amortization": (
        "AmortisationExpense",
        "AdjustmentsForAmortisationExpense",
        "AmortisationIntangibleAssetsOtherThanGoodwill",
    ),
}

_CHAINS = {"us-gaap": _US_GAAP, "ifrs-full": _IFRS}

# Fields whose value comes from the income statement / cash-flow statement and
# therefore must be resolved as a duration; everything else is an instant.
_DRIVER_FIELDS = ("revenue", "net_income", "operating_cash_flow")

# Period *discovery* (which end dates count as a real fiscal period) anchors on
# revenue alone wherever possible, falling back to the full _DRIVER_FIELDS set
# only when revenue never produces an annual/quarter-window span at all. Some
# filers tag a rolling trailing-twelve-month figure for net_income and/or
# operating_cash_flow as a supplementary MD&A disclosure rather than a restated
# fiscal period -- AMZN's 10-Qs carry a "Trailing Twelve Months" free-cash-flow
# reconciliation that XBRL-tags TTM NetIncomeLoss, OperatingCashFlow,
# ShareBasedCompensation and capex, each with a 340-400 day duration shape
# identical to a genuine annual span but ending on a non-fiscal-year-end
# quarter date. Using any() of _DRIVER_FIELDS for discovery (the original
# behaviour) treated every one of those rolling windows as a real fiscal
# period: it manufactured phantom "annual" entries with a real fiscal_date but
# null revenue/operating_income, and because the newest one sorts first, it
# also corrupted `fiscal_year_end_month` (derived from `annual_ends[0]`),
# discovered here as the actual root cause of AMZN's ~25% coverage --
# `revenue_growth_yoy` et al. read `annual[0]` expecting the latest *real*
# fiscal year, not a rolling TTM stub. Revenue was not observed carrying this
# pattern for any watchlist filer: it is the one mandatory top-line concept
# always tied to the filing's actual reporting period, never to a
# supplementary TTM table. See `reports/agents/2026-08-16_amzn-edgar-coverage-diagnosis.md`.
_PERIOD_DISCOVERY_FIELDS = ("revenue",)


class FactResolver:
    """Reads normalized field values out of a ConceptStore for a given period.

    Kept as a class rather than free functions because every lookup needs both
    the store and the taxonomy's chain table, and threading those through a
    dozen call sites obscured the actual mapping logic.
    """

    def __init__(self, store: ConceptStore) -> None:
        self.store = store
        self.chain = _CHAINS.get(store.taxonomy, {})

    # -- primitives --

    def concepts_for(self, field_name: str) -> tuple[str, ...]:
        return self.chain.get(field_name, ())

    def instant(self, field_name: str, when: date) -> float | None:
        for concept in self.concepts_for(field_name):
            value = self.store.instants.get(concept, {}).get(when)
            if value is not None:
                return value
        return None

    def _spans_ending_at(self, concept: str, end: date) -> dict[date, float]:
        """{start: value} for every deduped span of `concept` ending at `end`."""
        return {
            start: value
            for (start, span_end), value in self.store.durations.get(concept, {}).items()
            if span_end == end
        }

    def annual(self, field_name: str, end: date) -> float | None:
        for concept in self.concepts_for(field_name):
            for start, value in self._spans_ending_at(concept, end).items():
                if _in_window(_span_days(start, end), _ANNUAL_DAYS):
                    return value
        return None

    def quarter(self, field_name: str, end: date, prior_end: date | None) -> float | None:
        """The discrete three-month value for the quarter ending `end`.

        Prefers a directly tagged 3-month span. Falls back to differencing two
        year-to-date spans that share a start date -- which is what makes
        Q3 = YTD9M - YTD6M and Q4 = FY - YTD9M fall out of one expression
        rather than needing a per-quarter special case. Q1 needs neither: its
        YTD span *is* the quarter, so the direct check catches it.
        """
        for concept in self.concepts_for(field_name):
            spans = self._spans_ending_at(concept, end)
            for start, value in spans.items():
                if _in_window(_span_days(start, end), _QUARTER_DAYS):
                    return value

        if prior_end is None:
            return None

        for concept in self.concepts_for(field_name):
            spans = self._spans_ending_at(concept, end)
            concept_spans = self.store.durations.get(concept, {})
            # Shortest cumulative span first: prefers the tightest enclosing
            # pair when more than one is available.
            #
            # This does NOT keep the result within a single filing, despite
            # what an earlier version of this comment claimed: the FY span
            # normally comes from the 10-K and the YTD span from an interim
            # 10-Q, so they are two different accessions (and therefore two
            # different documents) in the ordinary case, not just the
            # restated one. `build_concept_store` discards `accn`, so nothing
            # here can tell a same-filing pair from a cross-filing one, and a
            # restated prior-year figure combined with an unrestated interim
            # figure can produce a discrete quarter that matches no single
            # filed document. Known limitation, not (currently) handled.
            for start in sorted(spans, key=lambda s: _span_days(s, end)):
                if start > prior_end:
                    continue
                prior = concept_spans.get((start, prior_end))
                if prior is not None:
                    if not _in_window(_span_days(prior_end, end), _QUARTER_DAYS):
                        continue
                    return spans[start] - prior
        return None


# --- Field assembly ----------------------------------------------------------


def _capex_as_outflow(value: float | None) -> float | None:
    """XBRL tags capital expenditure as a positive *payment*; FMP reported it
    as a negative cash-flow line, and every downstream consumer (notably
    `fcf = ocf + capex` in the FCF derivation, and the golden fixtures) was
    written against that sign. Normalize to the negative convention so the
    switch of provider is not also a silent switch of sign.
    """
    if value is None:
        return None
    return -abs(value)


def _total_debt(resolver: FactResolver, when: date) -> float | None:
    """Sum of interest-bearing debt, current + non-current.

    us-gaap has no single "total debt" concept (ADR 0001, cost item 3), so it
    has to be assembled. `DebtCurrent` is preferred for the current portion
    because it is already the total; only when it is absent are the individual
    current-debt tags summed, which is where double counting would otherwise
    creep in. `LongTermDebt` is a last resort and sits in `debt_combined`
    rather than `long_term_debt`: in us-gaap it means the *whole* carrying
    amount including current maturities, so adding a current portion to it
    would double count.

    `None` means "unresolved", not zero -- except when no debt concept
    resolves *and* the balance sheet otherwise does (a total-assets or
    total-equity figure exists for `when`). A resolving balance sheet with no
    borrowings tagged anywhere is positive evidence of zero debt, not missing
    data: genuinely debt-free filers never tag any of the concepts above, so
    treating that case as unresolved silently dropped enterprise value (and
    everything derived from it) for every debt-free company.
    """
    current = resolver.instant("debt_current", when)
    if current is None:
        parts = [
            resolver.instant("long_term_debt_current", when),
            resolver.instant("short_term_borrowings", when),
            resolver.instant("commercial_paper", when),
        ]
        present = [p for p in parts if p is not None]
        current = sum(present) if present else None

    noncurrent = resolver.instant("long_term_debt", when)

    if current is not None or noncurrent is not None:
        return (current or 0.0) + (noncurrent or 0.0)

    combined = resolver.instant("debt_combined", when)
    if combined is not None:
        return combined

    balance_sheet_resolves = (
        resolver.instant("total_assets", when) is not None
        or resolver.instant("total_stockholders_equity", when) is not None
    )
    return 0.0 if balance_sheet_resolves else None


def _gross_profit(resolver: FactResolver, read, when, prior) -> float | None:
    direct = read(resolver, "gross_profit", when, prior)
    if direct is not None:
        return direct
    revenue = read(resolver, "revenue", when, prior)
    cost = read(resolver, "cost_of_revenue", when, prior)
    if revenue is None or cost is None:
        return None
    return revenue - cost


def _depreciation_and_amortization(resolver: FactResolver, read, when, prior) -> float | None:
    direct = read(resolver, "depreciation_and_amortization", when, prior)
    if direct is not None:
        return direct
    # ADR 0001 called this out by name: filers that never tag a combined D&A
    # line (ORCL among them) tag the two components separately instead.
    depreciation = read(resolver, "depreciation", when, prior)
    amortization = read(resolver, "amortization", when, prior)
    # Both or neither, not "whichever resolved": summing only depreciation
    # when amortization happens to be tagged under a concept not in the chain
    # silently understates D&A (and overstates ev_ebitda) with no signal that
    # the figure is incomplete. Requiring both means a filer with genuinely no
    # intangibles (so no amortization tag at all) loses this field entirely --
    # a real cost, but a smaller one than "amortization tagged elsewhere"
    # producing a plausible wrong number for filers that do have intangibles.
    if depreciation is None or amortization is None:
        return None
    return depreciation + amortization


def _total_liabilities(resolver: FactResolver, when: date) -> float | None:
    direct = resolver.instant("total_liabilities", when)
    if direct is not None:
        return direct
    # Plenty of filers omit a `Liabilities` total and only tag the balance-sheet
    # identity, from which it falls out exactly.
    total = resolver.instant("liabilities_and_equity", when)
    equity = resolver.instant("total_stockholders_equity", when)
    if total is None or equity is None:
        return None
    return total - equity


def _fx_translation_for_period(
    fx_client: SupportsFxYearSeries,
    currency: str,
    fiscal_date: date,
    prior_end: date | None,
    is_annual: bool,
) -> FxTranslation:
    """The traceability record ADR 0007 §4 requires for a translated period.

    Individual facts inside the period were each already translated at their
    own exact XBRL start/end (`_translate_entry`, called per raw entry in
    `build_concept_store`) -- this is a *representative* summary for the
    period as a whole, using a nominal window rather than re-deriving the
    exact span each contributing concept happened to use: annual periods use
    [fiscal_date - 1y, fiscal_date], quarters use [prior_end, fiscal_date] (or
    a 90-day fallback for a first quarter with no prior period). Business-day
    FX averages are insensitive to being off by the day or two this nominal
    window can differ from a concept's literal tagged start, so this remains
    an accurate audit trail for "what rate applied here" even though it is
    not, tag-for-tag, the exact window every underlying figure used.
    """
    if is_annual:
        window_start = date(fiscal_date.year - 1, fiscal_date.month, fiscal_date.day)
    elif prior_end is not None:
        window_start = prior_end
    else:
        window_start = fiscal_date - timedelta(days=90)

    avg = average_rate(fx_client, currency, window_start, fiscal_date)
    close = closing_rate(fx_client, currency, fiscal_date)

    return FxTranslation(
        source_currency=currency,
        average_rate=avg,
        average_rate_start=window_start,
        average_rate_end=fiscal_date,
        closing_rate=close[0] if close is not None else None,
        closing_rate_date=close[1] if close is not None else None,
    )


def _build_period(
    resolver: FactResolver,
    fiscal_date: date,
    period_label: str,
    prior_end: date | None,
    is_annual: bool,
    fx: FxTranslation | None = None,
) -> PeriodFacts:
    """One PeriodFacts. `read` selects annual vs. discrete-quarter resolution
    for the flow items; instants are period-independent either way.
    """
    if is_annual:
        def read(r: FactResolver, name: str, when: date, _prior: date | None) -> float | None:
            return r.annual(name, when)
    else:
        def read(r: FactResolver, name: str, when: date, prior: date | None) -> float | None:
            return r.quarter(name, when, prior)

    ocf = read(resolver, "operating_cash_flow", fiscal_date, prior_end)
    capex = _capex_as_outflow(read(resolver, "capital_expenditure", fiscal_date, prior_end))
    # ADR 0001 clause 2: EDGAR has no free-cash-flow concept -- nobody tags it,
    # it is a derived measure. Same formula raw_facts.py already used to fill
    # FMP's gaps, and capex is negative by the time it gets here.
    free_cash_flow = ocf + capex if ocf is not None and capex is not None else None

    return PeriodFacts(
        fiscal_date=fiscal_date,
        period=period_label,
        revenue=read(resolver, "revenue", fiscal_date, prior_end),
        gross_profit=_gross_profit(resolver, read, fiscal_date, prior_end),
        operating_income=read(resolver, "operating_income", fiscal_date, prior_end),
        net_income=read(resolver, "net_income", fiscal_date, prior_end),
        interest_expense=read(resolver, "interest_expense", fiscal_date, prior_end),
        income_tax_expense=read(resolver, "income_tax_expense", fiscal_date, prior_end),
        weighted_avg_diluted_shares=read(
            resolver, "weighted_avg_diluted_shares", fiscal_date, prior_end
        ),
        total_assets=resolver.instant("total_assets", fiscal_date),
        total_liabilities=_total_liabilities(resolver, fiscal_date),
        total_current_assets=resolver.instant("total_current_assets", fiscal_date),
        total_current_liabilities=resolver.instant("total_current_liabilities", fiscal_date),
        retained_earnings=resolver.instant("retained_earnings", fiscal_date),
        total_stockholders_equity=resolver.instant("total_stockholders_equity", fiscal_date),
        total_debt=_total_debt(resolver, fiscal_date),
        cash_and_equivalents=resolver.instant("cash_and_equivalents", fiscal_date),
        long_term_debt=resolver.instant("long_term_debt", fiscal_date),
        operating_cash_flow=ocf,
        capital_expenditure=capex,
        stock_based_compensation=read(resolver, "stock_based_compensation", fiscal_date, prior_end),
        free_cash_flow=free_cash_flow,
        depreciation_and_amortization=_depreciation_and_amortization(
            resolver, read, fiscal_date, prior_end
        ),
        fx=fx,
    )


# --- Period discovery --------------------------------------------------------


def _concepts_for_fields(resolver: FactResolver, fields: tuple[str, ...]) -> list[str]:
    concepts: list[str] = []
    for field_name in fields:
        concepts.extend(resolver.concepts_for(field_name))
    return concepts


def _annual_end_dates(resolver: FactResolver) -> list[date]:
    """Fiscal year ends, newest first.

    Derived from concept duration shapes rather than from `fy`/`fp`: those
    describe the *filing's* fiscal year, not the fact's, so a 10-K's
    prior-year comparative carries the current year's `fy`. Anchored on
    revenue alone (see `_PERIOD_DISCOVERY_FIELDS`); falls back to the broader
    `_DRIVER_FIELDS` set only when revenue produces no annual-window span at
    all, so a filer that genuinely never tags revenue that way still gets
    periods discovered the original way.
    """

    def ends_for(concepts: list[str]) -> set[date]:
        ends: set[date] = set()
        for concept in concepts:
            for (start, end) in resolver.store.durations.get(concept, {}):
                if _in_window(_span_days(start, end), _ANNUAL_DAYS):
                    ends.add(end)
        return ends

    ends = ends_for(_concepts_for_fields(resolver, _PERIOD_DISCOVERY_FIELDS))
    if not ends:
        ends = ends_for(_concepts_for_fields(resolver, _DRIVER_FIELDS))
    return sorted(ends, reverse=True)


def _quarter_end_dates(resolver: FactResolver) -> list[date]:
    """Every fiscal quarter end, newest first -- including fiscal year ends,
    which are also quarter ends (the Q4 that no 10-Q reports directly).

    Anchoring and fallback follow the same rule as `_annual_end_dates`.
    """
    windows = (_QUARTER_DAYS, _SIX_MONTH_DAYS, _NINE_MONTH_DAYS, _ANNUAL_DAYS)

    def ends_for(concepts: list[str]) -> set[date]:
        ends: set[date] = set()
        for concept in concepts:
            for (start, end) in resolver.store.durations.get(concept, {}):
                days = _span_days(start, end)
                if any(_in_window(days, w) for w in windows):
                    ends.add(end)
        return ends

    ends = ends_for(_concepts_for_fields(resolver, _PERIOD_DISCOVERY_FIELDS))
    if not ends:
        ends = ends_for(_concepts_for_fields(resolver, _DRIVER_FIELDS))
    return sorted(ends, reverse=True)


def _has_flow_data(period: PeriodFacts) -> bool:
    """Whether any income-statement or cash-flow figure resolved.

    Balance-sheet instants are excluded on purpose: they are tagged at every
    fiscal year end regardless of whether the company reports quarterly, so
    they cannot distinguish a real quarter from a year end that merely looks
    like one.
    """
    return any(
        value is not None
        for value in (
            period.revenue,
            period.operating_income,
            period.net_income,
            period.operating_cash_flow,
        )
    )


def _quarter_label(end: date, fiscal_year_end_month: int | None) -> str:
    """Q1..Q4 from the quarter end's distance to the fiscal year end.

    Month arithmetic rather than day counting, because 52/53-week filers move
    their period ends by up to six days a year and would drift across a
    day-based boundary. The nearest-offset match absorbs the filers whose
    quarter ends land a month either side of the nominal boundary.
    """
    if fiscal_year_end_month is None:
        return "Q1"
    delta = (end.month - fiscal_year_end_month) % 12
    by_offset = {3: "Q1", 6: "Q2", 9: "Q3", 0: "Q4"}
    if delta in by_offset:
        return by_offset[delta]
    nearest = min(by_offset, key=lambda o: min((delta - o) % 12, (o - delta) % 12))
    return by_offset[nearest]


def build_periods(
    company_facts: dict[str, Any],
    *,
    annual_limit: int = 8,
    quarterly_limit: int = 8,
    fx_client: SupportsFxYearSeries | None = None,
) -> tuple[list[PeriodFacts], list[PeriodFacts], str | None]:
    """(annual, quarterly, taxonomy) for one company, each newest first.

    Returns empty lists (and the detected taxonomy, if any) when the company
    has filed XBRL but nothing this mapping recognizes -- callers distinguish
    that from "no facts at all" via the taxonomy being None.

    `fx_client` is passed straight through to `build_concept_store` (ADR
    0007); omitting it (the default) reproduces the exact pre-ADR-0007
    behaviour -- non-USD facts are simply skipped, never translated. When a
    company's facts *were* translated, every returned `PeriodFacts` carries a
    populated `.fx` traceability record (ADR 0007 §4); USD-native companies
    always get `fx=None`.
    """
    taxonomy = detect_taxonomy(company_facts)
    if taxonomy is None:
        return [], [], None

    store = build_concept_store(company_facts, taxonomy, fx_client=fx_client)
    if not store.has_any():
        return [], [], taxonomy
    resolver = FactResolver(store)
    currency = store.source_currency

    annual_ends = _annual_end_dates(resolver)
    fiscal_year_end_month = annual_ends[0].month if annual_ends else None

    annual = [
        _build_period(
            resolver,
            end,
            "FY",
            prior_end=None,
            is_annual=True,
            fx=_fx_translation_for_period(fx_client, currency, end, None, True)
            if currency is not None and fx_client is not None
            else None,
        )
        for end in annual_ends[:annual_limit]
    ]

    quarter_ends = _quarter_end_dates(resolver)
    quarterly: list[PeriodFacts] = []
    for index, end in enumerate(quarter_ends):
        if len(quarterly) >= quarterly_limit:
            break
        # The next end in the descending list is the previous quarter's, which
        # is exactly the subtrahend the cumulative-to-discrete derivation needs.
        prior_end = quarter_ends[index + 1] if index + 1 < len(quarter_ends) else None
        period = _build_period(
            resolver,
            end,
            _quarter_label(end, fiscal_year_end_month),
            prior_end=prior_end,
            is_annual=False,
            fx=_fx_translation_for_period(fx_client, currency, end, prior_end, False)
            if currency is not None and fx_client is not None
            else None,
        )
        # Annual-only filers (20-F issuers such as ASML, and any company whose
        # interim reports are 6-Ks rather than 10-Qs) contribute fiscal year
        # ends to the quarter-end candidate list, but no three-month figure can
        # be resolved or derived for them. Emitting those as quarters would
        # publish a row of Nones carrying a real fiscal date -- a period that
        # looks reported but says nothing.
        if _has_flow_data(period):
            quarterly.append(period)

    return annual, quarterly, taxonomy
