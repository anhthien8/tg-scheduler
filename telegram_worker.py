"""Telegram/background-engine process entrypoint for split runtime phase 1."""
import asyncio
import logging
import signal

import database as db
import engine_lifecycle as lifecycle

logger = logging.getLogger("tg-scheduler.worker")
_stop_event: asyncio.Event | None = None
_startup_task: asyncio.Task | None = None
_heartbeat_task: asyncio.Task | None = None
_consumer_task: asyncio.Task | None = None
_runtime_error: BaseException | None = None
_owns_runtime = False

CONSUMER_POLL_SECONDS = 2.0
CONSUMER_LEASE_SECONDS = 120.0


async def _default_dispatch(command: str, payload: dict) -> dict:
    import ipc_dispatcher
    return await ipc_dispatcher.dispatch(command, payload)


async def consume_one(worker_id: str, *, dispatch=None) -> bool:
    """Claim and execute one queued command. Returns True if one was processed."""
    import runtime_commands as rc
    dispatch = dispatch or _default_dispatch
    async with db.get_db() as conn:
        await rc.init_schema(conn)
        await rc.recover(conn)
        item = await rc.claim(conn, worker_id, lease_seconds=CONSUMER_LEASE_SECONDS)
    if item is None:
        return False
    try:
        result = await dispatch(item["command"], item["payload"])
    except BaseException as exc:  # noqa: BLE001 — every failure must be recorded, then re-raised if fatal
        error = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
        async with db.get_db() as conn:
            try:
                await rc.fail(conn, item["id"], item["owner_token"], str(error))
            except rc.OwnershipError:
                logger.warning("Lease lost for %s before failure could be recorded", item["id"])
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        logger.warning("Command %s (%s) failed: %s", item["id"], item["command"], error)
        return True
    async with db.get_db() as conn:
        try:
            await rc.complete(conn, item["id"], item["owner_token"], result if isinstance(result, dict) else {})
        except rc.OwnershipError:
            # Lease expired mid-execution → status already 'unknown'; do NOT claim success.
            logger.warning("Lease lost for %s; result dropped, status stays unknown", item["id"])
    return True


async def _consume_loop() -> None:
    worker_id = f"worker-{__import__('os').getpid()}"
    while True:
        try:
            processed = await consume_one(worker_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Command consumer iteration failed")
            processed = False
        if not processed:
            await asyncio.sleep(CONSUMER_POLL_SECONDS)


async def _heartbeat() -> None:
    while True:
        await lifecycle.write_status("ready")
        await asyncio.sleep(15)


async def start_runtime() -> None:
    global _startup_task, _heartbeat_task, _consumer_task, _owns_runtime
    lifecycle.acquire_data_lock()
    _owns_runtime = True
    await db.init_db()
    # Queue schema up-front: the consumer must never race a lazy CREATE TABLE.
    import runtime_commands as rc
    async with db.get_db() as conn:
        await rc.init_schema(conn)
    await lifecycle.write_status("starting")
    try:
        _startup_task = await lifecycle.start_engines()
        # Do not advertise readiness until account/watchers/Discord startup completes.
        await _startup_task
        _heartbeat_task = asyncio.create_task(_heartbeat())
        _consumer_task = asyncio.create_task(_consume_loop())
        await lifecycle.write_status("ready")
    except BaseException as exc:
        await lifecycle.write_status("error", error=str(exc))
        raise


async def stop_runtime() -> None:
    global _heartbeat_task, _consumer_task, _owns_runtime
    if not _owns_runtime:
        return
    # Cancel and await heartbeat/consumer before engine cleanup; never overwrite error state.
    if _consumer_task:
        if not _consumer_task.done():
            _consumer_task.cancel()
        try:
            await _consumer_task
        except (asyncio.CancelledError, Exception):
            pass
        _consumer_task = None
    if _heartbeat_task:
        if not _heartbeat_task.done():
            _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        _heartbeat_task = None
    try:
        await lifecycle.stop_engines(_startup_task)
        if _runtime_error is None:
            await lifecycle.write_status("stopped")
    finally:
        try:
            await db.close_db()
        finally:
            lifecycle.release_data_lock()
            _owns_runtime = False


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Install POSIX handlers or Windows-compatible signal callbacks."""
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _stop_event.set)
        except (NotImplementedError, RuntimeError):
            # Windows ProactorEventLoop has no add_signal_handler; signal.signal
            # runs its callback on the main thread, so call_soon_threadsafe is safe.
            try:
                signal.signal(sig, lambda *_args, lp=loop: lp.call_soon_threadsafe(_stop_event.set))
            except (OSError, ValueError):
                logger.warning("Could not install handler for %s", name)


async def run() -> None:
    global _stop_event, _runtime_error
    _stop_event = asyncio.Event()
    _runtime_error = None
    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop)
    try:
        await start_runtime()
        waiters = [asyncio.create_task(_stop_event.wait())]
        if _heartbeat_task is not None:
            waiters.append(_heartbeat_task)
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        waiters[0].cancel()
        if _heartbeat_task is not None and _heartbeat_task in done:
            await _heartbeat_task  # surface heartbeat write/sleep failure
    except BaseException as exc:
        _runtime_error = exc
        if _owns_runtime:
            try:
                await lifecycle.write_status("error", error=str(exc))
            except Exception:
                logger.exception("Could not persist worker error status")
        raise
    finally:
        if _owns_runtime:
            await stop_runtime()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(run())
