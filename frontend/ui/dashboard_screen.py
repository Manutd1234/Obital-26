from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


def _sparkline(values: list[float], width: int = 32) -> str:
    if not values:
        return "(no reward data yet)"
    bars = "▁▂▃▄▅▆▇█"
    source = values[-width:]
    low = min(source)
    high = max(source)
    span = (high - low) or 1.0
    out: list[str] = []
    for value in source:
        index = int(((value - low) / span) * (len(bars) - 1))
        out.append(bars[max(0, min(index, len(bars) - 1))])
    return "".join(out)


class DashboardScreen(ModalScreen[None]):
    CSS = """
    DashboardScreen {
        align: center middle;
    }
    #dashboard-modal {
        width: 80%;
        height: 80%;
        border: round #666666;
        padding: 1;
        background: $surface;
    }
    #dashboard-body { height: 1fr; overflow-y: auto; }
    #dashboard-status { height: 3; }
    """

    def __init__(self, account_id: str) -> None:
        super().__init__()
        self.account_id = account_id
        self._body = Static("Loading dashboard...", id="dashboard-body")
        self._status = Static(f"Account: {account_id}", id="dashboard-status")

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard-modal"):
            yield Label(f"Rewards Dashboard ({self.account_id})")
            yield self._body
            yield Button("Close", id="dashboard-close")
            yield self._status

    def set_status(self, value: str) -> None:
        self._status.update(value)

    def set_data(self, payload: dict) -> None:
        summary = payload.get("summary", {})
        trend = payload.get("trend", [])
        session_logs = payload.get("session_logs", [])
        graph = _sparkline([float(item.get("score_percent", 0.0)) for item in trend])
        lines = [
            f"Total points: {int(summary.get('total_points', 0))}",
            f"Total quizzes: {int(summary.get('total_quizzes', 0))}",
            f"Accuracy: {float(summary.get('accuracy_percent', 0.0)):.2f}%",
            "",
            "Score trend:",
            graph,
            "",
            "Recent sessions:",
        ]
        if not session_logs:
            lines.append("- none")
        else:
            for item in session_logs[:8]:
                lines.append(
                    f"- {item.get('session_id', '')}: +{int(item.get('points_delta', 0))} pts, "
                    f"+{int(item.get('quizzes_delta', 0))} quizzes ({int(item.get('events', 0))} events)"
                )
        self._body.update("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dashboard-close":
            self.dismiss(None)
