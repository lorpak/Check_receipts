# Этот файл валидирует входной payload, готовит параметры запуска и передает их в runner.
import os
from datetime import datetime, timedelta

from orchestration.runner import run_processing
from orchestration.sources import expand_creditline_sources, parse_source_entries
from orchestration.state import processing_status, write_status


def get_dates_range(start_date: str, end_date: str):
    dates = []
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def process_payload(payload: dict, status_file: str | None = None):
    try:
        data = payload or {}

        source_folder = data.get("source_folder") or data.get("input_folder") or data.get("local_folder_path")
        source_paths = data.get("source_paths")
        output_folder = data.get("output_folder")
        reports_folder = data.get("reports_folder")
        responses_folder = data.get("responses_folder")
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        event_type = data.get("event_type", "all")

        source_entries = parse_source_entries(source_folder, source_paths)
        source_entries = expand_creditline_sources(source_entries)
        if not source_entries:
            raise ValueError(
                "Источник не существует или в CreditLine не найдены папки EquifaxNew/UCBNew/UCHFNew с "
                "подпапками Reports и Receipts."
            )
        for src in source_entries:
            if not os.path.exists(src):
                raise ValueError(f"Источник не существует: {src}")
            if not (os.path.isdir(src) or os.path.isfile(src)):
                raise ValueError(f"Источник должен быть папкой или файлом: {src}")

        if not output_folder:
            first_source = source_entries[0]
            output_folder = first_source if os.path.isdir(first_source) else os.path.dirname(first_source)

        if not output_folder:
            raise ValueError("Укажите папку для сохранения результатов")

        os.makedirs(output_folder, exist_ok=True)

        if reports_folder or responses_folder:
            if not reports_folder or not responses_folder:
                raise ValueError("Для раздельного режима укажите обе папки: reports_folder и responses_folder")
            if not os.path.isdir(reports_folder):
                raise ValueError(f"Папка отчетов не существует: {reports_folder}")
            if not os.path.isdir(responses_folder):
                raise ValueError(f"Папка отбивок не существует: {responses_folder}")

        if not start_date or not end_date:
            raise ValueError("Укажите обе даты: начало и конец диапазона.")
        selected_dates = get_dates_range(start_date, end_date)

        processing_status.update(
            {
                "is_processing": True,
                "message": "Запуск обработки...",
                "result_files": [],
                "result_full_paths": [],
                "result_file_paths": {},
                "diagnostics": {},
                "progress": 0,
                "has_error": False,
            }
        )
        write_status(status_file)

        run_processing(
            source_entries,
            output_folder,
            selected_dates,
            event_type,
            reports_folder,
            responses_folder,
            status_file=status_file,
        )

    except Exception as e:
        processing_status.update(
            {
                "is_processing": False,
                "message": f"Ошибка обработки: {str(e)}",
                "result_files": [],
                "result_full_paths": [],
                "result_file_paths": {},
                "diagnostics": {},
                "progress": 0,
                "has_error": True,
            }
        )
        write_status(status_file)
        raise
