# Этот файл содержит базовую инфраструктуру: константы, кэши, утилиты и XML-кэш.
import math
import multiprocessing
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree as ET
try:
    import psutil
except Exception:
    psutil = None


XML_CACHE_MAX_SIZE = 2000
MAX_PROCESSES = min(6, max(1, (multiprocessing.cpu_count() or 2)))

DAILY_GROUP_LABEL = "ежедневные"
DAILY_GROUP_ALIASES = ("daily", "ежедневные", "ежедневный")

REPORTS_CANDIDATES = [
    "reports",
    "report",
    "отчеты",
    "отчёты",
    "отчет",
    "отчёт",
]

RESPONSES_CANDIDATES = [
    "responses",
    "response",
    "receipts",
    "receipt",
    "отбивки",
    "отбивка",
    "квитки",
    "квиток",
    "квитанции",
    "квитанция",
]

BKI_MAPPING = {"0XY_FCH": "ЭКС", "BD0": "НБКИ", "CHP": "ОКБ"}

BASE_ERROR_COLUMNS = [
    "FIO",
    "Doc",
    "Title",
    "AppId",
    "Uid",
    "OrderNum",
    "OrderNum_3_2",
    "EventName",
    "BlockName",
    "FieldName",
    "FieldValue",
    "ErrorCode",
    "ErrorMessage",
    "EventNum",
    "FileName",
    "FileDate",
    "IncomingDocNumber",
    "IncomingDocDate",
    "BKI",
]


MAIN_FILE_CACHE: Dict[int, Dict] = {}
TITLE_CACHE: Dict[str, str] = {}
ERROR_CACHE: Dict[str, List[Dict]] = {}
LAST_RUN_STATS: Dict[str, Any] = {}


def now_s() -> float:
    return time.perf_counter()


def fmt_dt(ts: float) -> str:
    return f"{ts:.3f}s"


def extract_date_from_filename(filename: str) -> str:
    if not filename:
        return ""
    m = re.search(r"(\d{8})", filename)
    if m:
        s = m.group(1)
        try:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        except Exception:
            return ""
    return ""


def get_size_kb(size_in_bytes: int) -> int:
    return math.ceil(size_in_bytes / 1024) if size_in_bytes and size_in_bytes > 0 else 0


def detect_file_type(path_or_name: str) -> Optional[str]:
    name = os.path.basename(path_or_name or "")
    upper_name = name.upper()
    if upper_name.startswith("0XY_FCH") or "0XY_FCH" in upper_name:
        return "0XY_FCH"
    if upper_name.startswith("BD0") or "BD0" in upper_name:
        return "BD0"
    if upper_name.startswith(("CHP", "CHT_")) or "CHP" in upper_name or "CHT_" in upper_name:
        return "CHP"
    return None


XML_CACHE_LOCK = threading.Lock()
XML_CACHE_HITS = 0
XML_CACHE_MISSES = 0
XML_CACHE_INVALIDATED = 0
XML_CACHE: "OrderedDict[str, Tuple[Tuple[int, int], ET._ElementTree]]" = OrderedDict()


def monitor_memory_usage_mb() -> float:
    if psutil is None:
        return 0.0
    try:
        proc = psutil.Process()
        return proc.memory_info().rss / 1024 / 1024
    except Exception:
        return 0.0


def get_xml_cache_stats() -> Dict[str, int]:
    with XML_CACHE_LOCK:
        return {
            "hits": int(XML_CACHE_HITS),
            "misses": int(XML_CACHE_MISSES),
            "invalidated": int(XML_CACHE_INVALIDATED),
            "size": len(XML_CACHE),
        }


def check_memory_limit(limit_mb: int = 1500, cleanup_ratio: float = 0.2) -> bool:
    mem = monitor_memory_usage_mb()
    if mem and mem > limit_mb:
        print(f"[MEM] {mem:.1f}MB > {limit_mb}MB - shrinking XML cache")
        with XML_CACHE_LOCK:
            cache_size = len(XML_CACHE)
            to_remove = max(1, int(cache_size * cleanup_ratio))
            for _ in range(to_remove):
                try:
                    XML_CACHE.popitem(last=False)
                except Exception:
                    break
        import gc

        gc.collect()
        return True
    return False


def _xml_signature(xml_path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(xml_path)
        return int(st.st_mtime_ns), int(st.st_size)
    except Exception:
        return None


def get_cached_xml(xml_path: str):
    global XML_CACHE_HITS, XML_CACHE_MISSES, XML_CACHE_INVALIDATED
    if not xml_path or not os.path.exists(xml_path):
        return None
    abs_path = os.path.abspath(xml_path)
    sig = _xml_signature(abs_path)
    if sig is None:
        return None

    with XML_CACHE_LOCK:
        cached = XML_CACHE.get(abs_path)
        if cached is not None:
            cached_sig, cached_tree = cached
            if cached_sig == sig:
                XML_CACHE_HITS += 1
                try:
                    XML_CACHE.move_to_end(abs_path, last=True)
                except Exception:
                    pass
                return cached_tree

            XML_CACHE_INVALIDATED += 1
            try:
                XML_CACHE.pop(abs_path, None)
            except Exception:
                pass
        XML_CACHE_MISSES += 1

    try:
        parser = ET.XMLParser(recover=True)
        tree = ET.parse(abs_path, parser=parser)
    except Exception as e:
        print(f"[XML PARSE ERROR] {abs_path}: {e}")
        return None

    with XML_CACHE_LOCK:
        XML_CACHE[abs_path] = (sig, tree)
        while len(XML_CACHE) > XML_CACHE_MAX_SIZE:
            try:
                XML_CACHE.popitem(last=False)
            except Exception:
                break
    return tree


def parse_xml_direct(xml_path: str):
    parser = ET.XMLParser(recover=True)
    return ET.parse(xml_path, parser=parser)
