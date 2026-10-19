"""
Position sizing for XAU/USD.

1 standard lot of spot gold = 100 oz, so a $1.00 move is worth $100 per lot.
    lots = (account × risk%) / (stop distance in $ × 100)
Rounded DOWN to 0.01 lot (the usual minimum), never below 0.01.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Sizing:
    account: float
    risk_pct: float
    stop_distance: float
    contract_oz: float = 100.0

    @property
    def risk_amount(self) -> float:
        return self.account * self.risk_pct / 100.0

    @property
    def lots(self) -> float:
        if self.stop_distance <= 0 or self.account <= 0:
            return 0.0
        raw = self.risk_amount / (self.stop_distance * self.contract_oz)
        return max(0.01, math.floor(raw * 100) / 100.0)

    @property
    def actual_risk(self) -> float:
        return self.lots * self.stop_distance * self.contract_oz

    def profit_at(self, distance: float) -> float:
        return self.lots * abs(distance) * self.contract_oz

    def to_dict(self) -> dict:
        return {"account": self.account, "risk_pct": self.risk_pct, "risk_amount": round(self.risk_amount, 2),
                "stop_distance": round(self.stop_distance, 2), "lots": self.lots, "actual_risk": round(self.actual_risk, 2)}


def size_for(account: float, risk_pct: float, entry: float, stop: float, contract_oz: float = 100.0) -> Sizing | None:
    if not account or account <= 0 or not risk_pct or risk_pct <= 0:
        return None
    return Sizing(account=account, risk_pct=risk_pct, stop_distance=abs(entry - stop), contract_oz=contract_oz)
