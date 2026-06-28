from types import MethodType

import pytest


PROGRESS_COLORS = {
    "failed": "red",
    "error": "red",
    "passed": "green",
    "skipped": "yellow",
}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" or report.outcome in {"failed", "skipped"}:
        item.config._kb_last_progress_outcome = report.outcome


def pytest_sessionstart(session):
    terminal_reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal_reporter is None:
        return

    def write_progress_information_filling_space(self):
        msg = self._get_progress_information_message()
        width = self._width_of_current_line
        fill = self._tw.fullwidth - width - 1
        outcome = getattr(self.config, "_kb_last_progress_outcome", "passed")
        color = PROGRESS_COLORS.get(outcome, "yellow")
        self.write(msg.rjust(fill), flush=True, **{color: True})

    terminal_reporter._write_progress_information_filling_space = MethodType(
        write_progress_information_filling_space,
        terminal_reporter,
    )
