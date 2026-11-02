"""Build and inspect actual deterministic pure Python installer archives."""

from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile


def test_deterministic_architecture_labelled_archives(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "package_extension", root / "scripts/package_extension.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hashes = []
    for target in ["linux-amd64", "linux-arm64"]:
        a = module.package_extension(root, target, tmp_path / "one")
        b = module.package_extension(root, target, tmp_path / "two")
        assert a.read_bytes() == b.read_bytes()
        assert a.stat().st_size <= 50 * 1024 * 1024
        hashes.append(hashlib.sha256(a.read_bytes()).hexdigest())
        with zipfile.ZipFile(a) as archive:
            names = archive.namelist()
            assert all(n.startswith("flowsteward_elasticsearch/") for n in names)
            assert json.loads(archive.read("flowsteward_elasticsearch/.fs-package.yaml")) == {
                "runtime_target": target
            }
            assert sum(i.file_size for i in archive.infolist()) <= 150 * 1024 * 1024
            assert "flowsteward_elasticsearch/artifacts/workflows/elasticsearch_ask.yaml" in names
            assert not any(
                "/.git/" in n or "/dev-wheels/" in n or "/dist/" in n or "/__pycache__/" in n
                for n in names
            )
            assert not any(archive.read(n).startswith(b"\x7fELF") for n in names)
    assert hashes[0] != hashes[1]
