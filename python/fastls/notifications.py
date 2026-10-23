"""Per-session asyncio wakeups. Payloads and state remain in the C ABI."""
import asyncio
import secrets
import socket

from ._native import FastlsError


class AsyncNotifier:
    def __init__(self, owner):
        self.owner = owner
        self.loop = asyncio.get_running_loop()
        self.token = secrets.token_bytes(32)
        self.ready = self.loop.create_future()
        self.server = self.writer = None
        self.tasks = set()
        self.writers = set()
        self.states = {}
        self.closed = self.registered = False
        self.failure = None

    async def start(self):
        try:
            self.server = await asyncio.start_server(self._accept, "127.0.0.1", 0,
                                                     family=socket.AF_INET, backlog=2, limit=4096)
            port = self.server.sockets[0].getsockname()[1]
            self.owner._native.call("fastls_session_watch", self.owner._session, port, self.token, len(self.token))
            self.registered = True
            await asyncio.wait_for(asyncio.shield(self.ready), 3)
            self.check()
        except BaseException as error:
            await self.aclose()
            if not isinstance(error, (asyncio.CancelledError, FastlsError)):
                raise FastlsError(f"could not start native notifications: {error}; use notifications='poll' to disable them") from error
            raise

    def _accept(self, reader, writer):
        if self.closed or self.writer is not None or len(self.tasks) >= 2:
            writer.close()
            return
        task = self.loop.create_task(self._receive(reader, writer))
        self.writers.add(writer)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _receive(self, reader, writer):
        authenticated = False
        try:
            token = await asyncio.wait_for(reader.readexactly(32), 1)
            if not secrets.compare_digest(token, self.token) or self.closed or self.writer is not None:
                return
            authenticated = True
            self.writer = writer
            self.server.close()
            if not self.ready.done():
                self.ready.set_result(None)
            while True:
                packet = await reader.read(4096)
                if not packet:
                    raise FastlsError("native notification connection closed")
                if any(byte != 1 for byte in packet):
                    raise FastlsError("invalid native notification packet")
                batch = self.owner._native.call("fastls_session_events", self.owner._session)
                handles = tuple(self.owner._handles) if batch["overflow"] else batch["handles"]
                for handle in handles:
                    if handle in self.owner._handles:
                        self.pulse(handle)
        except asyncio.CancelledError:
            if authenticated and not self.closed:
                self._fail(FastlsError("native notification reader was cancelled"))
            raise
        except Exception as error:
            if authenticated and not self.closed and not self.owner._closed:
                self._fail(FastlsError(f"native notifications failed: {error}"))
        finally:
            self.writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass

    def check(self):
        if self.closed or self.owner._closed:
            raise FastlsError("notification session is closed")
        if self.failure is not None:
            raise self.failure

    def generation(self, handle):
        self.check()
        return self.states.setdefault(handle, [0, None])[0]

    def pulse(self, handle):
        state = self.states.setdefault(handle, [0, None])
        state[0] += 1
        future, state[1] = state[1], None
        if future is not None and not future.done():
            future.set_result(None)

    async def wait(self, handle, generation):
        self.check()
        state = self.states.setdefault(handle, [0, None])
        if state[0] == generation:
            if state[1] is None:
                state[1] = self.loop.create_future()
            # A cancelled reader must not cancel another upload/read waiter.
            await asyncio.shield(state[1])
        self.check()

    def _forget(self, handle):
        state = self.states.pop(handle, None)
        if state is not None and state[1] is not None and not state[1].done():
            state[1].set_result(None)

    def _on_loop(self, callback, *args):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is self.loop:
            callback(*args)
        elif not self.loop.is_closed():
            self.loop.call_soon_threadsafe(callback, *args)

    def forget(self, handle):
        self._on_loop(self._forget, handle)

    def _fail(self, error):
        self.failure = self.failure or error
        for handle in tuple(self.states):
            self.pulse(handle)

    def _stop(self):
        if self.closed:
            return
        self.closed = True
        if self.server is not None:
            self.server.close()
        for writer in tuple(self.writers):
            writer.close()
        for task in tuple(self.tasks):
            task.cancel()
        for handle in tuple(self.states):
            self._forget(handle)
        if not self.ready.done():
            # No unobserved exception on setup cancellation.
            self.ready.set_result(None)

    def stop(self):
        if self.registered:
            self.registered = False
            self.owner._native.call("fastls_session_unwatch", self.owner._session)
        self._on_loop(self._stop)

    async def aclose(self):
        writers = tuple(self.writers)
        self.stop()
        if self.server is not None:
            await self.server.wait_closed()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if writers:
            await asyncio.gather(*(writer.wait_closed() for writer in writers), return_exceptions=True)
        self.writers.clear()
