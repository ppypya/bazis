import asyncio
import signal
import sys
import textwrap
from unittest.mock import AsyncMock, Mock

import pytest

from config import outbox
from config.settings import Settings


@pytest.fixture
def resources(monkeypatch):
    container = Mock(close=AsyncMock())
    broker = AsyncMock()
    topology = Mock(declare=AsyncMock())
    monkeypatch.setattr(outbox, "Container", Mock(return_value=container))
    monkeypatch.setattr(outbox, "create_broker", Mock(return_value=broker))
    monkeypatch.setattr(outbox, "RabbitTopology", Mock(return_value=topology))
    return container, broker, topology


@pytest.mark.parametrize("failure", [RuntimeError("loop failed"), asyncio.CancelledError()])
async def test_loop_failure_propagates_and_closes_resources(monkeypatch, resources, failure):
    container, broker, _ = resources
    monkeypatch.setattr(outbox, "run_outbox", AsyncMock(side_effect=failure))

    with pytest.raises(type(failure)):
        await outbox.run_outbox_worker(Settings(api_key="unit-test-api-key", _env_file=None))

    broker.__aexit__.assert_awaited_once()
    container.close.assert_awaited_once()


async def test_unexpected_loop_return_is_a_failure(monkeypatch, resources):
    container, broker, _ = resources
    monkeypatch.setattr(outbox, "run_outbox", AsyncMock())

    with pytest.raises(RuntimeError, match="Outbox loop stopped unexpectedly"):
        await outbox.run_outbox_worker(Settings(api_key="unit-test-api-key", _env_file=None))

    broker.__aexit__.assert_awaited_once()
    container.close.assert_awaited_once()


async def test_startup_failure_closes_resources(resources):
    container, broker, topology = resources
    topology.declare.side_effect = RuntimeError("topology unavailable")

    with pytest.raises(RuntimeError, match="topology unavailable"):
        await outbox.run_outbox_worker(Settings(api_key="unit-test-api-key", _env_file=None))

    broker.__aexit__.assert_awaited_once()
    container.close.assert_awaited_once()


@pytest.fixture
async def cli_process():
    processes = []

    async def start(mode):
        # Exercise real process exit codes and OS signals without external services.
        script = textwrap.dedent("""\
            import asyncio
            import sys
            from presentation.cli import outbox

            async def worker():
                try:
                    print("ready", flush=True)
                    if sys.argv[1] == "error":
                        raise RuntimeError("loop failed")
                    if sys.argv[1] == "cancel":
                        raise asyncio.CancelledError()
                    await asyncio.Future()
                finally:
                    await asyncio.sleep(0)
                    print("closed", flush=True)

            outbox.run_outbox_worker = worker
            outbox.main()
        """)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            script,
            mode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        assert await asyncio.wait_for(process.stdout.readline(), timeout=10) == b"ready\n"
        return process

    yield start

    for process in processes:
        if process.returncode is None:
            process.kill()
        await process.communicate()


@pytest.mark.parametrize("mode, error", [("error", b"RuntimeError"), ("cancel", b"CancelledError")])
async def test_cli_exits_with_error_on_unexpected_failure(cli_process, mode, error):
    process = await cli_process(mode)
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)

    assert process.returncode != 0
    assert b"closed" in stdout
    assert error in stderr


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
async def test_cli_shutdown_signal_allows_cleanup(cli_process, signum):
    process = await cli_process("wait")
    process.send_signal(signum)
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)

    assert process.returncode == 0, stderr.decode()
    assert b"closed" in stdout
    assert not stderr
