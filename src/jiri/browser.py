"""日课分析的全屏终端浏览器。"""

from __future__ import annotations

from datetime import date

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from .config import Config
from .service import render_daily_analyses


class AnalysisBrowser(App[None]):
    """以实心全屏布局浏览已保存的每日分析。"""

    CSS = """
    Screen {
        background: #0d1117;
        color: #e6edf3;
    }

    #title {
        height: 3;
        padding: 1 2;
        background: #161b22;
        color: #58d6ff;
        text-style: bold;
    }

    #content {
        height: 1fr;
        padding: 1 3;
        background: #0d1117;
        scrollbar-color: #58d6ff;
        scrollbar-color-hover: #79e2ff;
    }

    #analysis {
        width: 100%;
        color: #e6edf3;
    }

    #status {
        height: 3;
        padding: 1 2;
        background: #161b22;
        color: #8b949e;
    }
    """

    BINDINGS = [
        ("left", "previous_day", "前一天"),
        ("right", "next_day", "后一天"),
        ("up", "scroll_up", "向上滚动"),
        ("down", "scroll_down", "向下滚动"),
        ("q", "quit", "退出"),
    ]

    def __init__(self, config: Config, dates: list[date]) -> None:
        super().__init__()
        self.config = config
        self.dates = dates
        self.index = len(dates) - 1

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with VerticalScroll(id="content"):
            yield Static(id="analysis")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.query_one("#content", VerticalScroll).focus()
        self._refresh_day()

    def action_previous_day(self) -> None:
        self.index = max(0, self.index - 1)
        self._refresh_day()

    def action_next_day(self) -> None:
        self.index = min(len(self.dates) - 1, self.index + 1)
        self._refresh_day()

    def action_scroll_up(self) -> None:
        self.query_one("#content", VerticalScroll).scroll_up(animate=False)

    def action_scroll_down(self) -> None:
        self.query_one("#content", VerticalScroll).scroll_down(animate=False)

    def _refresh_day(self) -> None:
        current_date = self.dates[self.index]
        self.query_one("#title", Static).update(
            Text(f"日课分析  {current_date.isoformat()}  ({self.index + 1}/{len(self.dates)})", style="bold cyan")
        )
        self.query_one("#analysis", Static).update(Text(render_daily_analyses(self.config, current_date)))
        self.query_one("#status", Static).update(
            Text("← 前一天    → 后一天    ↑ / ↓ 滚动正文    q 退出", style="cyan")
        )
        self.query_one("#content", VerticalScroll).scroll_home(animate=False)


def run_browser(config: Config, dates: list[date]) -> None:
    """启动全屏浏览器。"""

    AnalysisBrowser(config, dates).run()
