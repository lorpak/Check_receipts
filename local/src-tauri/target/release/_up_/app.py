import argparse
import concurrent.futures
import json
import os
import re
import shutil
import tempfile
import sys
from datetime import datetime, timedelta

# Ensure local modules (check_receipts.py) are importable with embedded Python.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Глобальное состояние обработки
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

RESPONSE_DIR_CANDIDATES = ["responses", "response", "receipts", "receipt", "отбивки", "отбивка"]
KNOWN_BKI_FOLDERS = ["EquifaxNew", "UCBNew", "UCHFNew"]
CREDITLINE_BKI_ALIAS = {
    "EquifaxNew": "ЭКС",
    "UCBNew": "ОКБ",
    "UCHFNew": "НБКИ",
}


def _write_status(status_file: str | None):
    if not status_file:
        return
    try:
        tmp_path = status_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(processing_status, f, ensure_ascii=False)
        os.replace(tmp_path, status_file)
    except Exception:
        pass


def cleanup_caches():
    """Очистка кешей check_receipts перед новым запуском."""
    try:
        from check_receipts import MAIN_FILE_CACHE, TITLE_CACHE, ERROR_CACHE

        MAIN_FILE_CACHE.clear()
        TITLE_CACHE.clear()
        ERROR_CACHE.clear()
    except Exception:
        pass


def get_dates_range(start_date: str, end_date: str):
    """Преобразует диапазон дат в список дат YYYY-MM-DD."""
    dates = []
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def open_result_files(file_paths):
    """Открывает созданные файлы на локальной машине пользователя."""
    for path in file_paths:
        try:
            if os.path.exists(path) and hasattr(os, "startfile"):
                os.startfile(path)
        except Exception:
            # Не прерываем обработку, если конкретный файл не открылся.
            pass


def _resolve_existing_result_paths(result_files, output_folder: str):
    resolved = []
    seen = set()
    for idx, file_name in enumerate(result_files or []):
        candidate = file_name if os.path.isabs(file_name) else os.path.join(output_folder, file_name)
        candidate = os.path.abspath(candidate)
        if os.path.exists(candidate) and candidate not in seen:
            seen.add(candidate)
            resolved.append(candidate)
    return resolved


def _is_supported_report_name(file_name: str) -> bool:
    name_u = (file_name or "").upper()
    return name_u.startswith(("0XY_FCH", "BD0", "CHP", "CHT_"))


def _response_matches_report(report_name: str, response_name: str) -> bool:
    report_stem = os.path.splitext(report_name)[0]
    report_stem_u = report_stem.upper()
    response_u = response_name.upper()
    report_name_u = report_name.upper()

    if report_name_u.startswith("0XY_FCH"):
        # Формат отбивки ЭКС: <имя_отчета>.XML.<уникальный_код>
        # Пример: 0XY_FCH_20260130_40781.XML.6366...
        return response_u.startswith(report_stem_u + ".XML.")
    if report_name_u.startswith("BD0"):
        return report_stem_u in response_u and "TICKET2" in response_u
    if report_name_u.startswith("CHP"):
        return report_stem_u in response_u and "T" in response_u
    if report_name_u.startswith("CHT_"):
        return response_u.startswith(report_stem_u) and response_u.endswith(".XML")
    return False


def _find_matching_response_for_report(report_file: str, responses_folder: str = None) -> str:
    report_file = os.path.abspath(report_file)
    report_dir = os.path.dirname(report_file)
    report_name = os.path.basename(report_file)

    candidate_dirs = []
    if responses_folder and os.path.isdir(responses_folder):
        candidate_dirs.append(os.path.abspath(responses_folder))

    candidate_dirs.append(report_dir)

    parent_dir = os.path.dirname(report_dir)
    for base in (report_dir, parent_dir):
        if not base or not os.path.isdir(base):
            continue
        for folder_name in RESPONSE_DIR_CANDIDATES:
            candidate = os.path.join(base, folder_name)
            if os.path.isdir(candidate):
                candidate_dirs.append(os.path.abspath(candidate))

    seen = set()
    unique_dirs = []
    for path in candidate_dirs:
        if path not in seen:
            seen.add(path)
            unique_dirs.append(path)

    for resp_dir in unique_dirs:
        try:
            for response_name in os.listdir(resp_dir):
                response_path = os.path.join(resp_dir, response_name)
                if not os.path.isfile(response_path):
                    continue
                if os.path.abspath(response_path) == report_file:
                    continue
                if _response_matches_report(report_name, response_name):
                    return response_path
        except Exception:
            continue

    return ""


def _prepare_single_file_workspace(source_file: str, responses_folder: str = None):
    source_file = os.path.abspath(source_file)
    report_name = os.path.basename(source_file)

    if not _is_supported_report_name(report_name):
        raise ValueError(
            "Неподдерживаемое имя исходного файла. Ожидаются префиксы: 0XY_FCH, BD0, CHP, CHT_."
        )

    response_file = _find_matching_response_for_report(source_file, responses_folder=responses_folder)
    if not response_file:
        raise ValueError(
            f"Для выбранного файла не найдена отбивка: {report_name}. "
            "Проверьте, что файл отбивки находится рядом или в папке responses."
        )

    workspace_dir = tempfile.mkdtemp(prefix="check_receipts_single_")
    reports_dir = os.path.join(workspace_dir, "reports")
    responses_dir = os.path.join(workspace_dir, "responses")
    os.makedirs(reports_dir, exist_ok=True)
    os.makedirs(responses_dir, exist_ok=True)

    shutil.copy2(source_file, os.path.join(reports_dir, report_name))

    response_name = os.path.basename(response_file)
    report_name_u = report_name.upper()
    if report_name_u.startswith("0XY_FCH"):
        # check_receipts.find_file_pairs_between_dirs ожидает строго:
        # <report_stem>.XML.<suffix>
        report_stem = os.path.splitext(report_name)[0]
        m = re.match(rf"^{re.escape(report_stem)}\.xml(\..+)?$", response_name, flags=re.IGNORECASE)
        if m:
            suffix = m.group(1) or ".response"
            response_name = f"{report_stem}.XML{suffix}"
        else:
            response_name = f"{report_stem}.XML.{response_name}"

    shutil.copy2(response_file, os.path.join(responses_dir, response_name))
    return workspace_dir, reports_dir, responses_dir


def _parse_source_entries(source_folder: str, source_paths):
    entries = []
    if isinstance(source_paths, list):
        entries.extend([str(p).strip() for p in source_paths if str(p).strip()])

    source_raw = (source_folder or "").strip()
    if source_raw:
        # Если в поле введено несколько путей через ';', разбираем их.
        if ";" in source_raw and not os.path.exists(source_raw):
            entries.extend([p.strip() for p in source_raw.split(";") if p.strip()])
        else:
            entries.append(source_raw)

    dedup = []
    seen = set()
    for p in entries:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            dedup.append(ap)
    return dedup


def _expand_creditline_sources(source_entries):
    expanded = []
    for src in source_entries:
        src_abs = os.path.abspath(src)
        if os.path.isdir(src_abs) and os.path.basename(src_abs).lower() == "creditline":
            for bki_name in KNOWN_BKI_FOLDERS:
                bki_path = os.path.join(src_abs, bki_name)
                reports_dir = os.path.join(bki_path, "Reports")
                receipts_dir = os.path.join(bki_path, "Receipts")
                if os.path.isdir(bki_path) and os.path.isdir(reports_dir) and os.path.isdir(receipts_dir):
                    expanded.append(os.path.abspath(bki_path))
        else:
            expanded.append(src_abs)

    dedup = []
    seen = set()
    for p in expanded:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup


def _resolve_creditline_reports_receipts(source_folder: str) -> tuple[str, str] | tuple[None, None]:
    """For CreditLine BKI folder enforce strict Reports/Receipts scanning."""
    src_abs = os.path.abspath(source_folder or "")
    if not os.path.isdir(src_abs):
        return None, None
    parent_name = os.path.basename(os.path.dirname(src_abs)).lower()
    folder_name = os.path.basename(src_abs)
    if parent_name != "creditline" or folder_name not in KNOWN_BKI_FOLDERS:
        return None, None

    reports_dir = os.path.join(src_abs, "Reports")
    receipts_dir = os.path.join(src_abs, "Receipts")
    if os.path.isdir(reports_dir) and os.path.isdir(receipts_dir):
        return reports_dir, receipts_dir
    return None, None


def _run_single_source_task(
    source_folder: str,
    output_folder: str,
    selected_dates,
    event_type: str,
    reports_folder: str = None,
    responses_folder: str = None,
):
    """
    Worker for top-level parallelism: process one source and return JSON-serializable result.
    """
    temp_workspace = None
    process_source = source_folder
    process_reports = reports_folder
    process_responses = responses_folder
    source_abs = os.path.abspath(source_folder)

    try:
        import check_receipts
        from check_receipts import main_with_date

        # Strict CreditLine mode: only scan <BKI>/Reports and <BKI>/Receipts.
        cl_reports, cl_receipts = _resolve_creditline_reports_receipts(source_abs)
        if cl_reports and cl_receipts and not os.path.isfile(source_abs):
            process_reports = cl_reports
            process_responses = cl_receipts

        if os.path.isfile(source_abs):
            temp_workspace, process_reports, process_responses = _prepare_single_file_workspace(
                source_abs,
                responses_folder=responses_folder,
            )
            process_source = temp_workspace

        result_files = main_with_date(
            process_source,
            selected_dates,
            event_type,
            progress_callback=None,
            output_folder=output_folder,
            reports_folder=process_reports,
            responses_folder=process_responses,
        )
        return {
            "source": source_abs,
            "result_files": result_files or [],
            "stats": dict(getattr(check_receipts, "LAST_RUN_STATS", {}) or {}),
            "error": "",
        }
    except Exception as e:
        stats = {}
        try:
            import check_receipts as _cr
            stats = dict(getattr(_cr, "LAST_RUN_STATS", {}) or {})
        except Exception:
            stats = {}
        return {
            "source": source_abs,
            "result_files": [],
            "stats": stats,
            "error": str(e),
        }
    finally:
        if temp_workspace:
            try:
                shutil.rmtree(temp_workspace, ignore_errors=True)
            except Exception:
                pass


def run_processing(
    source_entries,
    output_folder: str,
    selected_dates,
    event_type: str,
    reports_folder: str = None,
    responses_folder: str = None,
    status_file: str | None = None,
):
    """Фоновая обработка файлов."""
    global processing_status

    cleanup_caches()

    processing_status.update(
        {
            "is_processing": True,
            "message": "Начинаем обработку...",
            "result_files": [],
            "result_full_paths": [],
            "result_file_paths": {},
            "progress": 0,
            "has_error": False,
        }
    )
    _write_status(status_file)

    try:
        import check_receipts
        from check_receipts import main_with_date

        sources = source_entries if isinstance(source_entries, list) else [source_entries]
        sources = [os.path.abspath(s) for s in sources if s]
        total_sources = max(1, len(sources))

        all_result_files = []
        source_errors = []
        source_diagnostics = []

        # Top-level parallelism for multiple independent sources.
        if total_sources > 1:
            max_parallel_sources = min(total_sources, max(1, os.cpu_count() or 2), 3)
            processing_status["message"] = f"Параллельная обработка источников: {total_sources} (workers={max_parallel_sources})"
            processing_status["progress"] = 0
            _write_status(status_file)

            futures_map = {}
            with concurrent.futures.ProcessPoolExecutor(max_workers=max_parallel_sources) as ex:
                for source_folder in sources:
                    fut = ex.submit(
                        _run_single_source_task,
                        source_folder,
                        output_folder,
                        selected_dates,
                        event_type,
                        reports_folder,
                        responses_folder,
                    )
                    futures_map[fut] = source_folder

                completed = 0
                for fut in concurrent.futures.as_completed(futures_map):
                    source_folder = futures_map.get(fut)
                    completed += 1
                    try:
                        item = fut.result()
                    except Exception as e:
                        source_errors.append({"source": source_folder, "error": str(e)})
                        source_diagnostics.append(
                            {
                                "source": source_folder,
                                "error": str(e),
                                "stats": {},
                            }
                        )
                    else:
                        src = item.get("source") or source_folder
                        err = item.get("error") or ""
                        stats = dict(item.get("stats") or {})
                        files = list(item.get("result_files") or [])
                        all_result_files.extend(files)
                        diag_item = {"source": src, "stats": stats}
                        if err:
                            source_errors.append({"source": src, "error": err})
                            diag_item["error"] = err
                        source_diagnostics.append(diag_item)

                    processing_status["progress"] = int((completed / total_sources) * 100)
                    processing_status["message"] = f"[{completed}/{total_sources}] Обработано источников"
                    _write_status(status_file)
        else:
            for idx, source_folder in enumerate(sources):
                process_source = source_folder
                process_reports = reports_folder
                process_responses = responses_folder
                process_dates = selected_dates
                temp_workspace = None

                # Strict CreditLine mode: only scan <BKI>/Reports and <BKI>/Receipts.
                cl_reports, cl_receipts = _resolve_creditline_reports_receipts(source_folder)
                if cl_reports and cl_receipts and not os.path.isfile(source_folder):
                    process_reports = cl_reports
                    process_responses = cl_receipts

                def update_progress(progress, message, current_idx=idx):
                    local_progress = max(0, min(100, int(progress)))
                    overall = int(((current_idx + (local_progress / 100.0)) / total_sources) * 100)
                    processing_status["progress"] = overall
                    processing_status["message"] = f"[{current_idx + 1}/{total_sources}] {message}"
                    _write_status(status_file)

                try:
                    if os.path.isfile(source_folder):
                        update_progress(3, "Подготовка выбранного файла и поиск отбивки...")
                        temp_workspace, process_reports, process_responses = _prepare_single_file_workspace(
                            source_folder,
                            responses_folder=responses_folder,
                        )
                        process_source = temp_workspace

                    result_files = main_with_date(
                        process_source,
                        process_dates,
                        event_type,
                        update_progress,
                        output_folder=output_folder,
                        reports_folder=process_reports,
                        responses_folder=process_responses,
                    )
                    all_result_files.extend(result_files or [])
                    source_diagnostics.append(
                        {
                            "source": source_folder,
                            "stats": dict(getattr(check_receipts, "LAST_RUN_STATS", {}) or {}),
                        }
                    )
                except Exception as e:
                    source_errors.append({"source": source_folder, "error": str(e)})
                    source_diagnostics.append(
                        {
                            "source": source_folder,
                            "error": str(e),
                            "stats": dict(getattr(check_receipts, "LAST_RUN_STATS", {}) or {}),
                        }
                    )
                finally:
                    if temp_workspace:
                        try:
                            shutil.rmtree(temp_workspace, ignore_errors=True)
                        except Exception:
                            pass

        result_file_paths = {}
        resolved_result_paths = _resolve_existing_result_paths(all_result_files, output_folder)
        for idx, abs_path in enumerate(resolved_result_paths):
            file_key = os.path.basename(abs_path) or f"result_{idx + 1}.xlsx"
            if file_key in result_file_paths:
                file_key = f"{idx + 1}_{file_key}"
            result_file_paths[file_key] = abs_path

        open_result_files(resolved_result_paths)

        has_error = len(source_errors) == total_sources and total_sources > 0
        status_message = (
            f"Обработка завершена. Создано файлов: {len(resolved_result_paths)}"
            if resolved_result_paths
            else "Обработка завершена. Файлы не созданы (см. diagnostics)."
        )
        if source_errors and not has_error:
            status_message += f" Ошибок по источникам: {len(source_errors)}."
        if has_error and source_errors:
            status_message = f"Ошибка обработки: {source_errors[0]['error']}"

        processing_status.update(
            {
                "is_processing": False,
                "message": status_message,
                "result_files": list(result_file_paths.keys()) or all_result_files,
                "result_full_paths": list(result_file_paths.values()),
                "result_file_paths": result_file_paths,
                "diagnostics": {
                    "sources_total": total_sources,
                    "sources": source_diagnostics,
                    "source_errors": source_errors,
                },
                "progress": 0 if has_error else 100,
                "has_error": has_error,
            }
        )
    except Exception as e:
        diagnostics = {}
        try:
            import check_receipts as _cr
            diagnostics = dict(getattr(_cr, "LAST_RUN_STATS", {}) or {})
        except Exception:
            diagnostics = {}
        processing_status.update(
            {
                "is_processing": False,
                "message": f"Ошибка обработки: {str(e)}",
                "result_files": [],
                "result_full_paths": [],
                "result_file_paths": {},
                "diagnostics": diagnostics,
                "progress": 0,
                "has_error": True,
            }
        )
    finally:
        _write_status(status_file)

def process_payload(payload: dict, status_file: str | None = None):
    global processing_status

    try:
        data = payload or {}

        source_folder = (
            data.get("source_folder")
            or data.get("input_folder")
            or data.get("local_folder_path")
        )
        source_paths = data.get("source_paths")
        output_folder = data.get("output_folder")
        reports_folder = data.get("reports_folder")
        responses_folder = data.get("responses_folder")
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        event_type = data.get("event_type", "all")

        source_entries = _parse_source_entries(source_folder, source_paths)
        source_entries = _expand_creditline_sources(source_entries)
        if not source_entries:
            raise ValueError(
                "Источник не существует или в CreditLine не найдены папки "
                "EquifaxNew/UCBNew/UCHFNew с подпапками Reports и Receipts."
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
        _write_status(status_file)

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
        _write_status(status_file)
        raise


def main():
    parser = argparse.ArgumentParser(description="CheckReceipts Tauri backend")
    parser.add_argument("--payload-file", required=True, help="Путь к JSON с параметрами обработки")
    parser.add_argument("--status-file", required=True, help="Путь для записи статуса обработки")
    args = parser.parse_args()

    try:
        with open(args.payload_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        process_payload(payload, args.status_file)
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

