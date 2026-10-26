from collections.abc import Iterator, Mapping, Sequence


class Headers(Mapping[str, str]):
    """Case-insensitive response fields, preserving every value per field name."""
    def __init__(self, values: Mapping[str, Sequence[str]] | None = None, *,
                 fallback: Mapping[str, str] | None = None) -> None:
        values = values if values is not None else {k: [v] for k, v in (fallback or {}).items()}
        self._fields = {name: tuple(items) for name, items in values.items() if items}
        self._names = {name.lower(): name for name in self._fields}

    def __getitem__(self, name: str) -> str:
        return self._fields[self._names[name.lower()]][0]

    def __iter__(self) -> Iterator[str]:
        return iter(self._fields)

    def __len__(self) -> int:
        return len(self._fields)

    def get_list(self, name: str) -> list[str]:
        key = self._names.get(name.lower())
        return list(self._fields[key]) if key is not None else []

    def multi_items(self) -> list[tuple[str, str]]:
        return [(name, value) for name, values in self._fields.items() for value in values]

    def __repr__(self) -> str:
        return f"Headers({self.multi_items()!r})"
