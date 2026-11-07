#!/usr/bin/env bash
# One-time setup on the Brev instance: Python env + raw data. Idempotent (safe to re-run).
set -euo pipefail
mkdir -p ~/biohack/{data/raw,data/processed,cache,logs,graph}
if ! command -v uv >/dev/null && [ ! -x ~/.local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
[ -x ~/venv/bin/python ] || uv venv ~/venv --python 3.11
uv pip install --python ~/venv/bin/python -q torch --index-url https://download.pytorch.org/whl/cu126
uv pip install --python ~/venv/bin/python -q torch_geometric pandas pyarrow requests scipy scikit-learn sentence-transformers matplotlib pytest
# pcst_fast: the PyPI wheel silently returns wrong trees with NumPy 2.x -> build from source
uv pip install --python ~/venv/bin/python -q --reinstall --no-binary pcst_fast "pcst_fast==1.0.10"
cd ~/biohack/data/raw
[ -s kg.csv ] || wget -q -O kg.csv https://dataverse.harvard.edu/api/access/datafile/6180620
for f in dismech_nodes.tsv dismech_edges.tsv; do
  [ -s "$f" ] || wget -q "https://github.com/monarch-initiative/dismech/releases/latest/download/$f"
done
curl -s https://api.github.com/repos/monarch-initiative/dismech/releases/latest | grep -m1 '"tag_name"' > dismech_release.txt || true
ls -la
cat dismech_release.txt
~/venv/bin/python -c "import torch, torch_geometric as pyg; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0), '| pyg', pyg.__version__)"
