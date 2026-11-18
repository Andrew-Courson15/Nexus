"""Bring an existing .env up to date: replace settings that still hold an *old default*.

Values the user changed themselves are never touched. Run by the setup scripts.
"""
from pathlib import Path

# setting -> (old default, new default)
UPGRADES = {
    "NEXUS_SEMANTIC_MIN_SIMILARITY": ("0.5", "0.6"),
    "NEXUS_SEMANTIC_WINDOW": ("0.15", "0.12"),
}


def upgrade(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)  # keeps Windows line endings
    changed = []
    for i, line in enumerate(lines):
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key in UPGRADES and value.strip() == UPGRADES[key][0]:
            ending = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = f"{key}={UPGRADES[key][1]}{ending}"
            changed.append(f"{key} {UPGRADES[key][0]} -> {UPGRADES[key][1]}")
    if changed:
        path.write_text("".join(lines), encoding="utf-8", newline="")
    return changed


if __name__ == "__main__":
    for change in upgrade(Path(__file__).resolve().parent.parent / ".env"):
        print(f"Updated setting: {change}")
