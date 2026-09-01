"""Private child-process worker for controlled scientific Python execution."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import random
import socket
import sys
from pathlib import Path
from typing import Any


MAX_AST_NODES = 4_000
BANNED_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef)
BANNED_NAMES = {
    "eval", "exec", "compile", "open", "input", "__import__", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "dir", "help", "breakpoint", "exit", "quit", "memoryview",
    "object", "type", "super",
}
BANNED_ATTRIBUTES = {
    "system", "popen", "spawn", "fork", "subprocess", "socket", "connect", "request", "urlopen",
    "read_csv", "read_json", "read_excel", "read_pickle", "read_parquet", "read_sql", "read_html",
    "to_csv", "to_json", "to_excel", "to_pickle", "to_parquet", "to_sql", "savefig", "imsave",
    "load", "save", "savez", "memmap", "open_memmap", "ctypes", "ctypeslib", "f2py", "testing", "io", "path",
    "environ", "modules", "executable", "__dict__", "__class__", "__bases__", "__subclasses__",
    "fromfile", "genfromtxt", "loadtxt", "fromregex", "tofile", "savetxt", "DataSource",
    "ExcelFile", "HDFStore", "eval", "query",
}
SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict, "enumerate": enumerate,
    "filter": filter, "float": float, "int": int, "len": len, "list": list, "map": map,
    "max": max, "min": min, "range": range, "round": round, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "zip": zip, "print": print,
    "Exception": Exception, "ValueError": ValueError, "RuntimeError": RuntimeError,
}


class BoundedWriter(io.StringIO):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def write(self, text: str) -> int:
        remaining = max(0, self.limit - len(self.getvalue()))
        if remaining:
            super().write(text[:remaining])
        return len(text)


def reject_network(*_: Any, **__: Any) -> None:
    raise PermissionError("network access is disabled in the controlled Python runner")


def validate_code(code: str) -> ast.Module:
    tree = ast.parse(code, mode="exec")
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise ValueError(f"code exceeds the AST node limit ({MAX_AST_NODES})")
    for node in nodes:
        if isinstance(node, BANNED_NODES):
            raise ValueError(f"{type(node).__name__} is not permitted")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in BANNED_NAMES):
            raise ValueError(f"name '{node.id}' is not permitted")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in BANNED_ATTRIBUTES):
            raise ValueError(f"attribute '{node.attr}' is not permitted")
        if isinstance(node, ast.Attribute) and node.attr.startswith("read_"):
            raise ValueError(f"attribute '{node.attr}' is not permitted")
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("to_")
            and node.attr not in {"to_dict", "to_numpy", "to_list", "to_period", "to_timestamp"}
        ):
            raise ValueError(f"attribute '{node.attr}' is not permitted")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip().replace("\\", "/")
            if "://" in value or value.startswith("/") or "../" in value or value == "..":
                raise ValueError("URL, absolute, UNC, or parent-relative path literals are not permitted")
            if len(value) >= 3 and value[1:3] == ":/":
                raise ValueError("absolute path literals are not permitted")
    return tree


def json_safe(value: Any) -> Any:
    import numpy as np
    import pandas as pd

    if isinstance(value, pd.DataFrame):
        return {"kind": "dataframe", "rows": len(value), "columns": list(map(str, value.columns))}
    if isinstance(value, pd.Series):
        return value.head(1_000).where(value.notna(), None).tolist()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in list(value.items())[:1_000]}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in list(value)[:10_000]]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def main() -> int:
    request_path = Path(sys.argv[1]).resolve()
    result_path = Path(sys.argv[2]).resolve()
    workspace = request_path.parent.resolve()
    if result_path.parent != workspace:
        return 2
    os.chdir(workspace)
    # The parent already supplies a strict environment allowlist. Keep
    # SYSTEMROOT/WINDIR because Windows scientific wheels and Winsock need them.
    os.environ.update({
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
        "HOME": str(workspace), "USERPROFILE": str(workspace), "TEMP": str(workspace), "TMP": str(workspace),
    })

    payload = json.loads(request_path.read_text(encoding="utf-8"))
    code = str(payload.get("code") or "")
    output = BoundedWriter(int(payload.get("max_output_chars") or 16_000))
    response: dict[str, Any]
    stage = "validation"
    try:
        tree = validate_code(code)
        stage = "scientific_imports"
        stage = "import_numpy"
        import numpy as np
        stage = "import_pandas"
        import pandas as pd

        stage = "dataset_loading"
        dataset = (workspace / str(payload["dataset_filename"])).resolve()
        if dataset.parent != workspace or not dataset.is_file():
            raise ValueError("dataset is not inside the sandbox workspace")
        loaders = {
            ".csv": pd.read_csv,
            ".tsv": lambda path: pd.read_csv(path, sep="\t"),
            ".json": pd.read_json,
            ".xlsx": pd.read_excel,
            ".xls": pd.read_excel,
        }
        if dataset.suffix.lower() not in loaders:
            raise ValueError("unsupported dataset format")
        data = loaders[dataset.suffix.lower()](dataset)
        seed = int(payload.get("seed") or 0)
        random.seed(seed)
        np.random.seed(seed)
        socket.create_connection = reject_network  # type: ignore[assignment]
        stage = "user_code"
        namespace = {
            "__builtins__": SAFE_BUILTINS,
            "data": data.copy(deep=True), "np": np, "pd": pd,
        }
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            exec(compile(tree, "<controlled-analysis>", "exec"), namespace, namespace)
        if "result" not in namespace:
            raise ValueError("analysis code must assign its final value to 'result'")
        stage = "artifact_export"
        artifacts: list[str] = []
        result = namespace["result"]
        if isinstance(result, pd.DataFrame):
            result.head(100_000).to_csv(workspace / "analysis_result.csv", index=False)
            artifacts.append("analysis_result.csv")
        else:
            (workspace / "analysis_result.json").write_text(
                json.dumps(json_safe(result), ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            artifacts.append("analysis_result.json")
        response = {
            "status": "success", "result": json_safe(result), "stdout": output.getvalue(),
            "stderr": "", "artifacts": artifacts,
        }
    except (SyntaxError, ValueError, PermissionError) as exc:
        response = {
            "status": "rejected", "error_type": type(exc).__name__, "error": f"{stage}: {exc}",
            "stdout": output.getvalue(), "stderr": "", "artifacts": [],
        }
    except Exception as exc:
        response = {
            "status": "failed", "error_type": type(exc).__name__, "error": f"{stage}: {exc}",
            "stdout": output.getvalue(), "stderr": "", "artifacts": [],
        }
    result_path.write_text(json.dumps(response, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0 if response["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
