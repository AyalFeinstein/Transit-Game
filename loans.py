"""Bank loans for companies.

A loan is borrowed in one sum and paid back in equal yearly instalments over a term of
LOAN_MIN_TERM_YEARS to LOAN_MAX_TERM_YEARS years, chosen by the borrower. Its interest rate
is the game's base rate (Game Settings) plus a risk premium that depends on
how likely the borrower is to pay it back, judged when the loan is taken, plus a little more
for each year beyond the shortest term.

A company that stays in debt (money below zero) with loans outstanding for
LOAN_SEIZURE_DEBT_YEARS year ends in a row has its assets seized to pay the lender: spare
trainsets first, then trainsets in service, then its least profitable lines. After a seizure
the company is never offered another loan.

Loans are kept on the player: ``player.loans`` (list of dicts), ``player.years_in_debt`` and
``player.loans_blacklisted``.
"""
from __future__ import annotations

import math

from game_presets import (
    LOAN_BASE_CREDIT, LOAN_BASE_INTEREST_RATE, LOAN_MAX_RISK_PREMIUM, LOAN_MAX_TERM_YEARS, LOAN_MIN_AMOUNT,
    LOAN_MIN_TERM_YEARS, LOAN_SEIZURE_DEBT_YEARS, LOAN_TERM_PREMIUM_PER_YEAR, LOAN_TERM_YEARS,
)


def clamp_term(years) -> int:
    """A loan term within the allowed range (the default term when ``years`` is missing)."""
    try:
        years = int(years)
    except (TypeError, ValueError):
        years = LOAN_TERM_YEARS
    return max(LOAN_MIN_TERM_YEARS, min(LOAN_MAX_TERM_YEARS, years))


def loans_of(player) -> list:
    loans = getattr(player, 'loans', None)
    if loans is None:
        loans = player.loans = []
    return loans


def total_debt(player) -> int:
    return int(sum(loan['balance'] for loan in loans_of(player)))


def annual_payment(principal: float, rate: float, years: int = LOAN_TERM_YEARS) -> int:
    """Equal yearly instalment that repays ``principal`` with interest in ``years``."""
    if principal <= 0:
        return 0
    if rate <= 0:
        return int(math.ceil(principal / years))
    return int(math.ceil(principal * rate / (1.0 - (1.0 + rate) ** -years)))


def base_rate(ops) -> float:
    return float(getattr(ops.game, 'loan_base_interest_rate', LOAN_BASE_INTEREST_RATE))


def _asset_value(ops, player) -> int:
    year = ops.get_year()
    value = 0
    for ts in player.get_trainsets() or []:
        try:
            value += int(ts.get_price(year)) // 2
        except Exception:
            pass
    return value + len(player.get_line_names() or []) * int(ops.inflate_economic_value(2000, max_factor=10.0))


def _yearly_net_before_loans(ops, player) -> float:
    try:
        summary = ops.get_player_financial_summary(player, ops.get_year())
        return float(summary.get('net', 0) or 0)
    except Exception:
        return 0.0


def repayment_chance(ops, player, amount: float, rate: float = None, years: int = LOAN_TERM_YEARS) -> float:
    """Estimated chance (0.05-0.98) that the company repays all its loans plus a new one."""
    rate = base_rate(ops) if rate is None else rate
    payments = sum(loan['payment'] for loan in loans_of(player)) + annual_payment(amount, rate, clamp_term(years))
    if payments <= 0:
        return 0.98
    net = _yearly_net_before_loans(ops, player)
    coverage = net / payments  # 1.0 = this year's net exactly covers the instalments
    debt = total_debt(player) + amount
    security = (_asset_value(ops, player) + max(0, ops.get_player_money(player))) / max(1.0, debt)
    chance = 0.5 + 0.3 * math.tanh(coverage - 0.5) + 0.2 * math.tanh(security - 1.0)
    return max(0.05, min(0.98, chance))


def max_loan_amount(ops, player) -> int:
    """Largest new loan the bank will offer (none to companies whose assets were seized)."""
    if getattr(player, 'loans_blacklisted', False):
        return 0
    net = max(0.0, _yearly_net_before_loans(ops, player))
    base = ops.inflate_economic_value(LOAN_BASE_CREDIT, max_factor=10.0)
    limit = max(base, 4 * net + _asset_value(ops, player)) - total_debt(player)
    return max(0, int(limit))


def loan_offer(ops, player, amount: int, years: int = LOAN_TERM_YEARS) -> dict:
    """Terms for borrowing ``amount`` now over ``years``: rate, yearly payment and repayment chance."""
    amount = int(amount)
    years = clamp_term(years)
    minimum = int(ops.inflate_economic_value(LOAN_MIN_AMOUNT, max_factor=10.0))
    maximum = max_loan_amount(ops, player)
    offer = {'amount': amount, 'minimum': minimum, 'maximum': maximum, 'years': years,
             'min_years': LOAN_MIN_TERM_YEARS, 'max_years': LOAN_MAX_TERM_YEARS}
    if getattr(player, 'loans_blacklisted', False):
        return {**offer, 'allowed': False,
                'reason': "Lenders will not deal with your company after seizing its assets."}
    if maximum < minimum:
        return {**offer, 'allowed': False, 'reason': "Your company cannot support any more borrowing."}
    if amount < minimum or amount > maximum:
        return {**offer, 'allowed': False,
                'reason': f"Loans must be between ${minimum:,} and ${maximum:,}."}
    chance = repayment_chance(ops, player, amount, years=years)
    rate = (base_rate(ops) + (1.0 - chance) * LOAN_MAX_RISK_PREMIUM
            + (years - LOAN_MIN_TERM_YEARS) * LOAN_TERM_PREMIUM_PER_YEAR)
    return {**offer, 'allowed': True, 'rate': rate, 'chance': chance,
            'payment': annual_payment(amount, rate, years)}


def take_loan(ops, player, amount: int, years: int = LOAN_TERM_YEARS) -> dict:
    offer = loan_offer(ops, player, amount, years)
    if not offer['allowed']:
        return {'success': False, 'message': offer['reason']}
    loans_of(player).append({
        'principal': int(amount), 'balance': int(amount), 'rate': float(offer['rate']),
        'payment': int(offer['payment']), 'year_taken': int(ops.get_year()), 'years': int(offer['years']),
    })
    ops.add_player_money(player, int(amount), reason='Loan')
    return {'success': True, 'message': (
        f"Borrowed ${int(amount):,} at {offer['rate'] * 100:.1f}% for {offer['years']} years "
        f"(${offer['payment']:,} a year)."), **offer}


def repay_loan(ops, player, index: int, amount: int = None) -> dict:
    """Pay off part (``amount``) or all of loan ``index`` from the company's money."""
    loans = loans_of(player)
    if not 0 <= index < len(loans):
        return {'success': False, 'message': "No such loan."}
    loan = loans[index]
    amount = loan['balance'] if amount is None else min(int(amount), loan['balance'])
    if amount <= 0:
        return {'success': False, 'message': "Nothing to repay."}
    if ops.get_player_money(player) < amount:
        return {'success': False, 'message': f"You need ${amount:,} to repay that."}
    ops.deduct_player_money(player, amount, reason='Loan repayment')
    loan['balance'] -= amount
    if loan['balance'] <= 0:
        loans.pop(index)
    return {'success': True, 'message': f"Repaid ${amount:,}."}


def _seize_assets(ops, player) -> list:
    """Sell the company's things until its debt and loans are covered (or nothing is left)."""
    seized = []

    def covered() -> bool:
        return ops.get_player_money(player) >= total_debt(player)

    lines = ops.get_map_lines() or {}
    spare = [t for t in list(player.get_trainsets() or []) if not t.get_line_assignment()]
    in_service = [t for t in list(player.get_trainsets() or []) if t.get_line_assignment()]
    for ts in spare + in_service:
        if covered():
            break
        try:
            sale = ops.sell_trainset_for_half_price(ts, player)
            seized.append(f"trainset (${sale['sale_price']:,})")
        except Exception:
            continue
    year = ops.get_year() - 1
    by_profit = sorted(
        (n for n in list(player.get_line_names() or []) if n in lines),
        key=lambda n: ops.get_line_profit_for_year(lines[n], year),
    )
    for name in by_profit:
        if covered():
            break
        try:
            ops.sell_line_complete(player, name)
            seized.append(f"line {name}")
        except Exception:
            continue
    # The lender takes what it is owed from the proceeds.
    for loan in list(loans_of(player)):
        pay = min(loan['balance'], max(0, ops.get_player_money(player)))
        if pay > 0:
            ops.deduct_player_money(player, pay, reason='Loan repaid from seized assets')
            loan['balance'] -= pay
    player.loans = [loan for loan in loans_of(player) if loan['balance'] > 0]
    return seized


def process_year_end(ops, player) -> dict:
    """Charge each loan's yearly instalment; seize assets after a long time in debt."""
    report = {'payments': 0, 'seized': []}
    loans = loans_of(player)
    for loan in loans:
        owed = int(math.ceil(loan['balance'] * (1.0 + loan['rate'])))
        made = int(loan.get('payments_made', 0)) + 1
        loan['payments_made'] = made
        # The last scheduled instalment settles whatever rounding left over.
        pay = owed if made >= int(loan.get('years', LOAN_TERM_YEARS)) else min(owed, loan['payment'])
        loan['balance'] = owed - pay
        ops.deduct_player_money(player, pay, reason='Loan payment')
        report['payments'] += pay
    player.loans = [loan for loan in loans if loan['balance'] > 0]
    if player.loans and ops.get_player_money(player) < 0:
        player.years_in_debt = int(getattr(player, 'years_in_debt', 0) or 0) + 1
    else:
        player.years_in_debt = 0
    if player.loans and player.years_in_debt >= LOAN_SEIZURE_DEBT_YEARS:
        report['seized'] = _seize_assets(ops, player)
        player.loans_blacklisted = True
        player.years_in_debt = 0
    return report
