from __future__ import annotations

from dataclasses import dataclass


# ── per-asset-class defaults ─────────────────────────────────────
ETF_CONSTRAINT_DEFAULTS = {
    'max_positions': 10,
    'max_position_size': 0.05,
    'min_position_size': 0.01,
    'max_sector_exposure': 0.25,
    'turnover_limit_pct': 0.50,
    'cash_buffer_pct': 0.05,
    'objective': 'max_sharpe',
    'vol_target': 0.12,
}

STOCK_CONSTRAINT_DEFAULTS = {
    'max_positions': 15,
    'max_position_size': 0.05,
    'min_position_size': 0.01,
    'max_sector_exposure': 0.20,
    'turnover_limit_pct': 0.40,
    'cash_buffer_pct': 0.05,
    'objective': 'max_sharpe',
    'vol_target': 0.15,
}

CONSTRAINT_DEFAULTS_BY_CLASS = {
    'etf': ETF_CONSTRAINT_DEFAULTS,
    'stock': STOCK_CONSTRAINT_DEFAULTS,
}


@dataclass
class PortfolioConstraints:
    """Portfolio construction constraints.

    All weight values are expressed as fractions (0.05 = 5%).
    """

    max_positions: int = 10
    max_position_size: float = 0.05
    min_position_size: float = 0.01
    max_sector_exposure: float = 0.25
    turnover_limit_pct: float = 0.50
    cash_buffer_pct: float = 0.05
    objective: str = 'max_sharpe'
    vol_target: float = 0.12

    @classmethod
    def from_config(
        cls,
        config_loader,
        asset_class: str = 'etf',
    ) -> PortfolioConstraints:
        """Load constraints from ConfigLoader (Redis → DB → defaults).

        Uses asset-class-specific defaults. Config keys are read from
        the 'portfolio' category (shared) — per-account overrides come
        from the Account model at the orchestrator level.
        """
        defaults = CONSTRAINT_DEFAULTS_BY_CLASS.get(
            asset_class, ETF_CONSTRAINT_DEFAULTS,
        )

        def _get(cat: str, key: str, default):
            try:
                if isinstance(default, int):
                    val = config_loader.get_float(cat, key)
                    return int(val) if val is not None else default
                elif isinstance(default, float):
                    val = config_loader.get_float(cat, key)
                    return val if val is not None else default
                else:
                    val = config_loader.get(cat, key)
                    return val if val is not None else default
            except Exception:
                return default

        return cls(
            max_positions=_get('portfolio', 'max_positions', defaults['max_positions']),
            max_position_size=_get('portfolio', 'max_position_pct', defaults['max_position_size']),
            min_position_size=_get('portfolio', 'min_position_pct', defaults['min_position_size']),
            max_sector_exposure=_get('portfolio', 'max_sector_pct', defaults['max_sector_exposure']),
            turnover_limit_pct=_get('portfolio', 'turnover_limit_pct', defaults['turnover_limit_pct']),
            cash_buffer_pct=_get('portfolio', 'cash_buffer_pct', defaults['cash_buffer_pct']),
            objective=_get('portfolio', 'objective', defaults['objective']),
            vol_target=_get('portfolio', 'vol_target', defaults['vol_target']),
        )

    @classmethod
    def for_etf(cls) -> PortfolioConstraints:
        """Create ETF constraints with hardcoded defaults (no config)."""
        return cls(**ETF_CONSTRAINT_DEFAULTS)

    @classmethod
    def for_stock(cls) -> PortfolioConstraints:
        """Create stock constraints with hardcoded defaults (no config)."""
        return cls(**STOCK_CONSTRAINT_DEFAULTS)
