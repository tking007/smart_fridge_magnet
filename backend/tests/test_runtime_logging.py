"""Service code must not bypass the request-id logging pipeline with print()."""
import ast
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
MODULES = ("app.py", "database.py", "ai_client.py", "scheduler.py")


def test_runtime_paths_do_not_print():
    for name in MODULES:
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if (isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and ast.unparse(node.test) == "__name__ == '__main__'"):
                node.body = []
        prints = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        assert not prints, f"{name} runtime print() calls at lines {prints}"
