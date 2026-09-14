from __future__ import annotations

import argparse
import json
from pathlib import Path

from dataops_agent.rules import detect_issues, load_csv
from dataops_agent.schemas import DetectionMetrics


def evaluate_detection(
    input_csv: str | Path,
    labels_json: str | Path,
    *,
    dataset_name: str | None = None,
) -> DetectionMetrics:
    df = load_csv(str(input_csv))
    labels = json.loads(Path(labels_json).read_text(encoding="utf-8"))
    dataset = dataset_name or labels.get("dataset_name") or "doctors"
    detected = {
        _issue_key(item.issue_type.value, item.row_index, item.field)
        for item in detect_issues(df, dataset_name=dataset)
    }
    expected = {
        _issue_key(item["issue_type"], int(item["row_index"]), item["field"])
        for item in labels["issues"]
    }
    true_positive = len(detected & expected)
    false_positive = len(detected - expected)
    false_negative = len(expected - detected)
    precision = true_positive / (true_positive + false_positive) if detected else 0.0
    recall = true_positive / (true_positive + false_negative) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return DetectionMetrics(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--dataset-name")
    args = parser.parse_args()
    metrics = evaluate_detection(args.input, args.labels, dataset_name=args.dataset_name)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
    print(metrics.model_dump_json(indent=2))


def _issue_key(issue_type: str, row_index: int, field: str) -> tuple[str, int, str]:
    return issue_type, row_index, field


if __name__ == "__main__":
    main()
