import json
import uuid
from pathlib import Path

from app.services.reports.schemas import ReportData


class JSONReportGenerator:
    """Export data laporan ke JSON."""

    def generate(self, data: ReportData, output_dir: str) -> str:
        """Tulis file JSON dan kembalikan path-nya."""
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        file_path = str(Path(output_dir) / f"{data.report_id}.json")

        payload = data.to_dict()
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

        return file_path
