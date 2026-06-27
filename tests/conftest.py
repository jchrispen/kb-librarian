from types import MethodType


def pytest_sessionstart(session):
    terminal_reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal_reporter is None:
        return

    def write_progress_information_filling_space(self):
        msg = self._get_progress_information_message()
        width = self._width_of_current_line
        fill = self._tw.fullwidth - width - 1
        self.write(msg.rjust(fill), flush=True)

    terminal_reporter._write_progress_information_filling_space = MethodType(
        write_progress_information_filling_space,
        terminal_reporter,
    )
