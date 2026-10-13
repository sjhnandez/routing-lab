"""Rotation state and the cached model list.

Both live next to the history inside ``PROBER_HISTORY_DIR``:

  ``.rotation`` - text file with the index of the next model position;
  ``.models``   - JSON array of model ids cached from the last live
                  ``GET /v1/models`` so ``--dry-run`` can resolve the next
                  model without any network access.

The index advances only after an actual probe (never on dry-run/status), and
it is normalized with ``% len(models)`` at use time so a shrunken model list
cannot push it out of range.
"""

from __future__ import annotations

import json
from pathlib import Path

ROTATION_FILENAME = ".rotation"
MODELS_FILENAME = ".models"


def read_rotation(history_dir: Path) -> int:
    try:
        raw = (Path(history_dir) / ROTATION_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    try:
        index = int(raw)
    except ValueError:
        return 0
    return max(index, 0)


def write_rotation(history_dir: Path, index: int) -> None:
    directory = Path(history_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ROTATION_FILENAME).write_text(f"{int(index)}\n", encoding="utf-8")


def read_models(history_dir: Path) -> list[str]:
    try:
        raw = (Path(history_dir) / MODELS_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str) and item]


def write_models(history_dir: Path, models: list[str]) -> None:
    directory = Path(history_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(list(models), ensure_ascii=True, separators=(",", ":"))
    (directory / MODELS_FILENAME).write_text(payload + "\n", encoding="utf-8")


def pick(models: list[str], index: int) -> tuple[int, str]:
    """Return (position, model) for the next probe position."""
    if not models:
        raise ValueError("cannot pick a model from an empty list")
    position = index % len(models)
    return position, models[position]


def advance(index: int, model_count: int) -> int:
    """Index of the position after the one about to be probed."""
    if model_count <= 0:
        raise ValueError("model count must be positive")
    return (index + 1) % model_count
