# Этот файл запускает основной процесс обработки оркестрации, включая последовательную и параллельную обработку источников.
import concurrent.futures
import os
import shutil

from orchestration.sources import prepare_single_file_workspace, resolve_creditline_reports_receipts
from orchestration.state import open_result_files, processing_status, resolve_existing_result_paths, write_status


def cleanup_caches():
    try:
        from check_receipts import ERROR_CACHE, MAIN_FILE_CACHE, TITLE_CACHE

        MAIN_FILE_CACHE.clear()
        TITLE_CACHE.clear()
        ERROR_CACHE.clear()
    except Exception:
        pass


def run_single_source_task(
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

        cl_reports, cl_receipts = resolve_creditline_reports_receipts(source_abs)
        if cl_reports and cl_receipts and not os.path.isfile(source_abs):
            process_reports = cl_reports
            process_responses = cl_receipts

        if os.path.isfile(source_abs):
            temp_workspace, process_reports, process_responses = prepare_single_file_workspace(
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
    write_status(status_file)

    try:
        import check_receipts
        from check_receipts import main_with_date

        sources = source_entries if isinstance(source_entries, list) else [source_entries]
        sources = [os.path.abspath(s) for s in sources if s]
        total_sources = max(1, len(sources))

        all_result_files = []
        source_errors = []
        source_diagnostics = []

        if total_sources > 1:
            max_parallel_sources = min(total_sources, max(1, os.cpu_count() or 2), 3)
            processing_status["message"] = (
                f"Параллельная обработка источников: {total_sources} (workers={max_parallel_sources})"
            )
            processing_status["progress"] = 0
            write_status(status_file)

            futures_map = {}
            with concurrent.futures.ProcessPoolExecutor(max_workers=max_parallel_sources) as ex:
                for source_folder in sources:
                    fut = ex.submit(
                        run_single_source_task,
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
                    write_status(status_file)
        else:
            for idx, source_folder in enumerate(sources):
                process_source = source_folder
                process_reports = reports_folder
                process_responses = responses_folder
                process_dates = selected_dates
                temp_workspace = None

                cl_reports, cl_receipts = resolve_creditline_reports_receipts(source_folder)
                if cl_reports and cl_receipts and not os.path.isfile(source_folder):
                    process_reports = cl_reports
                    process_responses = cl_receipts

                def update_progress(progress, message, current_idx=idx):
                    local_progress = max(0, min(100, int(progress)))
                    overall = int(((current_idx + (local_progress / 100.0)) / total_sources) * 100)
                    processing_status["progress"] = overall
                    processing_status["message"] = f"[{current_idx + 1}/{total_sources}] {message}"
                    write_status(status_file)

                try:
                    if os.path.isfile(source_folder):
                        update_progress(3, "Подготовка выбранного файла и поиск отбивки...")
                        temp_workspace, process_reports, process_responses = prepare_single_file_workspace(
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
        resolved_result_paths = resolve_existing_result_paths(all_result_files, output_folder)
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
        write_status(status_file)
