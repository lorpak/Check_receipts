# Этот файл отвечает за работу с источниками: разбор путей, поиск папок и подготовку пар report/response.
import os
import re
import shutil
import tempfile

from orchestration.constants import KNOWN_BKI_FOLDERS, RESPONSE_DIR_CANDIDATES


def parse_source_entries(source_folder: str, source_paths):
    entries = []
    if isinstance(source_paths, list):
        entries.extend([str(p).strip() for p in source_paths if str(p).strip()])

    source_raw = (source_folder or "").strip()
    if source_raw:
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


def expand_creditline_sources(source_entries):
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


def resolve_creditline_reports_receipts(source_folder: str) -> tuple[str, str] | tuple[None, None]:
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


def is_supported_report_name(file_name: str) -> bool:
    name_u = (file_name or "").upper()
    return name_u.startswith(("0XY_FCH", "BD0", "CHP", "CHT_"))


def response_matches_report(report_name: str, response_name: str) -> bool:
    report_stem = os.path.splitext(report_name)[0]
    report_stem_u = report_stem.upper()
    response_u = response_name.upper()
    report_name_u = report_name.upper()

    if report_name_u.startswith("0XY_FCH"):
        return response_u.startswith(report_stem_u + ".XML.")
    if report_name_u.startswith("BD0"):
        return report_stem_u in response_u and "TICKET2" in response_u
    if report_name_u.startswith("CHP"):
        return report_stem_u in response_u and "T" in response_u
    if report_name_u.startswith("CHT_"):
        return response_u.startswith(report_stem_u) and response_u.endswith(".XML")
    return False


def find_matching_response_for_report(report_file: str, responses_folder: str = None) -> str:
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
                if response_matches_report(report_name, response_name):
                    return response_path
        except Exception:
            continue

    return ""


def prepare_single_file_workspace(source_file: str, responses_folder: str = None):
    source_file = os.path.abspath(source_file)
    report_name = os.path.basename(source_file)

    if not is_supported_report_name(report_name):
        raise ValueError(
            "Неподдерживаемое имя исходного файла. Ожидаются префиксы: 0XY_FCH, BD0, CHP, CHT_."
        )

    response_file = find_matching_response_for_report(source_file, responses_folder=responses_folder)
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
        report_stem = os.path.splitext(report_name)[0]
        m = re.match(rf"^{re.escape(report_stem)}\.xml(\..+)?$", response_name, flags=re.IGNORECASE)
        if m:
            suffix = m.group(1) or ".response"
            response_name = f"{report_stem}.XML{suffix}"
        else:
            response_name = f"{report_stem}.XML.{response_name}"

    shutil.copy2(response_file, os.path.join(responses_dir, response_name))
    return workspace_dir, reports_dir, responses_dir
