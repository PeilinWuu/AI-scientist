"""Controlled deterministic execution for approved research operations.

This module intentionally does not expose Python, shell, ``exec`` or ``eval``.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
import time
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
        "linear_regression", "plot_histogram", "plot_scatter", "run_simulation",
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
        frame = self._load_dataset(paths["dataset_path"] ).select_dtypes(include="number")
        if frame.shape[1] < 2:
            raise ValueError("correlation requires at least two numeric columns")
        matrix = frame.corr()
        path = output_dir / "correlation.csv"
        matrix.to_csv(path)
        return {"columns": list(matrix.columns)}, [path], ["Computed Pearson correlation matrix."]

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
