"""Bounded upload adapters. Files remain owned by the caller."""
CHUNK_SIZE = 65536


def is_stream(data):
    if isinstance(data, str):
        return len(data) > 512 * 1024 or len(data.encode("utf-8")) > 512 * 1024
    return data is not None and (not isinstance(data, bytes) or len(data) > 512 * 1024)


def split(value):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("upload chunks must be bytes, bytearray or memoryview")
    view = memoryview(value).cast("B")
    for offset in range(0, len(view), CHUNK_SIZE):
        yield bytes(view[offset:offset + CHUNK_SIZE])


def chunks(data):
    if isinstance(data, str):
        # UTF-8 needs at most four bytes per character; avoid a whole-body copy.
        for offset in range(0, len(data), CHUNK_SIZE // 4):
            yield data[offset:offset + CHUNK_SIZE // 4].encode("utf-8")
        return
    if isinstance(data, (bytes, bytearray, memoryview)):
        yield from split(data)
    elif hasattr(data, "read"):
        while True:
            value = data.read(CHUNK_SIZE)
            if value == b"":
                return
            yield from split(value)
    else:
        iterator = iter(data)
        try:
            for value in iterator:
                yield from split(value)
        finally:
            if hasattr(iterator, "close"):
                iterator.close()


async def async_chunks(data):
    if hasattr(data, "__aiter__"):
        iterator = aiter(data)
        try:
            async for value in iterator:
                for chunk in split(value):
                    yield chunk
        finally:
            if hasattr(iterator, "aclose"):
                await iterator.aclose()
    else:
        for chunk in chunks(data):
            yield chunk
