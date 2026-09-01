"""Controlled deterministic execution for approved research operations.

This module intentionally does not expose Python, shell, ``exec`` or ``eval``.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw
from pydantic import ValidationError

from src.ai_scientist.competition_schemas import (
    ExecutionArtifact,
    ExecutionRequest,
    ExecutionResult,
    competition_id,
    now_utc,
)


class ExecutionAdapter:
    """Execute a small operation whitelist inside one explicit project root."""

    OPERATIONS = {
        "inspect_dataset", "describe_dataset", "missingness", "correlation",
        "linear_regression", "grouped_summary", "frequency_table", "contingency_table",
        "time_series_summary", "text_summary", "permutation_group_comparison",
        "plot_histogram", "plot_scatter", "run_simulation",
    }

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root or ".").resolve()

    def capabilities(self) -> dict[str, Any]:
        return {
            "execution_available": True,
            "backend": "controlled_local_deterministic",
            "operations": sorted(self.OPERATIONS),
            "arbitrary_code_execution": False,
        }

    def default_dataset_requests(
        self,
        dataset_path: str,
        output_directory: str,
        *,
        seed: int = 0,
        preferred_terms: str = "",
    ) -> list[dict[str, Any]]:
        """Build a bounded, deterministic analysis bundle from dataset structure."""

        path = self._safe_path(dataset_path, must_exist=True)
        frame = self._load_dataset(path)
        if frame.empty:
            raise ValueError("dataset contains no rows")
        if len(frame) > 1_000_000 or len(frame.columns) > 500:
            raise ValueError("dataset exceeds the automatic analysis boundary")

        terms = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", preferred_terms.lower()))

        def relevance(column: str) -> tuple[int, int]:
            tokens = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", str(column).lower().replace("_", " ")))
            return (len(tokens & terms), -list(map(str, frame.columns)).index(str(column)))

        numeric = sorted(map(str, frame.select_dtypes(include="number").columns), key=relevance, reverse=True)[:8]
        categorical = [
            str(column)
            for column in frame.columns
            if not pd.api.types.is_numeric_dtype(frame[column])
            and 2 <= frame[column].nunique(dropna=True) <= 50
        ][:5]
        datetime_columns: list[str] = []
        text_columns: list[str] = []
        for column in frame.select_dtypes(exclude="number").columns:
            values = frame[column].dropna().astype(str)
            if values.empty:
                continue
            name = str(column)
            time_hint = bool(re.search(r"date|time|year|month|day|日期|时间", name, re.I))
            # Require numeric date-like components; hyphenated category labels
            # such as "Iris-setosa" must not be sent through the date parser.
            formatted_ratio = float(values.str.contains(r"\d{1,4}[-/:]\d{1,2}").mean())
            parsed_ratio = (
                float(pd.to_datetime(values.head(500), errors="coerce", utc=True).notna().mean())
                if time_hint or formatted_ratio >= 0.8
                else 0.0
            )
            if parsed_ratio >= 0.8 and (time_hint or formatted_ratio >= 0.8):
                datetime_columns.append(name)
            elif frame[column].nunique(dropna=True) > 50 and float(values.str.len().mean()) >= 20:
                text_columns.append(name)

        def request(operation: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
            return {
                "operation": operation,
                "inputs": {"dataset_path": dataset_path},
                "parameters": parameters or {},
                "seed": seed,
                # Artifact filenames already identify the operation. Keeping a
                # shared run directory avoids exceeding Windows path limits in
                # deeply nested project/test workspaces.
                "output_directory": output_directory,
                "provenance": {"selection": "deterministic_structure_and_question_match"},
            }

        requests = [request("inspect_dataset"), request("describe_dataset"), request("missingness")]
        if numeric:
            requests.append(request("plot_histogram", {"column": numeric[0], "bins": 20}))
        if len(numeric) >= 2:
            correlation_parameters: dict[str, Any] = {"columns": numeric, "method": "pearson"}
            if categorical:
                correlation_parameters["group_by"] = categorical[0]
            requests.extend([
                request("correlation", correlation_parameters),
                request("plot_scatter", {"x": numeric[0], "y": numeric[1]}),
                request("linear_regression", {"target": numeric[1], "features": [numeric[0]]}),
            ])
        if categorical:
            requests.append(request("frequency_table", {"columns": categorical}))
        if categorical and numeric:
            requests.extend([
                request("grouped_summary", {"group_by": categorical[0], "columns": numeric}),
            ])
        if len(categorical) >= 2:
            requests.append(request("contingency_table", {"row": categorical[0], "column": categorical[1]}))
        if categorical and len(numeric) >= 1 and frame[categorical[0]].nunique(dropna=True) == 2:
            requests.append(request("permutation_group_comparison", {
                "value": numeric[0], "group": categorical[0], "iterations": 2000,
            }))
        if datetime_columns and numeric:
            requests.append(request("time_series_summary", {
                "time_column": datetime_columns[0], "value_columns": numeric,
            }))
        if text_columns:
            requests.append(request("text_summary", {"columns": text_columns}))
        return requests

    def execute(self, task: ExecutionRequest | dict[str, Any]) -> dict[str, Any]:
        started = now_utc()
        started_clock = time.perf_counter()
        if isinstance(task, dict):
            request_id = str(task.get("request_id") or competition_id("request"))
            operation = str(task.get("operation") or "invalid")
            raw_seed = task.get("seed", 0)
            seed = int(raw_seed) if isinstance(raw_seed, int) and not isinstance(raw_seed, bool) else 0
        else:
            request_id, operation, seed = task.request_id, task.operation, task.seed
        try:
            request = task if isinstance(task, ExecutionRequest) else ExecutionRequest.model_validate(task)
            input_paths = self._input_paths(request)
            input_checksums = {key: self._sha256(path) for key, path in input_paths.items()}
            fingerprint = self._fingerprint(request, input_checksums)
            output_dir = self._safe_path(request.output_directory, must_exist=False)
            output_dir.mkdir(parents=True, exist_ok=True)
            metrics, paths, log = self._handlers()[request.operation](request, input_paths, output_dir)
            artifacts = [self._artifact(path) for path in paths]
            status, failure_reason, actual_parameters = "success", None, request.parameters
        except (ValidationError, ValueError, KeyError, FileNotFoundError) as exc:
            input_checksums = {}
            fingerprint = self._fallback_fingerprint(task)
            metrics, artifacts = {}, []
            log = [f"Rejected before execution: {type(exc).__name__}"]
            status, failure_reason, actual_parameters = "rejected", str(exc), {}
        except Exception as exc:  # deterministic failures are returned, never swallowed
            input_checksums = locals().get("input_checksums", {})
            fingerprint = locals().get("fingerprint", self._fallback_fingerprint(task))
            metrics, artifacts = {}, []
            log = [f"Execution failed: {type(exc).__name__}"]
            status, failure_reason = "failed", str(exc)
            actual_parameters = getattr(locals().get("request"), "parameters", {})
        result = ExecutionResult(
            request_id=request_id,
            operation=operation,
            status=status,
            started_at=started,
            finished_at=now_utc(),
            duration_ms=max(0, round((time.perf_counter() - started_clock) * 1000)),
            seed=seed,
            input_fingerprint=fingerprint,
            input_checksums=input_checksums,
            actual_parameters=actual_parameters,
            software_versions={
                "python": platform.python_version(), "numpy": np.__version__,
                "pandas": pd.__version__, "platform": sys.platform,
            },
            metrics=metrics,
            artifacts=artifacts,
            run_log=log,
            failure_reason=failure_reason,
        )
        return result.model_dump(mode="json")

    def _handlers(self) -> dict[str, Callable]:
        return {
            "inspect_dataset": self._inspect_dataset,
            "describe_dataset": self._describe_dataset,
            "missingness": self._missingness,
            "correlation": self._correlation,
            "linear_regression": self._linear_regression,
            "grouped_summary": self._grouped_summary,
            "frequency_table": self._frequency_table,
            "contingency_table": self._contingency_table,
            "time_series_summary": self._time_series_summary,
            "text_summary": self._text_summary,
            "permutation_group_comparison": self._permutation_group_comparison,
            "plot_histogram": self._plot_histogram,
            "plot_scatter": self._plot_scatter,
            "run_simulation": self._run_simulation,
        }

    def _input_paths(self, request: ExecutionRequest) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        for key in ("dataset_path", "observations_path"):
            if key in request.inputs:
                paths[key] = self._safe_path(str(request.inputs[key]), must_exist=True)
        if request.operation != "run_simulation" and "dataset_path" not in paths:
            raise ValueError("dataset_path is required for this operation")
        return paths

    def _safe_path(self, value: str, must_exist: bool) -> Path:
        candidate = (self.project_root / value).resolve()
        try:
            candidate.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("path escapes the project boundary") from exc
        if must_exist and not candidate.is_file():
            raise FileNotFoundError(f"input file does not exist: {value}")
        return candidate

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65_536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fallback_fingerprint(task: Any) -> str:
        if hasattr(task, "model_dump"):
            task = task.model_dump(mode="json")
        payload = json.dumps(task, ensure_ascii=True, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _fingerprint(self, request: ExecutionRequest, checksums: dict[str, str]) -> str:
        return self._fallback_fingerprint({"request": request.model_dump(mode="json"), "input_checksums": checksums})

    def _artifact(self, path: Path) -> ExecutionArtifact:
        media = {".json": "application/json", ".csv": "text/csv", ".png": "image/png"}.get(
            path.suffix.lower(), "application/octet-stream"
        )
        return ExecutionArtifact(
            artifact_type=path.stem,
            relative_path=path.relative_to(self.project_root).as_posix(),
            media_type=media,
            checksum_sha256=self._sha256(path),
            size_bytes=path.stat().st_size,
        )

    @staticmethod
    def _load_dataset(path: Path) -> pd.DataFrame:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        if suffix == ".tsv":
            return pd.read_csv(path, sep="\t")
        if suffix == ".json":
            return pd.read_json(path)
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        raise ValueError(f"unsupported dataset extension: {suffix}")

    @staticmethod
    def _write_json(path: Path, payload: Any) -> Path:
        # Keep individual handlers independently robust when execution uses a
        # relative project store or a caller invokes a handler-specific path.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path

    def _inspect_dataset(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        result = {
            "rows": int(len(frame)), "columns": int(len(frame.columns)),
            "column_names": list(map(str, frame.columns)),
            "dtypes": {str(key): str(value) for key, value in frame.dtypes.items()},
        }
        path = self._write_json(output_dir / "dataset_inspection.json", result)
        return result, [path], [f"Inspected {len(frame)} rows and {len(frame.columns)} columns."]

    def _describe_dataset(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        described = frame.describe(include="all").transpose().reset_index(names="column")
        csv_path = output_dir / "descriptive_statistics.csv"
        described.to_csv(csv_path, index=False)
        metrics = {"rows": int(len(frame)), "numeric_columns": int(len(frame.select_dtypes(include="number").columns))}
        json_path = self._write_json(output_dir / "descriptive_statistics.json", described.to_dict(orient="records"))
        return metrics, [json_path, csv_path], ["Computed deterministic descriptive statistics."]

    def _missingness(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        counts = frame.isna().sum()
        result = {
            str(column): {"missing_count": int(counts[column]), "missing_fraction": float(counts[column] / max(len(frame), 1))}
            for column in frame.columns
        }
        path = self._write_json(output_dir / "missingness.json", result)
        return {"total_missing": int(counts.sum())}, [path], ["Computed missing values without imputation."]

    def _correlation(self, request, paths, output_dir):
        source = self._load_dataset(paths["dataset_path"])
        requested_columns = request.parameters.get("columns")
        numeric = source.select_dtypes(include="number")
        if isinstance(requested_columns, list) and requested_columns:
            missing = [str(item) for item in requested_columns if str(item) not in numeric.columns]
            if missing:
                raise ValueError(f"correlation columns are missing or non-numeric: {missing}")
            numeric = numeric[[str(item) for item in requested_columns]]
        if numeric.shape[1] < 2:
            raise ValueError("correlation requires at least two numeric columns")
        method = str(request.parameters.get("method", "pearson")).lower()
        if method not in {"pearson", "spearman"}:
            raise ValueError("correlation method must be pearson or spearman")
        matrix = numeric.corr(method=method)
        path = output_dir / "correlation.csv"
        matrix.to_csv(path)
        pairs: list[dict[str, Any]] = []
        group_by = str(request.parameters.get("group_by") or "")
        group_values: list[tuple[str, pd.DataFrame]] = [("overall", source)]
        if group_by:
            if group_by not in source.columns:
                raise ValueError("correlation group_by column does not exist")
            if source[group_by].nunique(dropna=True) > 50:
                raise ValueError("correlation group_by exceeds 50 groups")
            group_values.extend((str(name), group) for name, group in source.groupby(group_by, dropna=False))
        columns = list(numeric.columns)
        for group_name, group in group_values:
            for left_index, left in enumerate(columns):
                for right in columns[left_index + 1 :]:
                    clean = group[[left, right]].apply(pd.to_numeric, errors="coerce").dropna()
                    if len(clean) < 4:
                        continue
                    r = float(clean[left].corr(clean[right], method=method))
                    row: dict[str, Any] = {
                        "group": group_name,
                        "x": left,
                        "y": right,
                        "method": method,
                        "n": int(len(clean)),
                        "coefficient": r,
                    }
                    if method == "pearson" and abs(r) < 1:
                        z = math.atanh(r)
                        margin = 1.959963984540054 / math.sqrt(len(clean) - 3)
                        row["ci95_fisher_z"] = [math.tanh(z - margin), math.tanh(z + margin)]
                        slope, intercept = np.polyfit(clean[left].to_numpy(), clean[right].to_numpy(), 1)
                        row.update({"slope": float(slope), "intercept": float(intercept), "r_squared": r * r})
                    pairs.append(row)
        details_path = self._write_json(output_dir / "correlation_details.json", pairs)
        return {
            "columns": columns,
            "method": method,
            "group_by": group_by or None,
            "pair_count": len(pairs),
            "pairs": pairs,
        }, [path, details_path], [f"Computed deterministic {method.title()} correlations."]

    def _linear_regression(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        target, features = str(request.parameters.get("target", "")), request.parameters.get("features")
        if target not in frame.columns or not isinstance(features, list) or not features:
            raise ValueError("target and a non-empty features list must name dataset columns")
        if any(str(item) not in frame.columns for item in features):
            raise ValueError("one or more feature columns do not exist")
        clean = frame[[*features, target]].dropna()
        if len(clean) < len(features) + 2:
            raise ValueError("not enough complete rows for regression")
        x, y = clean[features].to_numpy(dtype=float), clean[target].to_numpy(dtype=float)
        design = np.column_stack([np.ones(len(x)), x])
        coefficients, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
        residual = y - design @ coefficients
        ss_res, ss_total = float(np.sum(residual**2)), float(np.sum((y - y.mean()) ** 2))
        result = {
            "intercept": float(coefficients[0]),
            "coefficients": {str(name): float(value) for name, value in zip(features, coefficients[1:])},
            "r_squared": float(1 - ss_res / ss_total) if ss_total else 1.0,
            "rmse": float(np.sqrt(np.mean(residual**2))), "n": int(len(clean)),
        }
        path = self._write_json(output_dir / "linear_regression.json", result)
        return result, [path], [f"Fit deterministic least-squares regression on {len(clean)} rows."]

    def _grouped_summary(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        group_by = str(request.parameters.get("group_by") or "")
        if group_by not in frame.columns:
            raise ValueError("grouped_summary group_by column does not exist")
        if frame[group_by].nunique(dropna=True) > 100:
            raise ValueError("grouped_summary exceeds 100 groups")
        columns = request.parameters.get("columns")
        numeric = list(frame.select_dtypes(include="number").columns)
        if isinstance(columns, list) and columns:
            numeric = [str(item) for item in columns]
            if any(item not in frame.columns for item in numeric):
                raise ValueError("one or more grouped_summary columns do not exist")
        if not numeric:
            raise ValueError("grouped_summary requires numeric columns")
        result: list[dict[str, Any]] = []
        for name, group in frame.groupby(group_by, dropna=False):
            for column in numeric:
                values = pd.to_numeric(group[column], errors="coerce").dropna()
                if values.empty:
                    continue
                result.append({
                    "group": str(name), "column": column, "n": int(len(values)),
                    "mean": float(values.mean()), "std": float(values.std(ddof=1)) if len(values) > 1 else None,
                    "min": float(values.min()), "median": float(values.median()), "max": float(values.max()),
                })
        path = self._write_json(output_dir / "grouped_summary.json", result)
        return {"group_by": group_by, "groups": int(frame[group_by].nunique(dropna=False)), "summaries": result}, [path], ["Computed grouped descriptive statistics."]

    def _frequency_table(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        columns = request.parameters.get("columns")
        if not isinstance(columns, list) or not columns:
            columns = list(frame.select_dtypes(exclude="number").columns)[:10]
        if any(str(item) not in frame.columns for item in columns):
            raise ValueError("one or more frequency columns do not exist")
        result: dict[str, list[dict[str, Any]]] = {}
        for column in map(str, columns):
            counts = frame[column].fillna("<MISSING>").astype(str).value_counts().head(100)
            result[column] = [
                {"value": value, "count": int(count), "fraction": float(count / max(len(frame), 1))}
                for value, count in counts.items()
            ]
        path = self._write_json(output_dir / "frequency_tables.json", result)
        return {"columns": list(result), "tables": result}, [path], ["Computed bounded categorical frequency tables."]

    def _contingency_table(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        row = str(request.parameters.get("row") or "")
        column = str(request.parameters.get("column") or "")
        if row not in frame.columns or column not in frame.columns or row == column:
            raise ValueError("contingency row and column must name two distinct columns")
        if frame[row].nunique(dropna=False) > 100 or frame[column].nunique(dropna=False) > 100:
            raise ValueError("contingency dimensions exceed 100 categories")
        table = pd.crosstab(frame[row].fillna("<MISSING>"), frame[column].fillna("<MISSING>"), dropna=False)
        csv_path = output_dir / "contingency_table.csv"
        table.to_csv(csv_path)
        payload = {
            "row": row, "column": column,
            "row_labels": list(map(str, table.index)), "column_labels": list(map(str, table.columns)),
            "counts": table.to_numpy(dtype=int).tolist(), "n": int(table.to_numpy().sum()),
        }
        json_path = self._write_json(output_dir / "contingency_table.json", payload)
        return payload, [json_path, csv_path], ["Computed categorical contingency counts without inferential claims."]

    def _time_series_summary(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        time_column = str(request.parameters.get("time_column") or "")
        value_columns = request.parameters.get("value_columns")
        if time_column not in frame.columns:
            raise ValueError("time_series_summary time_column does not exist")
        if not isinstance(value_columns, list) or not value_columns:
            value_columns = list(frame.select_dtypes(include="number").columns)[:10]
        parsed_time = pd.to_datetime(frame[time_column], errors="coerce", utc=True)
        if parsed_time.notna().sum() < 3:
            raise ValueError("time column has fewer than three parseable values")
        result: list[dict[str, Any]] = []
        for column in map(str, value_columns):
            if column not in frame.columns:
                raise ValueError("time-series value column does not exist")
            clean = pd.DataFrame({"time": parsed_time, "value": pd.to_numeric(frame[column], errors="coerce")}).dropna().sort_values("time")
            if len(clean) < 3:
                continue
            elapsed_days = (clean["time"] - clean["time"].iloc[0]).dt.total_seconds().to_numpy() / 86400
            slope = float(np.polyfit(elapsed_days, clean["value"].to_numpy(), 1)[0]) if np.ptp(elapsed_days) else 0.0
            result.append({
                "column": column, "n": int(len(clean)), "start": clean["time"].iloc[0].isoformat(),
                "end": clean["time"].iloc[-1].isoformat(), "mean": float(clean["value"].mean()),
                "trend_per_day": slope, "lag1_autocorrelation": float(clean["value"].autocorr(lag=1)),
            })
        path = self._write_json(output_dir / "time_series_summary.json", result)
        return {"time_column": time_column, "series": result}, [path], ["Computed deterministic time-series summaries."]

    def _text_summary(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        columns = request.parameters.get("columns")
        if not isinstance(columns, list) or not columns:
            columns = list(frame.select_dtypes(include=["object", "string"]).columns)[:5]
        stopwords = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "的", "了", "和", "是", "在"}
        result: dict[str, Any] = {}
        for column in map(str, columns):
            if column not in frame.columns:
                raise ValueError("text_summary column does not exist")
            values = frame[column].dropna().astype(str)
            tokens = [token.lower() for value in values for token in re.findall(r"[\w\u4e00-\u9fff]+", value) if token.lower() not in stopwords]
            result[column] = {
                "documents": int(len(values)), "characters": int(sum(map(len, values))),
                "tokens": len(tokens), "unique_tokens": len(set(tokens)),
                "top_tokens": [{"token": token, "count": count} for token, count in Counter(tokens).most_common(50)],
            }
        path = self._write_json(output_dir / "text_summary.json", result)
        return {"columns": list(result), "summaries": result}, [path], ["Computed bounded lexical summaries; no semantic interpretation was inferred."]

    def _permutation_group_comparison(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        value = str(request.parameters.get("value") or "")
        group = str(request.parameters.get("group") or "")
        iterations = self._bounded_int(request.parameters, "iterations", 100, 20_000)
        if value not in frame.columns or group not in frame.columns:
            raise ValueError("comparison value and group columns must exist")
        clean = pd.DataFrame({"value": pd.to_numeric(frame[value], errors="coerce"), "group": frame[group]}).dropna()
        labels = list(clean["group"].unique())
        if len(labels) != 2:
            raise ValueError("permutation comparison requires exactly two groups")
        first = clean.loc[clean["group"] == labels[0], "value"].to_numpy()
        second = clean.loc[clean["group"] == labels[1], "value"].to_numpy()
        observed = float(first.mean() - second.mean())
        pooled = clean["value"].to_numpy().copy()
        rng = np.random.default_rng(request.seed)
        exceedances = 0
        for _ in range(iterations):
            rng.shuffle(pooled)
            difference = float(pooled[: len(first)].mean() - pooled[len(first) :].mean())
            exceedances += abs(difference) >= abs(observed)
        pooled_sd = float(clean["value"].std(ddof=1))
        payload = {
            "value": value, "group": group, "groups": list(map(str, labels)),
            "n": [int(len(first)), int(len(second))], "means": [float(first.mean()), float(second.mean())],
            "observed_mean_difference": observed,
            "standardized_mean_difference": observed / pooled_sd if pooled_sd else 0.0,
            "iterations": iterations, "two_sided_permutation_p": float((exceedances + 1) / (iterations + 1)),
        }
        path = self._write_json(output_dir / "permutation_group_comparison.json", payload)
        return payload, [path], [f"Ran a seeded two-sided permutation test with {iterations} iterations."]

    def _plot_histogram(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        column = str(request.parameters.get("column", ""))
        if column not in frame.columns:
            raise ValueError("histogram column does not exist")
        values = pd.to_numeric(frame[column], errors="coerce").dropna().to_numpy()
        if not len(values):
            raise ValueError("histogram column has no numeric values")
        bins = self._bounded_int(request.parameters, "bins", 2, 100)
        counts, _ = np.histogram(values, bins=bins)
        path = output_dir / "histogram.png"
        self._bar_png(counts, path)
        return {"column": column, "count": int(len(values)), "bins": bins}, [path], ["Rendered histogram PNG."]

    def _plot_scatter(self, request, paths, output_dir):
        frame = self._load_dataset(paths["dataset_path"])
        x_name, y_name = str(request.parameters.get("x", "")), str(request.parameters.get("y", ""))
        if x_name not in frame.columns or y_name not in frame.columns:
            raise ValueError("scatter x and y columns must exist")
        clean = frame[[x_name, y_name]].apply(pd.to_numeric, errors="coerce").dropna()
        if clean.empty:
            raise ValueError("scatter columns have no paired numeric values")
        path = output_dir / "scatter.png"
        self._scatter_png(clean[x_name].to_numpy(), clean[y_name].to_numpy(), path)
        return {"x": x_name, "y": y_name, "count": int(len(clean))}, [path], ["Rendered scatter PNG."]

    def _run_simulation(self, request, paths, output_dir):
        mode = request.parameters.get("mode")
        if mode == "generate_damped_oscillator":
            return self._generate_oscillator(request, output_dir)
        if mode == "fit_damped_oscillator":
            if "observations_path" not in paths:
                raise ValueError("observations_path is required for fitting")
            return self._fit_oscillator(request, paths["observations_path"], output_dir)
        raise ValueError("run_simulation mode must be generate_damped_oscillator or fit_damped_oscillator")

    def _generate_oscillator(self, request, output_dir):
        p = request.parameters
        damping = self._bounded_float(p, "damping", 0.001, 5)
        omega = self._bounded_float(p, "omega", 0.01, 100)
        amplitude = self._bounded_float(p, "amplitude", 0.01, 100)
        phase = self._bounded_float(p, "phase", -2 * math.pi, 2 * math.pi)
        noise_std = self._bounded_float(p, "noise_std", 0, 10)
        duration = self._bounded_float(p, "duration", 0.1, 1000)
        samples = self._bounded_int(p, "samples", 20, 100_000)
        rng = np.random.default_rng(request.seed)
        time_values = np.linspace(0, duration, samples)
        clean = amplitude * np.exp(-damping * time_values) * np.cos(omega * time_values + phase)
        observed = clean + rng.normal(0, noise_std, samples)
        frame = pd.DataFrame({"time": time_values, "displacement": observed, "clean_signal": clean})
        data_path = output_dir / "observations.csv"
        frame.to_csv(data_path, index=False)
        truth_path = self._write_json(output_dir / "ground_truth.json", {
            "damping": damping, "omega": omega, "amplitude": amplitude,
            "phase": phase, "noise_std": noise_std,
        })
        figure_path = output_dir / "observations.png"
        self._scatter_png(time_values, observed, figure_path, line_x=time_values, line_y=clean)
        return {"samples": samples, "noise_std": noise_std}, [data_path, truth_path, figure_path], ["Generated seeded damped-oscillator observations."]

    def _fit_oscillator(self, request, observations_path, output_dir):
        p = request.parameters
        damping_min = self._bounded_float(p, "damping_min", 0.001, 5)
        damping_max = self._bounded_float(p, "damping_max", 0.001, 5)
        omega_min = self._bounded_float(p, "omega_min", 0.01, 100)
        omega_max = self._bounded_float(p, "omega_max", 0.01, 100)
        damping_points = self._bounded_int(p, "damping_points", 3, 500)
        omega_points = self._bounded_int(p, "omega_points", 3, 500)
        if damping_min >= damping_max or omega_min >= omega_max:
            raise ValueError("simulation parameter minima must be below maxima")
        if damping_points * omega_points > 250_000:
            raise ValueError("simulation grid exceeds 250000 evaluations")
        frame = pd.read_csv(observations_path)
        if not {"time", "displacement"}.issubset(frame.columns):
            raise ValueError("observations require time and displacement columns")
        time_values = frame["time"].to_numpy(dtype=float)
        observed = frame["displacement"].to_numpy(dtype=float)
        amplitude = self._bounded_float(p, "amplitude", 0.01, 100)
        phase = self._bounded_float(p, "phase", -2 * math.pi, 2 * math.pi)
        best = None
        rows: list[dict[str, float]] = []
        dampings = np.linspace(damping_min, damping_max, damping_points)
        omegas = np.linspace(omega_min, omega_max, omega_points)
        for damping in dampings:
            decay = amplitude * np.exp(-damping * time_values)
            for omega in omegas:
                predicted = decay * np.cos(omega * time_values + phase)
                rmse = float(np.sqrt(np.mean((observed - predicted) ** 2)))
                rows.append({"damping": float(damping), "omega": float(omega), "rmse": rmse})
                if best is None or rmse < best[0]:
                    best = (rmse, float(damping), float(omega), predicted)
        assert best is not None
        result = {
            "rmse": best[0], "best_damping": best[1], "best_omega": best[2],
            "evaluations": damping_points * omega_points,
            "damping_step": float(dampings[1] - dampings[0]),
            "omega_step": float(omegas[1] - omegas[0]),
            "best_on_boundary": bool(
                np.isclose(best[1], dampings[0]) or np.isclose(best[1], dampings[-1])
                or np.isclose(best[2], omegas[0]) or np.isclose(best[2], omegas[-1])
            ),
        }
        result_path = self._write_json(output_dir / "fit_result.json", result)
        grid_path = output_dir / "fit_grid.csv"
        pd.DataFrame(rows).to_csv(grid_path, index=False)
        figure_path = output_dir / "fit.png"
        self._scatter_png(time_values, observed, figure_path, line_x=time_values, line_y=best[3])
        return result, [result_path, grid_path, figure_path], [f"Evaluated {result['evaluations']} parameter combinations."]

    @staticmethod
    def _bounded_float(parameters, name, low, high):
        if name not in parameters:
            raise ValueError(f"missing required parameter: {name}")
        value = float(parameters[name])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}")
        return value

    @staticmethod
    def _bounded_int(parameters, name, low, high):
        if name not in parameters or isinstance(parameters[name], bool):
            raise ValueError(f"missing integer parameter: {name}")
        value = int(parameters[name])
        if value != float(parameters[name]) or not low <= value <= high:
            raise ValueError(f"{name} must be an integer between {low} and {high}")
        return value

    @staticmethod
    def _bar_png(values: np.ndarray, path: Path) -> None:
        image = Image.new("RGB", (800, 480), "white")
        draw = ImageDraw.Draw(image)
        maximum, width = max(int(np.max(values)), 1), 720 / max(len(values), 1)
        for index, value in enumerate(values):
            height = 400 * int(value) / maximum
            draw.rectangle((50 + index * width, 430 - height, 48 + (index + 1) * width, 430), fill="#2563eb")
        draw.line((50, 30, 50, 430, 770, 430), fill="black", width=2)
        image.save(path)

    @staticmethod
    def _scatter_png(x, y, path: Path, line_x=None, line_y=None) -> None:
        image = Image.new("RGB", (800, 480), "white")
        draw = ImageDraw.Draw(image)
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        x_min, x_max = float(x.min()), float(x.max())
        y_values = np.concatenate([y, np.asarray(line_y, dtype=float)]) if line_y is not None else y
        y_min, y_max = float(y_values.min()), float(y_values.max())
        sx = lambda value: 50 + 720 * (float(value) - x_min) / max(x_max - x_min, 1e-12)
        sy = lambda value: 430 - 400 * (float(value) - y_min) / max(y_max - y_min, 1e-12)
        if line_x is not None and line_y is not None:
            draw.line([(sx(a), sy(b)) for a, b in zip(line_x, line_y)], fill="#dc2626", width=3)
        for a, b in zip(x, y):
            px, py = sx(a), sy(b)
            draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill="#2563eb")
        draw.line((50, 30, 50, 430, 770, 430), fill="black", width=2)
        image.save(path)
