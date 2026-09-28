"""Install Ollama from inside NEXUS: only on this computer, only from ollama.com, with progress."""
import time

import httpx
import pytest

from app.services import lock, ollama_setup


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    ollama_setup.reset()
    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})
    monkeypatch.setattr(lock, "_LOCAL_NAMES", lock._LOCAL_NAMES | {"testserver"})
    yield
    ollama_setup.reset()


class Stream:
    def __init__(self, body, host="release-assets.githubusercontent.com"):
        self.body, self.url, self.headers = body, httpx.URL(f"https://{host}/f"), {"content-length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self, _n):
        yield self.body


def test_unsupported_here_and_only_on_this_computer(client, monkeypatch):
    monkeypatch.setattr(ollama_setup, "supported", lambda: False)
    st = client.get("/api/ollama/install").json()
    assert st["supported"] is False and st["state"] == "idle"
    assert client.post("/api/ollama/install").status_code == 400
    phone = {"x-forwarded-for": "100.64.0.7"}
    assert client.post("/api/ollama/install", headers=phone).status_code == 403


def _pretend_supported(monkeypatch):
    """As on Windows, without changing the platform for everything else in this process."""
    monkeypatch.setattr(ollama_setup, "supported", lambda: True)
    monkeypatch.setattr(ollama_setup, "installed", lambda: False)
    monkeypatch.setitem(ollama_setup.DOWNLOADS, ollama_setup.sys.platform, "https://ollama.com/download/OllamaSetup.exe")


def test_downloads_installs_and_reports_progress(client, monkeypatch):
    _pretend_supported(monkeypatch)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Stream(b"MZ" + b"x" * 100))
    installed = []
    monkeypatch.setattr(ollama_setup, "_install", lambda f: installed.append(f.read_bytes()[:2]))
    assert client.post("/api/ollama/install").status_code == 202
    for _ in range(100):
        st = client.get("/api/ollama/install").json()
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert st["state"] == "done" and st["done_bytes"] == 102 and installed == [b"MZ"]


def test_refuses_a_download_from_elsewhere(monkeypatch):
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Stream(b"MZ", host="evil.example"))
    with pytest.raises(ollama_setup.OllamaSetupError):
        ollama_setup._download("https://ollama.com/download/OllamaSetup.exe")


def test_a_failed_install_is_shown(monkeypatch):
    _pretend_supported(monkeypatch)
    monkeypatch.setattr(ollama_setup, "_download", lambda url: (_ for _ in ()).throw(ollama_setup.OllamaSetupError("offline")))
    ollama_setup._run()
    assert ollama_setup.status()["state"] == "error" and ollama_setup.status()["error"] == "offline"
