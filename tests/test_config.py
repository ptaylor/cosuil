"""Config path resolution tests."""

from __future__ import annotations

import pathlib

import cosuil.config as cfg_mod


def test_config_dir_xdg_default(tmp_path, monkeypatch):
    monkeypatch.delenv("COSUIL_CONFIG_DIR", raising=False)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        cfg_mod, "user_config_dir", lambda app: str(tmp_path / "native-config")
    )
    assert cfg_mod.config_dir() == tmp_path / ".config" / "cosuil"


def test_config_dir_falls_back_to_native(tmp_path, monkeypatch):
    monkeypatch.delenv("COSUIL_CONFIG_DIR", raising=False)
    monkeypatch.setattr(
        pathlib.Path, "home", classmethod(lambda cls: tmp_path / "home")
    )
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(cfg_mod, "user_config_dir", lambda app: str(native))
    assert cfg_mod.config_dir() == native


def test_config_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("COSUIL_CONFIG_DIR", str(tmp_path / "custom"))
    assert cfg_mod.config_dir() == tmp_path / "custom"
