"""
Screen-scrape manifest loader.

Loads TOML pattern files from the manifests/ directory and provides a
function to classify pane state based on the captured terminal text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

MANIFESTS_DIR = Path(__file__).resolve().parent.parent / "manifests"


@dataclass
class Rule:
    state: str
    pattern: re.Pattern
    exclude: re.Pattern | None


def load_manifest(path: Path) -> list[Rule]:
    """Load rules from a TOML manifest file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    rules = []
    for entry in data.get("rule", []):
        state = entry["state"]
        pattern = re.compile(entry["pattern"])
        exclude = re.compile(entry["exclude"]) if "exclude" in entry else None
        rules.append(Rule(state=state, pattern=pattern, exclude=exclude))
    return rules


def load_all_manifests(manifests_dir: Path = MANIFESTS_DIR) -> dict[str, list[Rule]]:
    """Load all manifests, keyed by agent name (filename without extension)."""
    result = {}
    if not manifests_dir.exists():
        return result
    for path in sorted(manifests_dir.glob("*.toml")):
        name = path.stem
        result[name] = load_manifest(path)
    return result


def classify(text: str, rules: list[Rule]) -> str | None:
    """Apply rules to captured text. Returns the state of the first match, or None."""
    for rule in rules:
        if rule.pattern.search(text):
            if rule.exclude and rule.exclude.search(text):
                continue
            return rule.state
    return None
