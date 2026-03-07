# Этот файл реализует сервисный уровень: сбор результатов, экспорт в Excel и основной main_with_date.
import os
import re
from typing import Dict, List, Optional, Tuple
import openpyxl
import pandas as pd

from .core import (
    BASE_ERROR_COLUMNS,
    DAILY_GROUP_ALIASES,
    ERROR_CACHE,
    LAST_RUN_STATS,
    MAIN_FILE_CACHE,
    TITLE_CACHE,
    extract_date_from_filename,
    fmt_dt,
    get_xml_cache_stats,
    now_s,
)
from .domain import _resolve_input_dirs, detect_group_type, find_file_pairs_between_dirs, process_pairs_parallel_optimized


def _add_client_info_ultrafast(errors: List[Dict], main_file: str) -> List[Dict]:
    return errors


def save_errors_to_excel(
    groups_by_date: Dict[str, List[Tuple[List[Dict], Dict]]], filename_suffix: str, output_dir: str = "."
) -> List[str]:
    created_files: List[str] = []
    all_errors: List[Dict] = []

    for _, date_groups in groups_by_date.items():
        for errors, group_info in date_groups:
            if errors:
                all_errors.extend(_add_client_info_ultrafast(errors, group_info.get("main_file", "")))

    if not all_errors:
        print(f"[SAVE] No errors to export ({filename_suffix}).")
        return []

    suffix_raw = (filename_suffix or "group").strip().lower()
    if suffix_raw in DAILY_GROUP_ALIASES:
        suffix_raw = "daily"
    suffix_safe = re.sub(r"[^A-Za-z0-9._-]+", "_", suffix_raw).strip("._-") or "group"
    excel_filename = f"errors_{suffix_safe}.xlsx"
    excel_path = os.path.join(output_dir or ".", excel_filename)
    try:
        t0 = now_s()
        os.makedirs(output_dir or ".", exist_ok=True)
        df = pd.DataFrame(all_errors)

        for col in BASE_ERROR_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        df = df.reindex(columns=BASE_ERROR_COLUMNS)
        df.to_excel(excel_path, index=False, engine="openpyxl")
        elapsed = now_s() - t0
        print(f"[SAVE] '{excel_path}' written in {fmt_dt(elapsed)} ({len(df)} rows)")
        created_files.append(excel_path)
    except Exception as e:
        print(f"[SAVE ERROR] {e}")
        try:
            os.makedirs(output_dir or ".", exist_ok=True)
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = suffix_safe[:31]
            ws.append(BASE_ERROR_COLUMNS)
            wb.save(excel_path)
            created_files.append(excel_path)
            print(f"[SAVE] Empty workbook created: '{excel_path}'")
        except Exception as e2:
            print(f"[SAVE FAIL] {e2}")
    return created_files


def main_with_date(
    local_folder_path: str,
    selected_dates: List[str],
    event_type: str = "all",
    progress_callback=None,
    output_folder: Optional[str] = None,
    reports_folder: Optional[str] = None,
    responses_folder: Optional[str] = None,
) -> List[str]:
    xml_cache_before = get_xml_cache_stats()
    LAST_RUN_STATS.clear()
    LAST_RUN_STATS.update(
        {
            "input_dir": local_folder_path,
            "reports_dir": "",
            "responses_dir": "",
            "pairs_found": 0,
            "pairs_after_date_filter": 0,
            "group_3_2_pairs": 0,
            "group_daily_pairs": 0,
            "event_type": event_type,
            "errors_found": 0,
            "saved_files": 0,
            "xml_cache_hits": 0,
            "xml_cache_misses": 0,
            "xml_cache_invalidated": 0,
            "xml_cache_size": 0,
            "note": "",
        }
    )
    MAIN_FILE_CACHE.clear()
    TITLE_CACHE.clear()
    ERROR_CACHE.clear()

    if progress_callback:
        progress_callback(5, "Scanning input files")

    output_dir = output_folder or local_folder_path
    os.makedirs(output_dir, exist_ok=True)

    reports_dir, responses_dir = _resolve_input_dirs(local_folder_path, reports_folder, responses_folder)
    LAST_RUN_STATS["reports_dir"] = reports_dir
    LAST_RUN_STATS["responses_dir"] = responses_dir
    print(f"[INPUT] Resolved dirs: reports='{reports_dir}', responses='{responses_dir}'")
    file_pairs = find_file_pairs_between_dirs(reports_dir, responses_dir)
    LAST_RUN_STATS["pairs_found"] = len(file_pairs)
    if not file_pairs:
        LAST_RUN_STATS["note"] = "pairs_not_found"
        raise ValueError("No report/response pairs found. Check file names and reports/responses folders.")

    selected_set = {d for d in (selected_dates or []) if d}
    if selected_set:
        filtered_pairs: List[Tuple[str, str]] = []
        for main_file, response_file in file_pairs:
            file_date = extract_date_from_filename(os.path.basename(main_file))
            if not file_date or file_date in selected_set:
                filtered_pairs.append((main_file, response_file))
        file_pairs = filtered_pairs
    LAST_RUN_STATS["pairs_after_date_filter"] = len(file_pairs)

    if not file_pairs:
        LAST_RUN_STATS["note"] = "pairs_filtered_by_date"
        raise ValueError("No pairs left after date filter.")

    daily_group_key = detect_group_type("__daily_group_key__")
    pairs_3_2: List[Tuple[str, str]] = []
    pairs_daily: List[Tuple[str, str]] = []
    for main_file, response_file in file_pairs:
        if detect_group_type(main_file) == "3-2":
            pairs_3_2.append((main_file, response_file))
        else:
            pairs_daily.append((main_file, response_file))
    LAST_RUN_STATS["group_3_2_pairs"] = len(pairs_3_2)
    LAST_RUN_STATS["group_daily_pairs"] = len(pairs_daily)
    print(f"[GROUPS] 3-2={len(pairs_3_2)}, daily={len(pairs_daily)}")

    normalized_event_type = (event_type or "all").lower().strip()
    groups_to_process: List[Tuple[str, List[Tuple[str, str]], int, int]] = []
    if normalized_event_type == "3-2":
        groups_to_process.append(("3-2", pairs_3_2, 15, 90))
    elif normalized_event_type in DAILY_GROUP_ALIASES:
        groups_to_process.append((daily_group_key, pairs_daily, 15, 90))
    else:
        if pairs_3_2:
            groups_to_process.append(("3-2", pairs_3_2, 10, 55))
        if pairs_daily:
            groups_to_process.append((daily_group_key, pairs_daily, 55, 90))

    if not groups_to_process:
        raise ValueError("No pairs available for selected event type.")

    created_files: List[str] = []
    total_errors_found = 0
    for group_label, pairs, start_progress, end_progress in groups_to_process:
        if not pairs:
            continue
        grouped_errors = process_pairs_parallel_optimized(
            pairs,
            group_label,
            progress_callback=progress_callback,
            start_progress=start_progress,
            end_progress=end_progress,
        )
        group_errors_count = sum(len(errors or []) for date_groups in grouped_errors.values() for errors, _ in date_groups)
        total_errors_found += group_errors_count
        print(f"[RESULT] group='{group_label}' pairs={len(pairs)} errors={group_errors_count}")
        created_files.extend(save_errors_to_excel(grouped_errors, group_label, output_dir=output_dir))

    LAST_RUN_STATS["errors_found"] = total_errors_found
    LAST_RUN_STATS["saved_files"] = len(created_files)
    xml_cache_after = get_xml_cache_stats()
    LAST_RUN_STATS["xml_cache_hits"] = max(0, xml_cache_after["hits"] - xml_cache_before["hits"])
    LAST_RUN_STATS["xml_cache_misses"] = max(0, xml_cache_after["misses"] - xml_cache_before["misses"])
    LAST_RUN_STATS["xml_cache_invalidated"] = max(0, xml_cache_after["invalidated"] - xml_cache_before["invalidated"])
    LAST_RUN_STATS["xml_cache_size"] = xml_cache_after["size"]
    if not created_files and total_errors_found == 0:
        LAST_RUN_STATS["note"] = "no_errors_in_receipts"
    elif not created_files:
        LAST_RUN_STATS["note"] = "save_failed_or_empty"
    else:
        LAST_RUN_STATS["note"] = "ok"
    print(
        f"[SUMMARY] pairs={LAST_RUN_STATS['pairs_found']} "
        f"filtered={LAST_RUN_STATS['pairs_after_date_filter']} "
        f"errors={total_errors_found} files={len(created_files)} "
        f"cache(h={LAST_RUN_STATS['xml_cache_hits']},m={LAST_RUN_STATS['xml_cache_misses']},"
        f"i={LAST_RUN_STATS['xml_cache_invalidated']},size={LAST_RUN_STATS['xml_cache_size']}) "
        f"note={LAST_RUN_STATS['note']}"
    )

    if progress_callback:
        progress_callback(100, f"Completed. Files created: {len(created_files)}")
    return created_files
