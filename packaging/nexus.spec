# PyInstaller recipe for the downloadable NEXUS app (no Python needed on the computer).
#   pip install pyinstaller
#   pyinstaller packaging/nexus.spec --noconfirm
# The result is in dist/NEXUS (Windows/Linux: a folder with NEXUS.exe / NEXUS) or dist/NEXUS.app (Mac).
# Your data and .env then live in the per-user app folder (see app/config.py: USER_DIR), not next to the app.
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - SPECPATH is set by PyInstaller

datas = [(os.path.join(ROOT, "static"), "static"), (os.path.join(ROOT, "app", "templates"), os.path.join("app", "templates"))]
binaries = []
hiddenimports = collect_submodules("app") + collect_submodules("uvicorn") + ["python_multipart", "multipart"]
# Optional parts: bundled when they're installed on the build machine (voice notes, HEIC, PDF, tray icon).
optional = ["faster_whisper", "ctranslate2", "tokenizers", "pillow_heif", "pymupdf", "qrcode"]
if sys.platform in ("win32", "darwin"):
    optional.append("pystray")  # the tray / menu-bar icon
for package in optional:
    try:
        d, b, h = collect_all(package)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(ROOT, "tray.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tkinter", "gi", "Xlib", "cryptography"] + ([] if sys.platform in ("win32", "darwin") else ["pystray"]),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="NEXUS",
    console=False,  # no terminal window: NEXUS lives in the tray (Windows) and the browser
    icon=os.path.join(ROOT, "static", "icons", "nexus.ico") if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="NEXUS")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="NEXUS.app",
        icon=os.path.join(ROOT, "packaging", "NEXUS.icns") if os.path.exists(os.path.join(ROOT, "packaging", "NEXUS.icns")) else None,
        bundle_identifier="local.nexus.app",
        info_plist={"CFBundleShortVersionString": __import__("re").search(
            r'__version__ = "([^"]+)"', open(os.path.join(ROOT, "app", "__init__.py")).read()).group(1)},
    )
