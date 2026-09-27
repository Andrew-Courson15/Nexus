"""The system-tray starter (tray.py): Quit stops NEXUS; without a tray NEXUS still runs."""
import threading
import types

import tray


class FakeServer:
    def __init__(self):
        self.should_exit = False
        self.ran = threading.Event()

    def run(self):
        self.ran.set()
        while not self.should_exit:
            threading.Event().wait(0.01)


def test_without_a_tray_nexus_just_runs(monkeypatch):
    server = FakeServer()
    server.should_exit = True
    monkeypatch.setattr(tray, "_server", lambda: server)
    monkeypatch.setattr(tray, "_load_tray", lambda: None)
    tray.main()
    assert server.ran.is_set()


def test_quit_in_the_tray_menu_stops_the_server(monkeypatch):
    server = FakeServer()
    opened, notes = [], []
    monkeypatch.setattr(tray, "_server", lambda: server)
    monkeypatch.setattr(tray.webbrowser, "open", opened.append)

    class Icon:
        def __init__(self, name, image, title, menu):
            self.title, self.items, self.stopped = title, menu, False

        def run(self):  # a user left-clicks (Open), backs up, then chooses Quit
            server.ran.wait(2)
            menu = {i.text: i for i in self.items}
            assert list(menu) == ["Open NEXUS", "Back up now", "Start with Windows", "Open data folder", "Quit NEXUS"]
            menu["Open NEXUS"].action(self, None)
            menu["Back up now"].action(self, None)
            menu["Quit NEXUS"].action(self, None)

        def stop(self):
            self.stopped = True

        def notify(self, message, title):
            notes.append(message)

    class Item:
        def __init__(self, text, action, default=False, checked=None, visible=True):
            self.text, self.action, self.default = text, action, default

    fake = types.SimpleNamespace(Icon=Icon, MenuItem=Item,
                                 Menu=type("Menu", (), {"SEPARATOR": None, "__new__": lambda cls, *items: [i for i in items if i]}))
    monkeypatch.setattr(tray, "_load_tray", lambda: (fake, object()))
    from app.services import backup

    monkeypatch.setattr(backup.runner, "start_now", lambda **kw: True)
    tray.main()
    assert server.should_exit and opened and opened[0].startswith("http://localhost:")
    assert notes == ["Backing up NEXUS in the background…"]


def test_start_with_windows_is_off_elsewhere(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "linux")
    assert tray.starts_with_windows() is False
    tray.set_start_with_windows(True)  # does nothing outside Windows
