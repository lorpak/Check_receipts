# Этот файл хранит общий статус обработки и функции записи/резолва результирующих файлов.
import json
import os


processing_status = {
    "is_processing": False,
    "message": "Ожидание запуска",
    "result_files": [],
    "result_full_paths": [],
    "result_file_paths": {},
    "diagnostics": {},
    "progress": 0,
    "has_error": False,
}


def write_status(status_file: str | None):
    if not status_file:
        return
    try:
        tmp_path = status_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(processing_status, f, ensure_ascii=False)
        os.replace(tmp_path, status_file)
    except Exception:
        pass


def open_result_files(file_paths):
    for path in file_paths:
        try:
            if os.path.exists(path) and hasattr(os, "startfile"):
                os.startfile(path)
        except Exception:
            pass


def resolve_existing_result_paths(result_files, output_folder: str):
    resolved = []
    seen = set()
    for file_name in result_files or []:
        candidate = file_name if os.path.isabs(file_name) else os.path.join(output_folder, file_name)
        candidate = os.path.abspath(candidate)
        if os.path.exists(candidate) and candidate not in seen:
            seen.add(candidate)
            resolved.append(candidate)
    return resolved
