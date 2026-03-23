"""
dashboard.py — Rich terminal dashboard.

Renders a live, auto-refreshing view with four panels:
  1. Watched Markets     — ticker, mid-price, 24h volume, last updated
  2. Signals Today       — every signal fired since midnight
  3. Open Positions      — contracts held + estimated P&L
  4. Bot Status          — mode (dry run / live), poll count, errors

Call `Dashboard.render(...)` each poll cycle to update the display.
For a live auto-refresh loop use `Dashboard.live_render(...)` with Rich Live.
"""

import logging
from datetime import datetime
from typing import Optional

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
import database

logger = logging.getLogger(__name__)
console = Console()


class Dashboard:
    """Manages the Rich terminal UI."""

    def __init__(self) -> None:
        self._live: Optional[Live] = None
        self._poll_count = 0
        self._error_count = 0
        self._start_time = datetime.utcnow()

    # ── Public API ────────────────────────────────────────────────────────────

    def start_live(self) -> "Dashboard":
        """Enter Rich's live-update context. Use as a context manager."""
        self._live = Live(
            self._build_layout([]),
            console=console,
            refresh_per_second=1,
            screen=True,
        )
        self._live.__enter__()
        return self

    def stop_live(self) -> None:
        """Exit Rich's live-update context."""
        if self._live:
            self._live.__exit__(None, None, None)

    def update(self, markets: list[dict], positions: list[dict],
               error: str | None = None) -> None:
        """
        Refresh the dashboard with the latest data.

        Args:
            markets:   List of market dicts (from KalshiClient.get_markets)
            positions: List of position dicts (from ExecutionEngine)
            error:     Optional error message to surface in the status panel
        """
        self._poll_count += 1
        if error:
            self._error_count += 1

        layout = self._build_layout(markets, positions, error)

        if self._live:
            self._live.update(layout)
        else:
            console.print(layout)

    def print_signal(self, signal) -> None:
        """Print a signal alert inline (used when not in live mode)."""
        color = {"MOMENTUM": "yellow", "LIQUIDITY": "cyan",
                 "MISPRICING": "magenta"}.get(signal.signal_type, "white")
        console.print(
            f"[bold {color}][{signal.signal_type}][/] "
            f"[white]{signal.ticker}[/] — {signal.detail} → "
            f"[bold green]{signal.recommended}[/]"
        )

    # ── Layout builders ───────────────────────────────────────────────────────

    def _build_layout(self, markets: list[dict],
                      positions: list[dict] | None = None,
                      error: str | None = None) -> Layout:
        layout = Layout(name="root")
        layout.split_column(
            Layout(name="top",    ratio=2),
            Layout(name="bottom", ratio=3),
        )
        layout["top"].split_row(
            Layout(name="markets", ratio=3),
            Layout(name="status",  ratio=1),
        )
        layout["bottom"].split_row(
            Layout(name="signals",   ratio=2),
            Layout(name="positions", ratio=1),
        )

        layout["markets"].update(self._markets_panel(markets))
        layout["status"].update(self._status_panel(error))
        layout["signals"].update(self._signals_panel())
        layout["positions"].update(self._positions_panel(positions or []))

        return layout

    def _markets_panel(self, markets: list[dict]) -> Panel:
        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold blue",
            expand=True,
        )
        table.add_column("Ticker",      style="cyan",  no_wrap=True, max_width=25)
        table.add_column("Title",       style="white", no_wrap=True, max_width=40)
        table.add_column("YES Bid",     justify="right", style="green")
        table.add_column("YES Ask",     justify="right", style="red")
        table.add_column("Volume",      justify="right")
        table.add_column("Updated",     justify="right", style="dim")

        for m in markets[:30]:  # cap rows to avoid overflow
            yes_bid = _fmt_price(m.get("yes_bid"))
            yes_ask = _fmt_price(m.get("yes_ask"))
            volume  = str(m.get("volume", "—"))
            updated = _fmt_time(m.get("close_time") or m.get("last_price_time"))
            table.add_row(
                m.get("ticker", "")[:25],
                m.get("title", "")[:40],
                yes_bid,
                yes_ask,
                volume,
                updated,
            )

        count = len(markets)
        return Panel(
            table,
            title=f"[bold blue]Watched Markets ({count})[/]",
            border_style="blue",
        )

    def _signals_panel(self) -> Panel:
        signals = database.get_signals_today()

        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold yellow",
            expand=True,
        )
        table.add_column("Time",     style="dim",    no_wrap=True, width=8)
        table.add_column("Type",     no_wrap=True,   width=11)
        table.add_column("Ticker",   style="cyan",   no_wrap=True, max_width=22)
        table.add_column("Price",    justify="right")
        table.add_column("Action",   justify="right")
        table.add_column("Detail",   style="dim",    no_wrap=False, max_width=35)

        type_colors = {
            "MOMENTUM":  "yellow",
            "LIQUIDITY": "cyan",
            "MISPRICING": "magenta",
        }

        for sig in signals[:20]:
            color = type_colors.get(sig["signal_type"], "white")
            table.add_row(
                sig["triggered_at"][11:19],
                f"[{color}]{sig['signal_type']}[/]",
                sig["ticker"][:22],
                f"{sig['current_price']:.2f}" if sig["current_price"] else "—",
                f"[bold green]{sig['recommended']}[/]",
                sig["detail"][:35] if sig["detail"] else "",
            )

        return Panel(
            table,
            title=f"[bold yellow]Signals Today ({len(signals)})[/]",
            border_style="yellow",
        )

    def _positions_panel(self, positions: list[dict]) -> Panel:
        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold green",
            expand=True,
        )
        table.add_column("Ticker",    style="cyan",  no_wrap=True)
        table.add_column("Contracts", justify="right")
        table.add_column("Avg Price", justify="right")
        table.add_column("P&L ($)",   justify="right")

        total_pnl = 0.0
        for pos in positions:
            pnl = pos.get("realised_pnl", 0.0)
            total_pnl += pnl
            pnl_color = "green" if pnl >= 0 else "red"
            table.add_row(
                pos["ticker"][:18],
                str(pos.get("contracts", 0)),
                f"{pos.get('avg_price', 0):.2f}",
                f"[{pnl_color}]{pnl:+.2f}[/]",
            )

        pnl_color = "green" if total_pnl >= 0 else "red"
        return Panel(
            table,
            title=(
                f"[bold green]Open Positions ({len(positions)}) — "
                f"P&L: [{pnl_color}]{total_pnl:+.2f}[/][/]"
            ),
            border_style="green",
        )

    def _status_panel(self, error: str | None) -> Panel:
        mode_text = (
            Text("DRY RUN", style="bold yellow")
            if config.DRY_RUN
            else Text("LIVE", style="bold red")
        )

        uptime = datetime.utcnow() - self._start_time
        uptime_str = str(uptime).split(".")[0]  # trim microseconds

        lines = [
            f"Mode:         {mode_text}",
            f"Poll #:       {self._poll_count}",
            f"Poll every:   {config.POLL_INTERVAL_SECONDS}s",
            f"Uptime:       {uptime_str}",
            f"Errors:       {self._error_count}",
            f"Max size:     {config.MAX_POSITION_SIZE} contracts",
            f"Max exposure: ${config.MAX_PORTFOLIO_EXPOSURE:.0f}",
            f"",
            f"Last update:  {datetime.utcnow().strftime('%H:%M:%S')} UTC",
        ]

        if error:
            lines.append(f"\n[bold red]ERROR:[/] {error[:60]}")

        content = "\n".join(str(l) for l in lines)
        return Panel(
            content,
            title="[bold white]Bot Status[/]",
            border_style="white",
        )


# ── Utility helpers ───────────────────────────────────────────────────────────

def _fmt_price(value) -> str:
    """Format a Kalshi price (in cents) as a two-decimal fraction string."""
    if value is None:
        return "—"
    try:
        return f"{int(value) / 100:.2f}"
    except (ValueError, TypeError):
        return str(value)


def _fmt_time(iso_str: str | None) -> str:
    """Shorten an ISO timestamp to HH:MM."""
    if not iso_str:
        return "—"
    try:
        return iso_str[11:16]
    except (IndexError, TypeError):
        return "—"
