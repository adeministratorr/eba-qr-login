#!/usr/bin/env python3
"""
Repack eta-qr-login Debian package with strict cleanliness and reproducible build support.
"""
import os
import sys
import time
import tarfile
import io
import shutil
import zipfile
import subprocess
import argparse
from pathlib import Path


def clean_pycache(repo_root: Path):
    """Executes explicit cleanup of __pycache__ directories and .pyc files."""
    print("🧹 Running pycache and bytecode cleanup...")
    # 1. System find command as requested by user
    try:
        subprocess.run(
            ["find", str(repo_root), "-name", "__pycache__", "-type", "d", "-prune", "-exec", "rm", "-rf", "{}", "+"],
            check=True
        )
        subprocess.run(
            ["find", str(repo_root), "-name", "*.pyc", "-delete"],
            check=True
        )
    except Exception as e:
        print(f"Warning during find cleanup: {e}")

    # 2. Python recursive fallback safety cleanup
    for root, dirs, files in os.walk(repo_root, topdown=False):
        for d in dirs:
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        for f in files:
            if f.endswith(".pyc") or f == ".DS_Store":
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass


def get_source_date_epoch(repo_root: Path) -> int:
    """Returns reproducible build timestamp from SOURCE_DATE_EPOCH or git commit."""
    if "SOURCE_DATE_EPOCH" in os.environ:
        try:
            return int(os.environ["SOURCE_DATE_EPOCH"])
        except ValueError:
            pass

    # Try git log commit timestamp
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--pretty=%ct"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        ts = res.stdout.strip()
        if ts.isdigit():
            return int(ts)
    except Exception:
        pass

    # Fallback to changelog.gz mtime
    changelog = repo_root / "pkg_unpacked" / "data" / "usr" / "share" / "doc" / "eta-qr-login" / "changelog.gz"
    if changelog.exists():
        return int(changelog.stat().st_mtime)

    return 1790143593  # Fixed epoch fallback (2026-09-23)


def create_tar_xz(source_dir: Path, epoch: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        # Add root entry ./
        root_ti = tarfile.TarInfo("./")
        root_ti.type = tarfile.DIRTYPE
        root_ti.mode = 0o755
        root_ti.uid = 0
        root_ti.gid = 0
        root_ti.uname = "root"
        root_ti.gname = "root"
        root_ti.mtime = epoch
        tar.addfile(root_ti)

        for root, dirs, files in os.walk(source_dir):
            # Exclude pycache and hidden directories
            dirs[:] = [d for d in sorted(dirs) if d != "__pycache__" and not d.startswith(".")]
            files = [f for f in sorted(files) if not f.endswith(".pyc") and not f.startswith(".DS_Store")]

            for d in dirs:
                full_path = Path(root) / d
                rel_path = "./" + str(full_path.relative_to(source_dir)) + "/"
                ti = tar.gettarinfo(str(full_path), arcname=rel_path)
                ti.uid = 0
                ti.gid = 0
                ti.uname = "root"
                ti.gname = "root"
                ti.mode = 0o755
                ti.mtime = epoch
                tar.addfile(ti)

            for f in files:
                full_path = Path(root) / f
                rel_path = "./" + str(full_path.relative_to(source_dir))
                ti = tar.gettarinfo(str(full_path), arcname=rel_path)
                ti.uid = 0
                ti.gid = 0
                ti.uname = "root"
                ti.gname = "root"
                ti.mtime = epoch
                if os.access(full_path, os.X_OK) or f in ("postinst", "postrm", "prerm"):
                    ti.mode = 0o755
                else:
                    ti.mode = 0o644
                with open(full_path, "rb") as fp:
                    tar.addfile(ti, fp)
    return buf.getvalue()


def build_ar_archive(out_path: Path, members: list, epoch: int):
    with open(out_path, "wb") as f:
        f.write(b"!<arch>\n")
        for name, data in members:
            # ar header is 60 bytes: name(16), mtime(12), uid(6), gid(6), mode(8), size(10), trailer(2)
            header = f"{name:<16}{epoch:<12}0     0     100644  {len(data):<10}`\n".encode("ascii")
            f.write(header)
            f.write(data)
            if len(data) % 2 == 1:
                f.write(b"\n")  # ar padding byte


def verify_package_cleanliness(data_tar_bytes: bytes, md5sums_path: Path):
    """Verifies that data.tar.xz contains NO __pycache__ or .pyc, and matches md5sums."""
    with tarfile.open(fileobj=io.BytesIO(data_tar_bytes), mode="r:xz") as tar:
        members = tar.getmembers()
        names = [m.name for m in members]

        # Check for pycache
        pyc_found = [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
        if pyc_found:
            raise ValueError(f"Package contains bytecode / pycache: {pyc_found}")

        # Check against md5sums
        expected_files = set()
        with open(md5sums_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    _, path = line.split(maxsplit=1)
                    expected_files.add("./" + path)

        file_members = {m.name for m in members if m.isreg()}
        diff = file_members - expected_files
        if diff:
            raise ValueError(f"Unexpected files in data.tar.xz not in md5sums: {diff}")
        missing = expected_files - file_members
        if missing:
            raise ValueError(f"Missing expected files in data.tar.xz: {missing}")

        print(f"✅ Package cleanliness verified: exactly {len(file_members)} files matching md5sums, 0 bytecode files.")


def update_distribution_bundles(repo_root: Path, epoch: int):
    """Updates .tar.gz and .zip distribution bundles with reproducible timestamps."""
    bundle_dir = repo_root / "eta-qr-login-eba-sso-v0.2.6.1"
    if not bundle_dir.exists():
        return

    def tar_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if "__pycache__" in ti.name or ti.name.endswith(".pyc") or ".DS_Store" in ti.name:
            return None
        ti.mtime = epoch
        ti.uid = 0
        ti.gid = 0
        ti.uname = "root"
        ti.gname = "root"
        return ti

    tar_gz_out = repo_root / "eta-qr-login-eba-sso-v0.2.6.1.tar.gz"
    print(f"📦 Updating reproducible bundle archive {tar_gz_out.name}...")
    import gzip
    with open(tar_gz_out, "wb") as f_out:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f_out, mtime=epoch) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                tar.add(bundle_dir, arcname="eta-qr-login-eba-sso-v0.2.6.1", filter=tar_filter)

    zip_out = repo_root / "eta-qr-login-eba-sso-v0.2.6.1.zip"
    print(f"📦 Updating reproducible bundle archive {zip_out.name}...")
    zip_time = time.gmtime(epoch)[:6]
    with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(bundle_dir):
            dirs[:] = [d for d in sorted(dirs) if d != "__pycache__" and not d.startswith(".")]
            for file in sorted(files):
                if file.endswith(".pyc") or file.startswith(".DS_Store"):
                    continue
                file_path = Path(root) / file
                arc_name = Path("eta-qr-login-eba-sso-v0.2.6.1") / file_path.relative_to(bundle_dir)
                zinfo = zipfile.ZipInfo(str(arc_name), date_time=zip_time)
                zinfo.external_attr = 0o644 << 16
                with open(file_path, "rb") as fp:
                    zf.writestr(zinfo, fp.read())


def main():
    parser = argparse.ArgumentParser(description="Deterministic builder for eta-qr-login .deb package")
    parser.add_argument("--no-archives", action="store_true", help="Skip creating .tar.gz and .zip release bundles")
    parser.add_argument("--epoch", type=int, default=None, help="Explicit SOURCE_DATE_EPOCH timestamp")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    pkg_unpacked = repo_root / "pkg_unpacked"
    control_dir = pkg_unpacked / "control"
    data_dir = pkg_unpacked / "data"

    epoch = args.epoch if args.epoch is not None else get_source_date_epoch(repo_root)
    print(f"⏱️  Using SOURCE_DATE_EPOCH: {epoch} ({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(epoch))})")

    # Step 1: Clean pycache before building
    clean_pycache(repo_root)

    # Step 2: Build control.tar.xz
    print("🔨 Building control.tar.xz (reproducible)...")
    control_bytes = create_tar_xz(control_dir, epoch)
    with open(pkg_unpacked / "control.tar.xz", "wb") as f:
        f.write(control_bytes)

    # Step 3: Build data.tar.xz
    print("🔨 Building data.tar.xz (reproducible)...")
    data_bytes = create_tar_xz(data_dir, epoch)
    with open(pkg_unpacked / "data.tar.xz", "wb") as f:
        f.write(data_bytes)

    # Step 4: Verify package cleanliness
    verify_package_cleanliness(data_bytes, control_dir / "md5sums")

    # Step 5: Build debian-binary
    deb_binary = b"2.0\n"
    with open(pkg_unpacked / "debian-binary", "wb") as f:
        f.write(deb_binary)

    # Step 6: Create .deb package in root
    out_deb = repo_root / "eta-qr-login_0.2.6.1_all.deb"
    print(f"📦 Building {out_deb.name}...")
    build_ar_archive(out_deb, [
        ("debian-binary", deb_binary),
        ("control.tar.xz", control_bytes),
        ("data.tar.xz", data_bytes)
    ], epoch)
    root_size = out_deb.stat().st_size
    print(f"✅ Created: {out_deb.name} ({root_size} bytes, ~{root_size/1024:.1f} KB)")

    # Step 7: Sync to standalone release bundle directory
    bundle_deb = repo_root / "eta-qr-login-eba-sso-v0.2.6.1" / "eta-qr-login_0.2.6.1_all.deb"
    print(f"📋 Syncing deb to bundle: {bundle_deb.name}...")
    shutil.copy2(out_deb, bundle_deb)

    # Step 8: Update .tar.gz and .zip release bundles if not disabled
    if not args.no_archives:
        update_distribution_bundles(repo_root, epoch)
    else:
        print("ℹ️ Skipping distribution bundle archives (--no-archives requested).")

    print("🚀 SUCCESS: Repackaging finished with reproducible timestamps!")


if __name__ == "__main__":
    main()
