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
    opened = []
    monkeypatch.setattr(tray, "_server", lambda: server)
    monkeypatch.setattr(tray.webbrowser, "open", opened.append)

    class Icon:
        def __init__(self, name, image, title, menu):
            self.title, self.items, self.stopped = title, menu, False

        def run(self):  # a user left-clicks (Open), then chooses Quit
            server.ran.wait(2)
            self.items[0].action(self, None)
            self.items[-1].action(self, None)

        def stop(self):
            self.stopped = True

    class Item:
        def __init__(self, text, action, default=False):
            self.text, self.action, self.default = text, action, default

    fake = types.SimpleNamespace(Icon=Icon, MenuItem=Item,
                                 Menu=type("Menu", (), {"SEPARATOR": None, "__new__": lambda cls, *items: [i for i in items if i]}))
    monkeypatch.setattr(tray, "_load_tray", lambda: (fake, object()))
    tray.main()
    assert server.should_exit and opened and opened[0].startswith("http://localhost:")
