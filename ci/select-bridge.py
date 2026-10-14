"""Check current compatible bridges; retain the certified rollback for later apps."""
import ast
from pathlib import Path
import os

certified = '4e8514a18a5eb54b81a76d5e53f682c759d239c7'
selected = certified
for node in ast.parse(Path('app/storage/adapters.py').read_text()).body:
    if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'POSTGRES_COMPATIBLE_VERSIONS' for target in node.targets):
        supported = ast.literal_eval(node.value)
        if [1, 2, 3] in supported and [1, 2, 3, 4] in supported:
            selected = os.environ['GITHUB_SHA']
print(f'ref={selected}')
