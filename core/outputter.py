from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ResultOutputter:
    def __init__(self, output_dir: Path, output_format: str = "csv") -> None:
        normalized_format = (output_format or "csv").lower().strip()
        if normalized_format not in {"csv", "json", "txt"}:
            raise ValueError(f"Unsupported output format: {output_format}")

        self.output_dir = output_dir
        self.output_format = normalized_format
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc).isoformat()
            return value.isoformat()
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    def _output_path(self, table_name: str) -> Path:
        return self.output_dir / f"{table_name}.{self.output_format}"

    def _write_csv(self, table_name: str, rows: list[dict[str, Any]]) -> Path:
        path = self._output_path(table_name)
        if not rows:
            path.write_text("", encoding="utf-8")
            return path

        columns: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in columns:
                    columns.append(key)

        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                normalized = {key: self._stringify(row.get(key)) for key in columns}
                writer.writerow(normalized)
        return path

    def _write_json(self, table_name: str, rows: list[dict[str, Any]]) -> Path:
        path = self._output_path(table_name)
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            normalized_rows.append({key: self._stringify(value) for key, value in row.items()})
        path.write_text(json.dumps(normalized_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_txt(self, table_name: str, rows: list[dict[str, Any]]) -> Path:
        path = self._output_path(table_name)
        lines: list[str] = [f"table={table_name}", f"rows={len(rows)}", ""]

        for idx, row in enumerate(rows, start=1):
            lines.append(f"--- row {idx} ---")
            for key, value in row.items():
                lines.append(f"{key}: {self._stringify(value)}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def write_table(self, table_name: str, rows: list[dict[str, Any]]) -> Path:
        if self.output_format == "csv":
            return self._write_csv(table_name, rows)
        if self.output_format == "json":
            return self._write_json(table_name, rows)
        return self._write_txt(table_name, rows)

    def export_tables(self, tables: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
        stats: dict[str, int] = {}
        for table_name, rows in tables.items():
            output_path = self.write_table(table_name, rows)
            stats[output_path.name] = len(rows)
        return stats
