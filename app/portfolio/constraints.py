from __future__ import annotations

from dataclasses import dataclass


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
    def from_config(cls, config_loader) -> PortfolioConstraints:
        """Load constraints from ConfigLoader (Redis → DB → defaults)."""
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
            max_positions=_get('portfolio', 'max_positions', 10),
            max_position_size=_get('portfolio', 'max_position_pct', 0.05),
            min_position_size=_get('portfolio', 'min_position_pct', 0.01),
            max_sector_exposure=_get('portfolio', 'max_sector_pct', 0.25),
            turnover_limit_pct=_get('portfolio', 'turnover_limit_pct', 0.50),
            cash_buffer_pct=_get('portfolio', 'cash_buffer_pct', 0.05),
            objective=_get('portfolio', 'objective', 'max_sharpe'),
            vol_target=_get('portfolio', 'vol_target', 0.12),
        )
