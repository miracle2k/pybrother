#!/usr/bin/env python3

import sys

from pybrother import cli


async def _fake_send_via_ipp(binary, copies, printer, max_retries=5, initial_delay=2):
    _ = (binary, copies, printer, max_retries, initial_delay)
    return True


def _run_main(monkeypatch, args):
    monkeypatch.setattr(cli, "send_via_ipp", _fake_send_via_ipp)
    monkeypatch.setattr(sys, "argv", ["pybrother", *args])
    cli.main()


def test_default_print_writes_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    _run_main(
        monkeypatch,
        ["Hello", "--printer", "127.0.0.1", "--tape", "W6"],
    )

    assert list(tmp_path.iterdir()) == []


def test_output_writes_png_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output_path = tmp_path / "preview" / "label.png"

    _run_main(
        monkeypatch,
        [
            "Hello",
            "--printer",
            "127.0.0.1",
            "--tape",
            "W6",
            "--output",
            str(output_path),
        ],
    )

    assert output_path.exists()
    assert output_path.is_file()
    assert list(tmp_path.rglob("*.bin")) == []


def test_artifacts_writes_png_and_bin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    artifacts_dir = tmp_path / "artifacts"

    _run_main(
        monkeypatch,
        [
            "Hello Artifacts",
            "--printer",
            "127.0.0.1",
            "--tape",
            "W6",
            "--artifacts",
            str(artifacts_dir),
        ],
    )

    assert (artifacts_dir / "W6_Hello_Artifacts.png").exists()
    assert (artifacts_dir / "W6_Hello_Artifacts.bin").exists()


def test_artifacts_without_dir_uses_temp_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path))

    _run_main(
        monkeypatch,
        ["Hello", "--printer", "127.0.0.1", "--tape", "W6", "--artifacts"],
    )

    base_dir = tmp_path / "pybrother"
    run_dirs = [p for p in base_dir.iterdir() if p.is_dir()]

    assert len(run_dirs) == 1
    assert (run_dirs[0] / "W6_Hello.png").exists()
    assert (run_dirs[0] / "W6_Hello.bin").exists()
