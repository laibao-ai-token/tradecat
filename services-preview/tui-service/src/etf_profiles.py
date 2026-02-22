from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ETFWeights:
    trend: float = 0.35
    momentum: float = 0.25
    liquidity: float = 0.20
    risk_adjusted: float = 0.20


@dataclass(frozen=True)
class ETFDomainProfile:
    key: str
    label: str
    symbols: tuple[str, ...]
    top_n: int
    rebalance: str
    risk_profile: str
    weights: ETFWeights


# Seed profile for issue #001.
# The symbol list is intentionally editable so we can tune coverage quickly.
AUTO_DRIVING_CN_PROFILE = ETFDomainProfile(
    key="auto_driving_cn",
    label="Auto Driving CN",
    symbols=(
        "SZ159889",
        "SH516520",
        "SH515700",
        "SZ159806",
        "SH512480",
        "SZ159995",
        "SH588000",
        "SH515000",
        "SZ159819",
        "SH516110",
    ),
    top_n=5,
    rebalance="daily",
    risk_profile="conservative",
    weights=ETFWeights(),
)


ETF_DOMAIN_PROFILES: dict[str, ETFDomainProfile] = {
    AUTO_DRIVING_CN_PROFILE.key: AUTO_DRIVING_CN_PROFILE,
}

_DYNAMIC_AUTO_DRIVING_FILES: tuple[str, ...] = (
    "artifacts/analysis/cybercab_fund_relevance_expanded_20260219.csv",
    "artifacts/analysis/cybercab_fund_relevance_full_20260219.csv",
    "artifacts/analysis/cybercab_fund_relevance_unique_20260219.csv",
    "artifacts/analysis/cybercab_fund_relevance_20260219.csv",
)


def _cn_fund_symbol_from_code(code: str) -> str:
    digits = "".join(ch for ch in (code or "").strip() if ch.isdigit())
    if len(digits) != 6:
        return ""
    if digits[0] in {"5", "6", "9"}:
        return f"SH{digits}"
    if digits.startswith(("15", "16", "18")):
        return f"SZ{digits}"
    # Off-market fund code (e.g. 010955/024389): keep 6 digits.
    return digits


def _to_float(value: object) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def load_dynamic_auto_driving_symbols(repo_root: Path, top_n: int = 35) -> list[str]:
    """
    Load top-N auto-driving fund symbols from analysis CSV.

    Priority: expanded -> full -> unique -> strict.
    """
    limit = max(1, int(top_n))
    base = repo_root.resolve()

    for rel in _DYNAMIC_AUTO_DRIVING_FILES:
        path = base / rel
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                scored_rows: list[tuple[float, float, str]] = []
                for row in reader:
                    symbol = _cn_fund_symbol_from_code(str(row.get("code", "")).strip())
                    if not symbol:
                        continue
                    core_hit = _to_float(row.get("core_hit", 0.0))
                    oem_hit = _to_float(row.get("oem_hit", 0.0))
                    chip_hit = _to_float(row.get("chip_hit", 0.0))
                    relevance = _to_float(row.get("relevance", 0.0))
                    # Keep the Cybercab-heavy priority used in our previous analysis:
                    # core chain > OEM > chips.
                    cybercab_score = 0.62 * core_hit + 0.25 * oem_hit + 0.13 * chip_hit
                    scored_rows.append((cybercab_score, relevance, symbol))

                if not scored_rows:
                    continue

                scored_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
                out: list[str] = []
                seen: set[str] = set()
                for _, _, symbol in scored_rows:
                    if symbol in seen:
                        continue
                    out.append(symbol)
                    seen.add(symbol)
                    if len(out) >= limit:
                        break
                if out:
                    return out
        except Exception:
            continue
    return []


def get_etf_domain_profile(key: str) -> ETFDomainProfile:
    profile_key = (key or "").strip().lower()
    if profile_key in ETF_DOMAIN_PROFILES:
        return ETF_DOMAIN_PROFILES[profile_key]
    return AUTO_DRIVING_CN_PROFILE
