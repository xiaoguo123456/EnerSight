"""全目录气象按网格落盘，内存仅保留小型索引和最近几个网格。"""

import hashlib
import json
import shutil
from collections import OrderedDict
from datetime import date
from pathlib import Path


class WeatherCells:
    def __init__(self, folder: Path, references: dict | None = None):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.references = dict(references or {})
        self._recent = OrderedDict()

    def _path(self, key):
        name = self.references.get(key)
        if (
            not isinstance(name, str)
            or len(name) != 64
            or any(c not in "0123456789abcdef" for c in name)
        ):
            return None
        return self.folder / f"{name}.json"

    def __contains__(self, key):
        return self.get(key) is not None

    def __iter__(self):
        return iter(self.references)

    def __len__(self):
        return len(self.references)

    def get(self, key, default=None):
        if key in self._recent:
            self._recent.move_to_end(key)
            return self._recent[key]
        path = self._path(key)
        if path is None:
            return default
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            self.references.pop(key, None)
            return default
        self._remember(key, raw)
        return raw

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def _remember(self, key, value):
        self._recent[key] = value
        self._recent.move_to_end(key)
        while len(self._recent) > 8:
            self._recent.popitem(last=False)

    def __setitem__(self, key, value):
        content = json.dumps(value, ensure_ascii=False, allow_nan=False)
        name = hashlib.sha256(content.encode()).hexdigest()
        path = self.folder / f"{name}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content)
        tmp.replace(path)
        self.references[key] = name
        self._remember(key, value)

    def prune_before(self, cutoff: date):
        """按模型清理过期日期缓存，保留当前缓存和不可变预测留档。"""
        for folder in self.folder.parent.iterdir():
            if folder == self.folder or folder.is_symlink() or not folder.is_dir():
                continue
            try:
                day = date.fromisoformat(folder.name)
            except ValueError:
                continue
            if day < cutoff:
                shutil.rmtree(folder)
