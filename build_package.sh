#!/usr/bin/env bash
set -euo pipefail

# Move to the script's directory (repo root)
cd "$(dirname "$0")"

echo "=========================================================="
echo " Building Pardus ETAP eta-qr-login (v0.2.6.1) Package"
echo "=========================================================="

# 1. Clean all __pycache__ and bytecode artifacts
echo "🧹 Pruning __pycache__ and *.pyc bytecode..."
find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true

# 2. Re-pack debian archive with reproducible timestamps and validation
export PYTHONDONTWRITEBYTECODE=1
python3 repack_deb.py "$@"

# 3. Post-build verification
echo "🔍 Final verification of package cleanliness:"
python3 -c "
import subprocess, tarfile, io

p = subprocess.run(['ar', '-p', 'eta-qr-login_0.2.6.1_all.deb', 'data.tar.xz'], stdout=subprocess.PIPE, check=True)
with tarfile.open(fileobj=io.BytesIO(p.stdout), mode='r:xz') as tar:
    names = tar.getnames()
    pyc_items = [n for n in names if '__pycache__' in n or n.endswith('.pyc')]
    if pyc_items:
        print('❌ ERROR: Found bytecode in data.tar.xz:', pyc_items)
        exit(1)
    print('✅ Cleanliness PASS: 0 bytecode files found in data.tar.xz!')
"

echo "🎉 Build completed successfully!"
