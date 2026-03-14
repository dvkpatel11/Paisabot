from __future__ import annotations

import pandas as pd
import structlog

from app.portfolio.constraints import PortfolioConstraints

logger = structlog.get_logger()

# Defensive ETFs favored in risk_off regime
DEFENSIVE_SECTORS = frozenset({'Health Care', 'Utilities', 'Consumer Staples'})
CYCLICAL_SECTORS = frozenset({'Energy', 'Financials', 'Consumer Discretionary'})


class CandidateSelector:
    """Select portfolio candidates from ranked signals.

    Applies regime-based adjustments:
      - risk_off: cap at 5 positions, prefer defensives, block cyclicals
      - trending/rotation/consolidation: standard max_positions from config
    """

    def __init__(self, config_loader=None):
        self._config = config_loader
        self._log = logger.bind(component='candidate_selector')

    def select(
        self,
        signals: dict[str, dict],
        constraints: PortfolioConstraints,
        regime: str = 'consolidation',
        sector_map: dict[str, str] | None = None,
    ) -> list[str]:
        """Select top-N long candidates respecting regime and constraints.

        Args:
            signals: {symbol: signal_dict} from SignalGenerator.run().
                Each signal_dict must have 'signal_type', 'composite_score', 'rank'.
            constraints: portfolio constraints.
            regime: current market regime.
            sector_map: {symbol: sector} for regime-based filtering.

        Returns:
            Ordered list of candidate symbols (best first).
        """
        if sector_map is None:
            sector_map = {}

        # Filter to long signals only
        longs = [
            (sym, sig) for sym, sig in signals.items()
            if sig.get('signal_type') == 'long' and sig.get('tradable', True)
        ]

        if not longs:
            self._log.info('no_long_candidates')
            return []

        # Sort by composite score descending
        longs.sort(key=lambda x: x[1].get('composite_score', 0), reverse=True)

        # Determine max positions
        max_pos = constraints.max_positions
        if regime == 'risk_off':
            max_pos = min(max_pos, 5)

        # Apply regime-based filtering
        if regime == 'risk_off':
            candidates = self._risk_off_filter(longs, max_pos, sector_map)
        else:
            candidates = [sym for sym, _ in longs[:max_pos]]

        self._log.info(
            'candidates_selected',
            regime=regime,
            max_positions=max_pos,
            eligible=len(longs),
            selected=len(candidates),
            symbols=candidates,
        )

        return candidates

    def _risk_off_filter(
        self,
        longs: list[tuple[str, dict]],
        max_pos: int,
        sector_map: dict[str, str],
    ) -> list[str]:
        """In risk_off: prefer defensive sectors, block cyclicals."""
        defensive = []
        other = []

        for sym, sig in longs:
            sector = sector_map.get(sym, 'Unknown')
            if sector in CYCLICAL_SECTORS:
                continue  # block cyclicals in risk_off
            if sector in DEFENSIVE_SECTORS:
                defensive.append(sym)
            else:
                other.append(sym)

        # Fill with defensives first, then others
        candidates = defensive[:max_pos]
        remaining = max_pos - len(candidates)
        if remaining > 0:
            candidates.extend(other[:remaining])

        return candidates
