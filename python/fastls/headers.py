from collections.abc import Mapping


class Headers(Mapping):
    """Case-insensitive response fields, preserving every value per field name."""
    def __init__(self, values=None, *, fallback=None):
        values = values if values is not None else {k: [v] for k, v in (fallback or {}).items()}
        self._fields = {name: tuple(items) for name, items in values.items() if items}
        self._names = {name.lower(): name for name in self._fields}

    def __getitem__(self, name):
        return self._fields[self._names[name.lower()]][0]

    def __iter__(self):
        return iter(self._fields)

    def __len__(self):
        return len(self._fields)

    def get_list(self, name):
        key = self._names.get(name.lower())
        return list(self._fields[key]) if key is not None else []

    def multi_items(self):
        return [(name, value) for name, values in self._fields.items() for value in values]

    def __repr__(self):
        return f"Headers({self.multi_items()!r})"
