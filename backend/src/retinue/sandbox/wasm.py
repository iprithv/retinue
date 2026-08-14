"""WASM sandbox floor (§12, §27.5): wasmtime executing a WASI CPython build.

The interpreter binary (`python.wasm`) is not bundled in the wheel (size);
`retinue doctor` explains how to fetch it into `~/.retinue/sandbox/`. When
either wasmtime or the interpreter is absent this backend reports itself
unavailable and the sandbox tool answers with a clean, honest error —
never a crash, never a silent no-op.
"""

import asyncio
from pathlib import Path

import structlog

from retinue.sandbox.base import ExecLimits, ExecResult

log = structlog.get_logger("retinue.sandbox.wasm")


class WasmSandbox:
    name = "wasm"

    def __init__(self, sandbox_dir: Path) -> None:
        self._dir = sandbox_dir
        self._interpreter = sandbox_dir / "python.wasm"
        self._engine = None

    def available(self) -> bool:
        if not self._interpreter.is_file():
            return False
        try:
            import wasmtime  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            return False
        return True

    def describe(self) -> str:
        if self.available():
            return (
                "WASM sandbox (wasmtime + WASI CPython): pure-Python code, "
                "no network, 5s CPU / 256MB caps"
            )
        return (
            "sandbox unavailable — install the wasmtime extra and place a WASI "
            "CPython build at ~/.retinue/sandbox/python.wasm (see `retinue doctor`)"
        )

    async def run(self, code: str, lang: str, limits: ExecLimits) -> ExecResult:
        if lang not in ("python", "py"):
            return ExecResult(status="error", stderr=f"unsupported language {lang!r}")
        if not self.available():
            return ExecResult(status="unavailable", stderr=self.describe())
        return await asyncio.to_thread(self._run_sync, code, limits)

    def _run_sync(self, code: str, limits: ExecLimits) -> ExecResult:
        import tempfile

        from wasmtime import (
            Config,
            Engine,
            Linker,
            Module,
            Store,
            WasiConfig,
        )

        config = Config()
        config.consume_fuel = True
        engine = Engine(config)
        linker = Linker(engine)
        linker.define_wasi()
        module = Module.from_file(engine, str(self._interpreter))
        store = Store(engine)
        store.set_fuel(4_000_000_000)  # ≈ seconds of CPU, deterministic

        with tempfile.TemporaryDirectory(prefix="retinue-sbx-") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "main.py").write_text(code, encoding="utf-8")
            out_file = tmp_path / "stdout.txt"
            err_file = tmp_path / "stderr.txt"
            wasi = WasiConfig()
            wasi.argv = ("python", "/box/main.py")
            wasi.preopen_dir(str(tmp_path), "/box")
            wasi.stdout_file = str(out_file)
            wasi.stderr_file = str(err_file)
            store.set_wasi(wasi)
            instance = linker.instantiate(store, module)
            start = instance.exports(store)["_start"]
            status = "ok"
            try:
                start(store)
            except Exception as exc:  # WASI exit / fuel exhaustion
                message = str(exc)
                if "fuel" in message.lower():
                    status = "timeout"
                elif "exit status 0" not in message:
                    status = "error"
            stdout = out_file.read_text(errors="replace")[: limits.output_cap]
            stderr = err_file.read_text(errors="replace")[: limits.output_cap]
            return ExecResult(status=status, stdout=stdout, stderr=stderr)
