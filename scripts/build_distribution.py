from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZIP_STORED, ZipFile


REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_ROOT = REPO_ROOT / "dist"
PACKAGE_NAME = "AtlasNode-Windows-Package"
PACKAGE_ROOT = DIST_ROOT / PACKAGE_NAME
MODEL_SOURCE = Path(
    os.getenv(
        "ATLASNODE_DISTRIBUTION_MODEL_SOURCE",
        r"C:\Users\jdmcc\source\repos\AiModels\BAAI--bge-m3",
    )
)
MODEL_TARGET = PACKAGE_ROOT / "models" / "BAAI--bge-m3"
MODEL_ARCHIVE_NAME = "BAAI--bge-m3-package.zip"
MODEL_DELIVERY_MODE = os.getenv("ATLASNODE_DISTRIBUTION_MODEL_DELIVERY", "sidecar").strip().lower()
MODEL_SPLIT_SIZE_MB = max(0, int(os.getenv("ATLASNODE_DISTRIBUTION_MODEL_SPLIT_SIZE_MB", "1900")))
MODEL_SPLIT_SIZE_BYTES = MODEL_SPLIT_SIZE_MB * 1024 * 1024
WHEELHOUSE_TARGET = PACKAGE_ROOT / "wheelhouse"

COPY_ITEMS = [
    "atlasnode_mcp",
    "assets",
    "LICENSE",
    "README.md",
    "MCP.md",
    "pyproject.toml",
]

TEMPLATE_ITEMS = [
    ("packaging/install-atlasnode.bat", "install-atlasnode.bat"),
    ("packaging/run-atlasnode-dashboard.bat", "run-atlasnode-dashboard.bat"),
    ("packaging/run-atlasnode-server.bat", "run-atlasnode-server.bat"),
    ("packaging/START_HERE.md", "START_HERE.md"),
    ("packaging/agent-template/README.md", "agent-template/README.md"),
    ("packaging/agent-template/AGENTS.md", "agent-template/AGENTS.md"),
    ("packaging/agent-template/.vscode/mcp-http.json", "agent-template/.vscode/mcp-http.json"),
    ("packaging/agent-template/.vscode/mcp-stdio-template.json", "agent-template/.vscode/mcp-stdio-template.json"),
    ("packaging/agent-template/.vscode/settings.json", "agent-template/.vscode/settings.json"),
]


def remove_existing(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def copy_item(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".git"),
        )
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_model(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Bundled model source not found: {source}")
    validate_model_source(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(".cache", "imgs", "README.md", "long.jpg", ".gitattributes"),
    )


def _iter_model_files(source: Path) -> list[Path]:
    ignored_names = {"README.md", "long.jpg", ".gitattributes"}
    ignored_dirs = {".cache", "imgs"}
    return [
        path
        for path in source.rglob("*")
        if path.is_file()
        and not any(part in ignored_dirs for part in path.relative_to(source).parts)
        and path.name not in ignored_names
    ]


def validate_model_source(source: Path) -> None:
    required_files = ("config.json", "modules.json", "sentence_bert_config.json")
    missing = [name for name in required_files if not (source / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Bundled model source is missing required files: {', '.join(missing)}"
        )

    if not ((source / "tokenizer.json").exists() or (source / "sentencepiece.bpe.model").exists()):
        raise FileNotFoundError(
            "Bundled model source must include tokenizer assets."
        )

    for path in source.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"Bundled model source must not contain symlinks: {path}")
        if path.is_file() and path.suffix.lower() in {".py", ".pyc", ".pyo", ".so", ".dll"}:
            raise RuntimeError(f"Bundled model source contains executable code artifacts: {path}")


def create_model_archive(source: Path, archive_path: Path) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"Bundled model source not found: {source}")
    validate_model_source(source)
    remove_existing(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive_path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        for path in _iter_model_files(source):
            archive.write(path, Path("BAAI--bge-m3") / path.relative_to(source))
    return archive_path


def split_file(path: Path, chunk_size: int) -> list[Path]:
    if chunk_size <= 0 or path.stat().st_size <= chunk_size:
        return [path]

    parts: list[Path] = []
    with path.open("rb") as source_handle:
        index = 1
        while True:
            chunk = source_handle.read(chunk_size)
            if not chunk:
                break
            part_path = path.with_suffix(path.suffix + f".{index:03d}")
            part_path.write_bytes(chunk)
            parts.append(part_path)
            index += 1
    path.unlink()
    return parts


def write_env_files() -> None:
    env_text = "\n".join(
        [
            "ATLASNODE_EMBEDDING_BACKEND=bge-m3",
            "ATLASNODE_EMBEDDING_MODEL_PATH=models\\BAAI--bge-m3",
            "",
        ]
    )
    (PACKAGE_ROOT / ".env").write_text(env_text, encoding="utf-8")
    (PACKAGE_ROOT / ".env.example").write_text(env_text, encoding="utf-8")


def write_manifest() -> None:
    manifest = {
        "package_name": PACKAGE_NAME,
        "repo_source": str(REPO_ROOT),
        "model_source": str(MODEL_SOURCE),
        "python_version": sys.version.split()[0],
        "contents": {
            "server_module": "atlasnode_mcp.server",
            "dashboard_module": "atlasnode_mcp.dashboard",
            "bundled_model": str(Path("models") / "BAAI--bge-m3"),
            "wheelhouse": "wheelhouse",
            "dashboard_launcher": "run-atlasnode-dashboard.bat",
            "server_launcher": "run-atlasnode-server.bat",
            "install_launcher": "install-atlasnode.bat",
            "shortcut": "AtlasNode.lnk",
            "agent_template": "agent-template",
        },
        "model_delivery": MODEL_DELIVERY_MODE,
    }
    if MODEL_DELIVERY_MODE == "sidecar":
        manifest["contents"]["model_archive"] = MODEL_ARCHIVE_NAME
        manifest["contents"]["model_split_size_mb"] = MODEL_SPLIT_SIZE_MB
    (PACKAGE_ROOT / "package-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def build_wheelhouse() -> None:
    remove_existing(WHEELHOUSE_TARGET)
    WHEELHOUSE_TARGET.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(REPO_ROOT),
            "--wheel-dir",
            str(WHEELHOUSE_TARGET),
        ],
        check=True,
        cwd=REPO_ROOT,
    )


def create_shortcut() -> None:
    shortcut_path = PACKAGE_ROOT / "AtlasNode.lnk"
    target_path = PACKAGE_ROOT / "run-atlasnode-dashboard.bat"
    icon_path = PACKAGE_ROOT / "assets" / "atlasnode-brain-sunburst-icon.ico"
    command = f"""
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut('{shortcut_path}')
$shortcut.TargetPath = '{target_path}'
$shortcut.WorkingDirectory = '{PACKAGE_ROOT}'
$shortcut.IconLocation = '{icon_path}'
$shortcut.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=True,
        cwd=REPO_ROOT,
    )


def create_zip_archive() -> Path:
    zip_path = DIST_ROOT / f"{PACKAGE_NAME}.zip"
    remove_existing(zip_path)
    with ZipFile(zip_path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        for path in PACKAGE_ROOT.rglob("*"):
            archive.write(path, path.relative_to(PACKAGE_ROOT.parent))
    return zip_path


def main() -> None:
    if MODEL_DELIVERY_MODE not in {"embedded", "sidecar"}:
        raise ValueError("ATLASNODE_DISTRIBUTION_MODEL_DELIVERY must be 'embedded' or 'sidecar'.")

    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    remove_existing(PACKAGE_ROOT)
    PACKAGE_ROOT.mkdir(parents=True, exist_ok=True)

    for item in COPY_ITEMS:
        copy_item(REPO_ROOT / item, PACKAGE_ROOT / item)

    for source, target in TEMPLATE_ITEMS:
        copy_item(REPO_ROOT / source, PACKAGE_ROOT / target)

    model_outputs: list[Path] = []
    if MODEL_DELIVERY_MODE == "embedded":
        copy_model(MODEL_SOURCE, MODEL_TARGET)
    else:
        model_outputs = split_file(
            create_model_archive(MODEL_SOURCE, DIST_ROOT / MODEL_ARCHIVE_NAME),
            MODEL_SPLIT_SIZE_BYTES,
        )

    build_wheelhouse()
    write_env_files()
    write_manifest()
    create_shortcut()
    zip_path = create_zip_archive()

    print(f"Built package folder: {PACKAGE_ROOT}")
    print(f"Built package archive: {zip_path}")
    if model_outputs:
        print("Built model sidecar archive parts:")
        for output in model_outputs:
            print(f"  - {output}")


if __name__ == "__main__":
    main()


