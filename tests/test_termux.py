"""The Termux helper scripts (run with bash here; the phone uses the same files)."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent


def fake_kitefinder(tmp_path, output="Read 1 post: kite 12m² North · ₪3,200"):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    exe = bin_dir / "kitefinder"
    exe.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/args"\necho "{output}"\n')
    exe.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}


def test_share_to_termux_adds_the_link(tmp_path):
    env = fake_kitefinder(tmp_path)
    out = subprocess.run(
        ["bash", str(ROOT / "bin/termux-url-opener"), "https://www.yad2.co.il/item/a1b2"],
        env=env, capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert (tmp_path / "args").read_text().strip() == "add-url https://www.yad2.co.il/item/a1b2"
    assert out.stdout.strip() == "Read 1 post: kite 12m² North · ₪3,200"


def test_share_without_a_link_fails_clearly(tmp_path):
    out = subprocess.run(["bash", str(ROOT / "bin/termux-url-opener")], env=fake_kitefinder(tmp_path),
                         capture_output=True, text=True)  # fmt: skip
    assert out.returncode == 1 and "No link was shared." in out.stderr


def test_installer_refuses_to_run_outside_termux():
    env = {k: v for k, v in os.environ.items() if k != "PREFIX"}
    out = subprocess.run(
        ["bash", str(ROOT / "install_termux.sh")], env=env, capture_output=True, text=True
    )
    assert out.returncode == 1 and "for Termux on Android" in out.stderr


def test_scripts_are_executable_and_parse():
    for name in ("install_termux.sh", "bin/termux-url-opener"):
        path = ROOT / name
        assert os.access(path, os.X_OK), name
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_installer_never_prints_or_commits_secrets():
    text = (ROOT / "install_termux.sh").read_text()
    assert "cat .env" not in text and "chmod 600" in text and "chmod 700" in text
    ignored = (ROOT / ".gitignore").read_text()
    assert ".env" in ignored and "secrets/" in ignored


def test_installer_does_not_upgrade_pip():
    lines = [
        ln
        for ln in (ROOT / "install_termux.sh").read_text().splitlines()
        if not ln.lstrip().startswith("#")
    ]
    assert not any("--upgrade pip" in ln for ln in lines)  # Termux forbids it


def test_a_notification_that_never_answers_does_not_hang_the_share(tmp_path):
    env = fake_kitefinder(tmp_path)
    hang = tmp_path / "fakebin" / "termux-notification"
    hang.write_text("#!/bin/sh\nsleep 60\n")
    hang.chmod(0o755)
    text = (ROOT / "bin/termux-url-opener").read_text().replace("timeout 10 ", "timeout 1 ")
    script = tmp_path / "opener"
    script.write_text(text)
    out = subprocess.run(
        ["bash", str(script), "https://x.example/p"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert out.returncode == 0 and "Read 1 post" in out.stdout
