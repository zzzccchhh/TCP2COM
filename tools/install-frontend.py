"""Install the pinned TCP2COM desktop/LAN build under frontend/dist."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile


VERSION = "v1.0.0"
ASSET = "tcp2com-frontend.zip"
URL = f"https://github.com/zzzccchhh/TCP2COM-FRONT/releases/download/{VERSION}/{ASSET}"
SHA256 = "321a215e1259dad9a60bacff4a7bb044b71ca2daf0b4e6b3cfaec6c720953eba"
BASE = Path(__file__).resolve().parents[1]


def install(archive=None):
    directory = BASE / "frontend"
    directory.mkdir(exist_ok=True)
    dest = directory / "dist"
    backup = directory / "dist.previous"
    if backup.exists():
        raise ValueError(f"请先检查并移走前端备份：{backup}")

    # Stage on the same volume so moving the validated build needs no file copies.
    with tempfile.TemporaryDirectory(prefix="dist-install-", dir=directory) as folder:
        staging = Path(folder)
        if archive is None:
            archive = staging / ASSET
            with urlopen(URL, timeout=30) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)

        with archive.open("rb") as source:
            if hashlib.sha256(source.read()).hexdigest() != SHA256:
                raise ValueError(f"{VERSION} 前端安装包的 SHA-256 校验失败")
            source.seek(0)
            with ZipFile(source) as bundle:
                bundle.extractall(staging / "dist")

        staged_build = staging / "dist"
        if not (staged_build / "index.html").is_file():
            raise ValueError("前端安装包缺少 index.html")

        has_previous = dest.exists()
        if has_previous:
            dest.rename(backup)
        try:
            staged_build.rename(dest)
        except BaseException:
            if has_previous:
                backup.rename(dest)
            raise
        if has_previous:
            shutil.rmtree(backup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help=f"Use a local copy of {ASSET} for offline installation")
    args = parser.parse_args()
    try:
        install(args.archive)
    except (OSError, ValueError, BadZipFile) as error:
        parser.exit(1, f"前端安装失败：{error}\n")
    print(f"已安装 TCP2COM 前端 {VERSION}：{BASE / 'frontend' / 'dist'}")


if __name__ == "__main__":
    main()
