"""
Position sizing for XAU/USD.

1 standard lot of spot gold = 100 oz, so a $1.00 move is worth US$100 per lot.

Two ways to size:
* risk-based   lots = (account × risk%) ÷ (stop distance × 100), rounded down to 0.01
* fixed lots   the trader's usual size (e.g. 0.05); the bot then reports what that
               size risks on this particular stop, in the account currency and as a
               % of the account, and the risk-based size for comparison.

Accounts in another currency (CAD, EUR…) are converted with the live USD rate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SYMBOLS = {"USD": "$", "CAD": "C$", "EUR": "€", "GBP": "£", "AUD": "A$", "NZD": "NZ$", "CHF": "CHF ", "JPY": "¥", "SGD": "S$", "AED": "AED "}


@dataclass
class Sizing:
    account: float                 # in account currency
    risk_pct: float                # the trader's risk guide, % per trade
    stop_distance: float           # $ per oz between entry and stop
    contract_oz: float = 100.0
    currency: str = "USD"
    fx: float = 1.0                # units of account currency per US dollar
    fixed_lots: float = 0.0        # 0 = size by risk

    # ---- money helpers ------------------------------------------------------
    @property
    def symbol(self) -> str:
        return SYMBOLS.get(self.currency.upper(), self.currency + " ")

    def money(self, amount_acct: float, d: int = 0) -> str:
        return f"{self.symbol}{amount_acct:,.{d}f}"

    def usd_to_acct(self, usd: float) -> float:
        return usd * self.fx

    @property
    def account_usd(self) -> float:
        return self.account / self.fx if self.fx else self.account

    # ---- sizes ----------------------------------------------------------------
    @property
    def risk_amount(self) -> float:
        """Risk guide in account currency."""
        return self.account * self.risk_pct / 100.0

    @property
    def lots_by_risk(self) -> float:
        if self.stop_distance <= 0 or self.account <= 0:
            return 0.0
        raw = (self.risk_amount / self.fx) / (self.stop_distance * self.contract_oz)
        return max(0.01, math.floor(raw * 100) / 100.0)

    @property
    def lots(self) -> float:
        return self.fixed_lots if self.fixed_lots > 0 else self.lots_by_risk

    @property
    def is_fixed(self) -> bool:
        return self.fixed_lots > 0

    # ---- what the chosen size means -----------------------------------------
    @property
    def actual_risk_usd(self) -> float:
        return self.lots * self.stop_distance * self.contract_oz

    @property
    def actual_risk(self) -> float:
        """Risk of the chosen size, in account currency."""
        return self.usd_to_acct(self.actual_risk_usd)

    @property
    def pct_of_account(self) -> float:
        return 100.0 * self.actual_risk / self.account if self.account else 0.0

    @property
    def over_guide(self) -> bool:
        """Fixed size risks noticeably more than the risk guide allows."""
        return self.is_fixed and self.pct_of_account > self.risk_pct * 1.25 + 1e-9

    def profit_at(self, distance: float) -> float:
        """Profit (account currency) if price moves ``distance`` $ in your favour."""
        return self.usd_to_acct(self.lots * abs(distance) * self.contract_oz)

    def to_dict(self) -> dict:
        return {"account": self.account, "currency": self.currency, "fx": round(self.fx, 4), "risk_pct": self.risk_pct,
                "risk_amount": round(self.risk_amount, 2), "stop_distance": round(self.stop_distance, 2),
                "lots": self.lots, "lots_by_risk": self.lots_by_risk, "fixed": self.is_fixed,
                "actual_risk": round(self.actual_risk, 2), "pct_of_account": round(self.pct_of_account, 2),
                "over_guide": self.over_guide}


def size_for(account: float, risk_pct: float, entry: float, stop: float, contract_oz: float = 100.0,
             currency: str = "USD", fx: float = 1.0, fixed_lots: float = 0.0) -> Sizing | None:
    if not account or account <= 0 or not risk_pct or risk_pct <= 0:
        return None
    return Sizing(account=account, risk_pct=risk_pct, stop_distance=abs(entry - stop), contract_oz=contract_oz,
                  currency=(currency or "USD").upper(), fx=fx or 1.0, fixed_lots=fixed_lots or 0.0)
