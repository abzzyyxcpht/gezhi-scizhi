"""Data summaries preserve missing values and render only valid observations."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.agents import stats


@pytest.fixture
def stats_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "ARTIFACT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(stats, "log_tool", lambda *args, **kwargs: None)
    return tmp_path


def _analyze_csv(root: Path, content: str):
    path = root / "experiment.csv"
    path.write_text(content, encoding="utf-8")
    result = stats.analyze(path)
    json.dumps(result, allow_nan=False)
    return result


def test_single_observation_keeps_undefined_standard_deviation(stats_workspace):
    result = _analyze_csv(stats_workspace, "value\n3\n")
    assert result["describe"]["value"]["count"] == 1
    assert result["describe"]["value"]["mean"] == 3
    assert result["describe"]["value"]["std"] is None
    assert result["charts"]
    assert any("标准差" in note for note in result["chart_advice"])


def test_infinite_samples_are_counted_and_excluded_from_statistics(stats_workspace):
    result = _analyze_csv(stats_workspace, "id,value\na,1\nb,3\nc,inf\nd,-inf\ne,\n")
    assert result["shape"] == [5, 2]
    assert result["missing"] == {"value": 1}
    assert result["non_finite"] == {"value": 2}
    assert result["describe"]["value"]["count"] == 2
    assert result["describe"]["value"]["mean"] == 2
    assert result["outliers"] == {}
    assert result["charts"]
    assert all((stats_workspace / "artifacts" / name).is_file() for name in result["charts"])
    assert any("无穷" in note and "2" in note for note in result["chart_advice"])


def test_empty_numeric_column_has_no_fabricated_values_or_chart(stats_workspace):
    result = _analyze_csv(stats_workspace, "id,value\na,\nb,\n")
    assert result["describe"]["value"]["count"] == 0
    assert all(value is None for key, value in result["describe"]["value"].items()
               if key != "count")
    assert result["charts"] == []
    assert result["missing"] == {"value": 2}
    assert any("有效" in note for note in result["chart_advice"])


def test_excel_numeric_headers_are_usable_json_labels(stats_workspace):
    path = stats_workspace / "numeric-headers.xlsx"
    pd.DataFrame({2026: [1, 2, 3]}).to_excel(path, index=False)
    result = stats.analyze(path)
    json.dumps(result, allow_nan=False)
    assert result["columns"] == ["2026"]
    assert result["describe"]["2026"]["mean"] == 2
    assert result["charts"]


def test_excel_labels_remain_distinct_after_conversion_to_strings(stats_workspace):
    path = stats_workspace / "mixed-headers.xlsx"
    pd.DataFrame([[1, 10], [3, 30]], columns=[2026, "2026"]).to_excel(path, index=False)
    result = stats.analyze(path)
    json.dumps(result, allow_nan=False)
    assert len(result["columns"]) == len(set(result["columns"])) == 2
    assert all(isinstance(label, str) for label in result["columns"])
    assert sorted(column["mean"] for column in result["describe"].values()) == [2, 20]


def test_pairwise_correlation_reports_its_actual_sample_size(stats_workspace):
    result = _analyze_csv(stats_workspace, "x,y\n1,2\n2,4\ninf,6\n4,\n")
    assert result["correlations"] == [{"pair": ["x", "y"], "r": 1.0, "n": 2}]
    assert result["describe"]["x"]["count"] == 3
    assert result["describe"]["y"]["count"] == 3


def test_undefined_correlations_do_not_generate_a_misleading_heatmap(stats_workspace):
    result = _analyze_csv(stats_workspace, "x,constant\n1,7\n2,7\n3,7\n")
    assert result["correlations"] == []
    assert all(not Path(name).name.startswith("corr_") for name in result["charts"])
