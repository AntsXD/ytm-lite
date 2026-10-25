from __future__ import annotations

from pathlib import Path

import pytest

from ytm_worker import jsruntime


def _fake_candidates(found: list[tuple[str, Path]]) -> object:
    def candidates(preferred: str = "auto") -> list[tuple[str, Path]]:
        if preferred in ("deno", "node"):
            return [c for c in found if c[0] == preferred]
        return list(found)

    return candidates


def test_prefers_first_working_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    found = [("deno", Path("deno.exe")), ("node", Path("node.exe"))]
    monkeypatch.setattr(jsruntime, "candidates", _fake_candidates(found))
    versions = {"deno.exe": "deno 2.9.8 (stable)", "node.exe": "v24.19.0"}
    runtime = jsruntime.detect(probe=lambda p: versions[p.name])
    assert runtime == jsruntime.JsRuntime("deno", "deno.exe", "deno 2.9.8 (stable)")
    assert runtime.as_ytdlp_option() == {"deno": {"path": "deno.exe"}}


def test_skips_runtimes_that_are_too_old(monkeypatch: pytest.MonkeyPatch) -> None:
    found = [("deno", Path("deno.exe")), ("node", Path("node.exe"))]
    monkeypatch.setattr(jsruntime, "candidates", _fake_candidates(found))
    versions = {"deno.exe": "deno 2.2.0", "node.exe": "v22.1.0"}
    runtime = jsruntime.detect(probe=lambda p: versions[p.name])
    assert runtime is not None and runtime.name == "node"


def test_none_when_nothing_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jsruntime, "candidates", _fake_candidates([("node", Path("node.exe"))]))
    assert jsruntime.detect(probe=lambda p: None) is None
    assert jsruntime.detect(probe=lambda p: "v20.11.0") is None


def test_explicit_path(tmp_path: Path) -> None:
    exe = tmp_path / "node.exe"
    exe.write_bytes(b"")
    assert jsruntime.candidates(str(exe)) == [("node", exe)]
