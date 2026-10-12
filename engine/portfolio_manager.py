import logging
from core.config import settings

logger = logging.getLogger(__name__)

class PortfolioManager:
    def __init__(self, paper_trader):
        self.paper_trader = paper_trader

    async def check_emergency_guard(self, symbol: str, regime: str, phase: str, current_price: float):
        """
        The 'Trapped Position' Emergency Guard.
        When PHASE 1: IMPULSE SHOCK triggers, iterate through all currently open active positions.
        Rule: If an open position has leverage > 10x AND is currently in profit,
        immediately execute an emergency order modification moving the Stop Loss to Breakeven (Entry Price).
        If the price has already slipped past breakeven, close the position at market.
        """
        if regime == "HIGH_VOLATILITY" and phase == "PHASE 1":
            open_positions = await self.paper_trader.get_open_positions(symbol)
            for position in open_positions:
                lev_str = str(position.get('leverage', '1'))
                try:
                    leverage = float(lev_str.replace('x', ''))
                except (ValueError, TypeError):
                    leverage = 1.0
                
                pnl_pct = position.get('pnl_pct', 0.0)
                
                # If leverage >= threshold and position is in profit >= threshold
                if leverage >= settings.emergency_guard_leverage_threshold and pnl_pct >= settings.emergency_guard_pnl_threshold:
                    old_sl = position['stop_loss']
                    entry_price = position['entry']
                    direction = position.get('direction', 'LONG').upper()
                    
                    if old_sl != entry_price:
                        if direction == 'LONG' and current_price <= entry_price:
                            logger.warning(f"[EMERGENCY GUARD] LONG Slippage detected for {symbol}. Market price {current_price} already below entry {entry_price}. Executing Market Close.")
                            await self.paper_trader.close_position(position['id'], current_price, "EMERGENCY_SLIPPAGE_CLOSE")
                        elif direction == 'SHORT' and current_price >= entry_price:
                            logger.warning(f"[EMERGENCY GUARD] SHORT Slippage detected for {symbol}. Market price {current_price} already above entry {entry_price}. Executing Market Close.")
                            await self.paper_trader.close_position(position['id'], current_price, "EMERGENCY_SLIPPAGE_CLOSE")
                        else:
                            await self.paper_trader.move_sl_to_be(position['id'], entry_price)
                            logger.warning(f"[EMERGENCY GUARD] Volatility shock detected. Moving SL to Breakeven for open {symbol} position to protect against liquidation.")

