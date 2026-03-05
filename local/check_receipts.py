

import os
import math
import time
import re
import concurrent.futures
import threading
from collections import defaultdict, OrderedDict
from datetime import datetime
from typing import Dict, Optional, List, Tuple, Any
import multiprocessing

import pandas as pd
import openpyxl
from lxml import etree as ET

try:
    import psutil
except Exception:
    psutil = None

XML_CACHE_LOCK = threading.Lock()
XML_CACHE_MAX_SIZE = 2000
XML_CACHE_HITS = 0
XML_CACHE_MISSES = 0
XML_CACHE_INVALIDATED = 0
XML_CACHE: "OrderedDict[str, Tuple[Tuple[int, int], ET._ElementTree]]" = OrderedDict()

MAIN_FILE_CACHE: Dict[int, Dict] = {}
TITLE_CACHE: Dict[str, str] = {}
ERROR_CACHE: Dict[str, List[Dict]] = {}
LAST_RUN_STATS: Dict[str, Any] = {}
MAX_PROCESSES = min(6, max(1, (multiprocessing.cpu_count() or 2)))
def now_s() -> float:
    return time.perf_counter()

def fmt_dt(ts: float) -> str:
    return f"{ts:.3f}s"

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
    """Return parsed XML tree using process-local LRU cache."""
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
def extract_date_from_filename(filename: str) -> str:
    if not filename:
        return ""
    m = re.search(r'(\d{8})', filename)
    if m:
        s = m.group(1)
        try:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        except Exception:
            return ""
    return ""

def get_size_kb(size_in_bytes: int) -> int:
    return math.ceil(size_in_bytes / 1024) if size_in_bytes and size_in_bytes > 0 else 0
def find_file_pairs_between_dirs(reports_dir: str, responses_dir: str) -> List[Tuple[str, str]]:
    """Find (report, response) file pairs between two directories."""
    pairs: List[Tuple[str, str]] = []
    if not os.path.isdir(reports_dir) or not os.path.isdir(responses_dir):
        print(f"[WARN] Missing directory: reports='{reports_dir}', responses='{responses_dir}'")
        return pairs

    print(f"[SCAN] Start: reports='{reports_dir}', responses='{responses_dir}'")
    try:
        report_files = os.listdir(reports_dir)
        response_files = os.listdir(responses_dir)
    except Exception as e:
        print(f"[ERROR] Failed to read directories: {e}")
        return pairs
    print(f"[SCAN] Files in reports={len(report_files)}, responses={len(response_files)}")

    fch_prefix_index: Dict[str, str] = {}
    bd0_candidates: List[Tuple[str, str]] = []
    chp_candidates: List[Tuple[str, str]] = []
    cht_candidates: List[Tuple[str, str]] = []
    for response_name in response_files:
        response_u = response_name.upper()

        if ".XML." in response_u:
            fch_prefix = response_u.split(".XML.", 1)[0]
            if fch_prefix not in fch_prefix_index:
                fch_prefix_index[fch_prefix] = response_name
        if "TICKET2" in response_u:
            bd0_candidates.append((response_name, response_u))
        if "T" in response_u:
            chp_candidates.append((response_name, response_u))
        if response_u.endswith(".XML"):
            cht_candidates.append((response_name, response_u))

    for main_file in report_files:
        main_name_u = main_file.upper()
        if not (main_name_u.startswith('0XY_FCH') or main_name_u.startswith('BD0') or main_name_u.startswith('CHP') or main_name_u.startswith('CHT_')):
            continue
        if main_name_u.startswith('0XY_FCH') and '.XML.' in main_name_u:
            continue

        main_no_ext = os.path.splitext(main_file)[0]
        main_no_ext_u = main_no_ext.upper()
        found_response = None

        if main_name_u.startswith('0XY_FCH'):
            found_response = fch_prefix_index.get(main_no_ext_u)
        elif main_name_u.startswith('BD0'):
            for rf, rf_u in bd0_candidates:
                if main_no_ext_u in rf_u:
                    found_response = rf
                    break
        elif main_name_u.startswith('CHP'):
            for rf, rf_u in chp_candidates:
                if main_no_ext_u in rf_u:
                    found_response = rf
                    break
        elif main_name_u.startswith('CHT_'):
            for rf, rf_u in cht_candidates:
                if rf_u.startswith(main_no_ext_u) and rf_u.endswith('.XML'):
                    found_response = rf
                    break

        if found_response:
            pairs.append((os.path.join(reports_dir, main_file), os.path.join(responses_dir, found_response)))
            print(f"[PAIR] {main_file} -> {found_response}")
        else:
            print(f"[WARN] Response not found for {main_file} in {responses_dir}")

    print(f"[SCAN] Pairs found in {reports_dir}: {len(pairs)}")
    return pairs
class ErrorReceiptProcessor:
    ERROR_FIELDS = {
        'ErrorCode': {'xpath': 'errorCode'},
        'ErrorMessage': {'xpath': 'errorMessage'},
        'Uid': {'xpath': 'uid'},
        'OrderNum': {'xpath': 'orderNum'},
        'OrderNum_3_2': {'xpath': 'orderNum_3_2'},
        'EventName': {'xpath': 'eventName'},
        'BlockName': {'xpath': 'blockName'},
        'FieldName': {'xpath': 'fieldName'},
        'FieldValue': {'xpath': 'fieldValue'}
    }

    def __init__(self, main_files_mapping: Dict[str, str], group_type: str):
        self.main_files_mapping = main_files_mapping or {}
        self.group_type = group_type
        self.main_event_by_ordernum: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.app_id_mapping: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        self.ls_mapping: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.file_dates: Dict[str, str] = defaultdict(str)
        self.subject_info_mapping: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        self._load_cached_data()

    def _load_cached_data(self):
        cache_key = hash(tuple(sorted(self.main_files_mapping.items())))
        if cache_key in MAIN_FILE_CACHE:
            cached = MAIN_FILE_CACHE[cache_key]
            self.main_event_by_ordernum = cached.get('main_event_by_ordernum', defaultdict(dict))
            self.app_id_mapping = cached.get('app_id_mapping', defaultdict(dict))
            self.ls_mapping = cached.get('ls_mapping', defaultdict(dict))
            self.file_dates = cached.get('file_dates', defaultdict(str))
            self.subject_info_mapping = cached.get('subject_info_mapping', defaultdict(dict))
            return
        for file_type, file_path in self.main_files_mapping.items():
            if file_type in ['0XY_FCH', 'BD0', 'CHP'] and os.path.exists(file_path):
                self._index_single_file_fast(file_path, file_type)
        MAIN_FILE_CACHE[cache_key] = {
            'main_event_by_ordernum': self.main_event_by_ordernum,
            'app_id_mapping': self.app_id_mapping,
            'ls_mapping': self.ls_mapping,
            'file_dates': self.file_dates,
            'subject_info_mapping': self.subject_info_mapping
        }

    def _index_single_file_fast(self, xml_path: str, file_type: str):
        """Index a main XML file and cache mappings for fast error extraction."""
        try:
            tree = get_cached_xml(xml_path)
            if tree is None:
                return
            root = tree.getroot()

            fl_packet = root.find('FL_PACKET')
            if fl_packet is not None:
                self.file_dates[file_type] = fl_packet.get('dateDoc', '') or self.file_dates.get(file_type, '')
            for subject_fl in root.iterfind('.//Subject_FL'):
                subject_uid, subject_ls, subject_appid = '', '', ''
                subject_title = ''
                all_order_nums = set()
                client_info = self._extract_subject_title_info_fast(subject_fl)
                for elem in subject_fl.iter():
                    order_num = elem.get('orderNum')
                    if order_num:
                        all_order_nums.add(order_num)

                    if elem.tag.startswith('FL_Event_'):
                        uid, ls, app_info = self._extract_event_data_fast(elem)
                        if uid and not subject_uid:
                            subject_uid = uid
                        if ls and not subject_ls:
                            subject_ls = ls
                        if app_info.get('main_app_id') and not subject_appid:
                            subject_appid = app_info.get('main_app_id')
                        if app_info.get('title') and not subject_title:
                            subject_title = app_info.get('title')
                if self.group_type == '3-2':
                    main_3_2_event = subject_fl.find('.//FL_Event_3_2')
                    if main_3_2_event is not None:
                        for child_event in main_3_2_event:
                            if child_event.tag.startswith('FL_Event_'):
                                child_order_num = child_event.get('orderNum')
                                if child_order_num:
                                    _, _, child_app_info = self._extract_event_data_fast(child_event)
                                    child_appid = child_app_info.get('main_app_id')
                                    child_title = child_app_info.get('title', '')
                                    if child_appid:
                                        self.app_id_mapping[file_type][child_order_num] = {
                                            'main_app_id': child_appid,
                                            'ls_number': child_app_info.get('ls_number', ''),
                                            'title': child_title,
                                            'child_events': {}
                                        }
                if all_order_nums:
                    final_app_info = {'main_app_id': subject_appid, 'ls_number': subject_ls, 'title': subject_title, 'child_events': {}}
                    for order_num in all_order_nums:
                        if subject_uid:
                            self.main_event_by_ordernum[file_type][order_num] = subject_uid
                        if subject_ls:
                            self.ls_mapping[file_type][order_num] = subject_ls
                        if order_num not in self.app_id_mapping[file_type]:
                            self.app_id_mapping[file_type][order_num] = final_app_info.copy()
                        info = client_info.copy()
                        if subject_title:
                            info['title'] = subject_title
                        self.subject_info_mapping[file_type][order_num] = info

        except Exception as e:
            print(f"[INDEX ERROR] {xml_path}: {e}")

    def _extract_subject_title_info_fast(self, subject_fl_elem) -> Dict[str, str]:
        result = {'fio': '', 'doc_info': ''}
        title_elem = subject_fl_elem.find('Title')
        if title_elem is None:
            return result
        name_elem = title_elem.find('.//FL_1_Name')
        if name_elem is not None:
            last = name_elem.findtext('lastName', '').strip()
            first = name_elem.findtext('firstName', '').strip()
            middle = name_elem.findtext('middleName', '').strip()
            result['fio'] = f"{last} {first} {middle}".strip()
        doc_elem = title_elem.find('.//FL_4_Doc')
        if doc_elem is not None:
            series = doc_elem.findtext('docSeries', '').strip()
            num = doc_elem.findtext('docNum', '').strip()
            result['doc_info'] = f"{series} {num}".strip() if series else num
        return result

    def _extract_event_data_fast(self, event_elem):
        """Return (deal_uid, ls_or_title, app_info) extracted from an event node."""
        deal_uid = ''
        ls_number = ''
        title = ''
        app_info = {'main_app_id': '', 'ls_number': '', 'title': ''}

        try:
            fl_17 = event_elem.find('.//FL_17_DealUid')
            fl_55 = event_elem.find('.//FL_55_Application')

            if fl_17 is not None:
                uid17 = (fl_17.findtext('uid') or '').strip()
                if uid17:
                    deal_uid = uid17
            if not deal_uid and fl_55 is not None:
                uid55 = (fl_55.findtext('uid') or '').strip()
                if uid55:
                    deal_uid = uid55
            if fl_55 is not None:
                num55 = (fl_55.findtext('num') or '').strip()
                if num55:
                    app_info['main_app_id'] = num55
            if fl_17 is not None:
                num17 = (fl_17.findtext('num') or '').strip()
                if num17:
                    if '/' in num17:
                        left, right = [p.strip() for p in num17.split('/', 1)]
                        if left:
                            title = left
                        if right and not app_info['main_app_id']:
                            app_info['main_app_id'] = right
                        if left and left.replace(' ', '').isdigit():
                            ls_number = left
                    else:
                        if app_info['main_app_id']:
                            title = num17
                        else:
                            if num17.isdigit() and len(num17) >= 4:
                                app_info['main_app_id'] = num17
                            else:
                                title = num17

            if not ls_number and title and title.replace(' ', '').isdigit():
                ls_number = title

            app_info['ls_number'] = ls_number
            app_info['title'] = title
        except Exception:
            pass

        return deal_uid, (ls_number or title), app_info

    def _extract_errors_actual(self, xml_path: str) -> List[Dict]:
        """
        Извлечение ошибок из отбивки с устойчивым чтением тегов (включая XML с namespace).
        """
        try:
            tree = get_cached_xml(xml_path)
            if tree is None:
                return []
            root = tree.getroot()
            filename = os.path.basename(xml_path)

            file_type = None
            uname = filename.upper()
            if '0XY_FCH' in uname:
                file_type = '0XY_FCH'
            elif 'BD0' in uname:
                file_type = 'BD0'
            elif 'CHP' in uname or 'CHT_' in uname:
                file_type = 'CHP'

            bki_mapping = {'0XY_FCH': 'ЭКС', 'BD0': 'НБКИ', 'CHP': 'ОКБ'}
            main_filename = os.path.basename(self.main_files_mapping.get(file_type, '') or '')
            file_date = self.file_dates.get(file_type, '') or extract_date_from_filename(os.path.basename(main_filename) or filename)

            def read_text(node, tag_name: str) -> str:
                value = (node.findtext(tag_name) or '').strip()
                if value:
                    return value
                try:
                    found = node.xpath("./*[local-name()='" + tag_name + "']/text()")
                    if found:
                        return (found[0] or '').strip()
                except Exception:
                    pass
                return ''

            incoming_doc_date = (root.findtext('.//incomingDocDate') or '').strip()
            if not incoming_doc_date:
                try:
                    found = root.xpath(".//*[local-name()='incomingDocDate']/text()")
                    if found:
                        incoming_doc_date = (found[0] or '').strip()
                except Exception:
                    pass

            common_data = {
                'FileName': main_filename,
                'FileDate': file_date,
                'IncomingDocNumber': filename,
                'IncomingDocDate': incoming_doc_date,
                'BKI': bki_mapping.get(file_type, '')
            }

            error_nodes = list(root.iterfind('.//Error'))
            if not error_nodes:
                try:
                    error_nodes = list(root.xpath(".//*[local-name()='Error']"))
                except Exception:
                    error_nodes = []

            errors: List[Dict] = []
            for error in error_nodes:
                error_message = read_text(error, 'errorMessage')

                orderNum = read_text(error, 'orderNum')
                if not orderNum:
                    orderNum = (error.get('orderNum') or '').strip()
                if not orderNum:
                    orderNum = read_text(error, 'OrderNum')

                orderNum_3_2 = ''
                if self.group_type == '3-2':
                    orderNum_3_2 = read_text(error, 'orderNum_3_2')
                    if not orderNum_3_2:
                        orderNum_3_2 = (error.get('orderNum_3_2') or '').strip()
                    if not orderNum_3_2:
                        orderNum_3_2 = read_text(error, 'orderNum3_2') or read_text(error, 'orderNum3.2')

                uid = self.main_event_by_ordernum.get(file_type, {}).get(orderNum, '')
                ls_number = self.ls_mapping.get(file_type, {}).get(orderNum, '')
                subject_info = self.subject_info_mapping.get(file_type, {}).get(orderNum, {}) or {}
                fio = subject_info.get('fio', '') or ''
                doc = subject_info.get('doc_info', '') or ''

                appid = ''
                event_info = self.app_id_mapping.get(file_type, {}).get(orderNum)
                if event_info:
                    appid = event_info.get('main_app_id', '') or ''
                if not appid and self.group_type == '3-2' and orderNum_3_2:
                    child_info = self.app_id_mapping.get(file_type, {}).get(orderNum_3_2)
                    appid = (child_info.get('main_app_id') if child_info else '') or ''

                title_val = ''
                if event_info and isinstance(event_info, dict):
                    title_val = event_info.get('title', '') or ''
                if not title_val:
                    title_val = subject_info.get('title', '') or ''
                if not title_val:
                    title_val = ls_number or ''

                err_row = {
                    'FIO': fio,
                    'Doc': doc,
                    'Title': title_val,
                    'AppId': appid,
                    'Uid': uid,
                    'OrderNum': orderNum,
                    'OrderNum_3_2': orderNum_3_2 if self.group_type == '3-2' else '',
                    'EventName': read_text(error, 'eventName'),
                    'BlockName': read_text(error, 'blockName'),
                    'FieldName': read_text(error, 'fieldName'),
                    'FieldValue': read_text(error, 'fieldValue'),
                    'ErrorCode': read_text(error, 'errorCode'),
                    'ErrorMessage': error_message,
                    'EventNum': read_text(error, 'eventNum')
                }
                err_row.update(common_data)
                errors.append(err_row)
            return errors
        except Exception as e:
            print(f"[EXTRACT ERR] {xml_path}: {e}")
            return []
    def extract_error_data_fast(self, xml_path: str) -> List[Dict]:
        """Compatibility alias for error extraction."""
        return self._extract_errors_actual(xml_path)



def worker_process_main(args):
    """ProcessPool worker: index one main file and process its response files."""
    main_file, response_files, group_type = args
    t0 = now_s()
    profiling = {'index_time': 0.0, 'total_resp_time': 0.0, 'responses': 0, 'errors': 0}
    results = []

    main_filename = os.path.basename(main_file)
    if main_filename.startswith('0XY_FCH'):
        file_type = '0XY_FCH'
    elif main_filename.startswith('BD0'):
        file_type = 'BD0'
    elif main_filename.startswith(('CHP', 'CHT_')):
        file_type = 'CHP'
    else:
        file_type = None
    t_idx_start = now_s()
    try:
        mapping = {file_type: main_file} if file_type else {}
        proc = ErrorReceiptProcessor(mapping, group_type)
    except Exception as e:
        print(f"[WORKER ERROR] Failed to build processor for {main_file}: {e}")
        proc = ErrorReceiptProcessor({}, group_type)
    t_idx_end = now_s()
    profiling['index_time'] = t_idx_end - t_idx_start
    for resp in response_files:
        t_r0 = now_s()
        profiling['responses'] += 1
        try:
            errors = proc.extract_error_data_fast(resp)
            t_r1 = now_s()
            profiling['total_resp_time'] += (t_r1 - t_r0)
            if errors:
                profiling['errors'] += len(errors)
                file_date = proc.file_dates.get(file_type, '') or extract_date_from_filename(os.path.basename(main_file)) or datetime.now().strftime('%Y-%m-%d')
                results.append({
                    'file_date': file_date,
                    'errors': errors,
                    'response_file': resp,
                    'main_file': main_file
                })
        except Exception as e:
            t_r1 = now_s()
            profiling['total_resp_time'] += (t_r1 - t_r0)
            print(f"[WORKER TASK ERROR] {resp}: {e}")

    total_t = now_s() - t0
    profile_summary = {
        'index_time': profiling['index_time'],
        'total_resp_time': profiling['total_resp_time'],
        'responses': profiling['responses'],
        'errors': profiling['errors'],
        'total_time': total_t
    }
    return {'main_file': main_file, 'file_type': file_type, 'profile': profile_summary, 'results': results}
def process_pairs_processpool(file_pairs: List[Tuple[str, str]], group_type: str, max_workers: int = MAX_PROCESSES):
    """Process grouped pairs with ProcessPool and return results grouped by date."""
    print(f"[PROCESSPOOL] Starting mode with max_workers={max_workers} for {len(file_pairs)} pairs ({group_type})")
    per_main: Dict[str, List[str]] = {}
    for main_file, resp in file_pairs:
        per_main.setdefault(main_file, []).append(resp)

    tasks = []
    for main, responses in per_main.items():
        tasks.append((main, responses, group_type))

    results_by_date: Dict[str, List[Tuple[List[Dict], Dict]]] = defaultdict(list)
    profiles = []
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(worker_process_main, task): task[0] for task in tasks}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    r = fut.result()
                except Exception as e:
                    print(f"[PROCESSPOOL FUTURE ERR] {e}")
                    continue
                profiles.append({'main_file': r.get('main_file'), 'profile': r.get('profile', {})})
                for item in r.get('results', []):
                    fd = item.get('file_date') or extract_date_from_filename(os.path.basename(item.get('main_file', '')))
                    errors = item.get('errors', [])
                    meta = {'main_file': item.get('main_file'), 'response_file': item.get('response_file')}
                    results_by_date[fd].append((errors, meta))
    except Exception as e:
        print(f"[PROCESSPOOL LAUNCH ERROR] {e}")
        print("[FALLBACK] Switching to ThreadPoolExecutor fallback.")
        return process_pairs_parallel_optimized(file_pairs, group_type)
    total_index = sum(p['profile'].get('index_time', 0.0) for p in profiles)
    total_resp = sum(p['profile'].get('total_resp_time', 0.0) for p in profiles)
    total_errors = sum(p['profile'].get('errors', 0) for p in profiles)
    total_responses = sum(p['profile'].get('responses', 0) for p in profiles)
    print(f"[PROFILE SUMMARY] main_files={len(profiles)}, responses={total_responses}, errors={total_errors}")
    print(f"  total_index_time={fmt_dt(total_index)}, total_resp_time={fmt_dt(total_resp)}, avg_resp_time={(total_resp/total_responses) if total_responses else 0:.3f}s")
    return results_by_date
def process_pairs_parallel_optimized(file_pairs: List[Tuple[str, str]], group_type: str,
                                     progress_callback=None, start_progress=0, end_progress=100) -> Dict[str, List[Tuple[List[Dict], Dict]]]:
    print(f"[OPT PROC] ThreadPool: processing {len(file_pairs)} pairs for '{group_type}'")
    if progress_callback:
        progress_callback(start_progress, f"Processing {group_type}")

    per_main: Dict[str, List[str]] = {}
    for main_file, response_file in file_pairs:
        per_main.setdefault(main_file, []).append(response_file)

    processors: Dict[str, ErrorReceiptProcessor] = {}
    for main_file in per_main.keys():
        main_filename = os.path.basename(main_file)
        if main_filename.startswith('0XY_FCH'):
            file_type = '0XY_FCH'
        elif main_filename.startswith('BD0'):
            file_type = 'BD0'
        elif main_filename.startswith(('CHP', 'CHT_')):
            file_type = 'CHP'
        else:
            file_type = None
        processors[main_file] = ErrorReceiptProcessor({file_type: main_file} if file_type else {}, group_type)

    results_by_date = defaultdict(list)
    max_workers = min(16, max(2, (os.cpu_count() or 2) * 2))
    total_tasks = sum(len(v) for v in per_main.values()) or 1
    completed = 0

    def _process_single(main_file: str, response_file: str):
        nonlocal completed
        proc = processors.get(main_file)
        res = []
        try:
            errors = proc.extract_error_data_fast(response_file)
            if errors:
                main_filename = os.path.basename(main_file)
                file_type = None
                if main_filename.startswith('0XY_FCH'):
                    file_type = '0XY_FCH'
                elif main_filename.startswith('BD0'):
                    file_type = 'BD0'
                elif main_filename.startswith(('CHP', 'CHT_')):
                    file_type = 'CHP'
                file_date = proc.file_dates.get(file_type, '') or extract_date_from_filename(os.path.basename(main_file)) or datetime.now().strftime('%Y-%m-%d')
                res = (file_date, (errors, {'main_file': main_file, 'response_file': response_file}))
        except Exception as e:
            print(f"[TASK ERR] {os.path.basename(response_file)}: {e}")
        finally:
            completed += 1
            if progress_callback:
                progress = start_progress + int((end_progress - start_progress) * (completed / total_tasks))
                progress_callback(progress, f"Processed {completed}/{total_tasks}")
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = []
        for main_file, responses in per_main.items():
            for resp in responses:
                futures.append(ex.submit(_process_single, main_file, resp))
        for fut in concurrent.futures.as_completed(futures):
            try:
                r = fut.result()
                if r:
                    date, val = r
                    results_by_date[date].append(val)
            except Exception as e:
                print(f"[FUTURE ERR] {e}")

    check_memory_limit()
    return results_by_date

def _add_client_info_ultrafast(errors: List[Dict], main_file: str) -> List[Dict]:
    return errors

def save_errors_to_excel(groups_by_date: Dict[str, List[Tuple[List[Dict], Dict]]], filename_suffix: str,
                         output_dir: str = ".") -> List[str]:
    created_files: List[str] = []
    all_errors: List[Dict] = []

    for file_date, date_groups in groups_by_date.items():
        for errors, group_info in date_groups:
            if errors:
                all_errors.extend(_add_client_info_ultrafast(errors, group_info.get('main_file', '')))

    if not all_errors:
        print(f"[SAVE] No errors to export ({filename_suffix}).")
        return []

    suffix_raw = (filename_suffix or "group").strip().lower()
    if suffix_raw in ("daily", "ежеднев", "ежедневные"):
        suffix_raw = "daily"
    suffix_safe = re.sub(r"[^A-Za-z0-9._-]+", "_", suffix_raw).strip("._-") or "group"
    excel_filename = f"errors_{suffix_safe}.xlsx"
    excel_path = os.path.join(output_dir or ".", excel_filename)
    try:
        t0 = now_s()
        os.makedirs(output_dir or ".", exist_ok=True)
        df = pd.DataFrame(all_errors)

        base_columns = ['FIO', 'Doc', 'Title', 'AppId', 'Uid', 'OrderNum',
                        'OrderNum_3_2', 'EventName', 'BlockName', 'FieldName',
                        'FieldValue', 'ErrorCode', 'ErrorMessage', 'EventNum',
                        'FileName', 'FileDate', 'IncomingDocNumber', 'IncomingDocDate', 'BKI']

        for col in base_columns:
            if col not in df.columns:
                df[col] = ''

        df = df.reindex(columns=base_columns)
        df.to_excel(excel_path, index=False, engine='openpyxl')
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
            ws.append(base_columns)
            wb.save(excel_path)
            created_files.append(excel_path)
            print(f"[SAVE] Empty workbook created: '{excel_path}'")
        except Exception as e2:
            print(f"[SAVE FAIL] {e2}")
    return created_files
def _find_existing_subdir(base_dir: str, candidates: List[str]) -> Optional[str]:
    if not base_dir or not os.path.isdir(base_dir):
        return None

    candidate_set = {c.lower() for c in candidates}
    try:
        entries = [e for e in os.listdir(base_dir)]
    except Exception:
        return None
    for name in entries:
        full_path = os.path.join(base_dir, name)
        if os.path.isdir(full_path) and name.lower() in candidate_set:
            return full_path
    for name in entries:
        full_path = os.path.join(base_dir, name)
        if not os.path.isdir(full_path):
            continue
        low_name = name.lower()
        for token in candidate_set:
            if token and token in low_name:
                return full_path
    return None


def _resolve_input_dirs(local_folder_path: str, reports_folder: Optional[str], responses_folder: Optional[str]) -> Tuple[str, str]:
    if reports_folder or responses_folder:
        if not reports_folder or not responses_folder:
            raise ValueError("For split mode provide both folders: reports_folder and responses_folder.")
        if not os.path.isdir(reports_folder):
            raise ValueError(f"Reports folder does not exist: {reports_folder}")
        if not os.path.isdir(responses_folder):
            raise ValueError(f"Responses folder does not exist: {responses_folder}")
        return reports_folder, responses_folder

    if not local_folder_path or not os.path.isdir(local_folder_path):
        raise ValueError(f"Input folder does not exist: {local_folder_path}")

    reports_candidates = [
        "reports",
        "report",
        "\u043e\u0442\u0447\u0435\u0442\u044b",
        "\u043e\u0442\u0447\u0451\u0442\u044b",
        "\u043e\u0442\u0447\u0435\u0442",
        "\u043e\u0442\u0447\u0451\u0442",
    ]
    responses_candidates = [
        "responses",
        "response",
        "receipts",
        "receipt",
        "\u043e\u0442\u0431\u0438\u0432\u043a\u0438",
        "\u043e\u0442\u0431\u0438\u0432\u043a\u0430",
        "\u043a\u0432\u0438\u0442\u043a\u0438",
        "\u043a\u0432\u0438\u0442\u043e\u043a",
        "\u043a\u0432\u0438\u0442\u0430\u043d\u0446\u0438\u0438",
        "\u043a\u0432\u0438\u0442\u0430\u043d\u0446\u0438\u044f",
    ]

    def _name_has_any_token(path: str, tokens: List[str]) -> bool:
        name = os.path.basename(os.path.normpath(path)).lower()
        return any(token and token in name for token in tokens)

    auto_reports = _find_existing_subdir(local_folder_path, reports_candidates)
    auto_responses = _find_existing_subdir(local_folder_path, responses_candidates)
    if auto_reports and auto_responses:
        return auto_reports, auto_responses
    parent_dir = os.path.dirname(local_folder_path)
    if parent_dir and os.path.isdir(parent_dir):
        parent_reports = _find_existing_subdir(parent_dir, reports_candidates)
        parent_responses = _find_existing_subdir(parent_dir, responses_candidates)

        if _name_has_any_token(local_folder_path, reports_candidates):
            return local_folder_path, (parent_responses or local_folder_path)
        if _name_has_any_token(local_folder_path, responses_candidates):
            return (parent_reports or local_folder_path), local_folder_path
        if parent_reports and parent_responses:
            return parent_reports, parent_responses

    return local_folder_path, local_folder_path


def main_with_date(local_folder_path: str, selected_dates: List[str], event_type: str = "all",
                   progress_callback=None, output_folder: Optional[str] = None,
                   reports_folder: Optional[str] = None, responses_folder: Optional[str] = None) -> List[str]:
    global MAIN_FILE_CACHE, TITLE_CACHE, ERROR_CACHE, LAST_RUN_STATS
    xml_cache_before = get_xml_cache_stats()
    LAST_RUN_STATS = {
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
        raise ValueError(
            "No report/response pairs found. Check file names and reports/responses folders."
        )

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
    elif normalized_event_type in ("daily", "ежеднев", "ежедневные"):
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
            end_progress=end_progress
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
def detect_group_type(main_fch_path: str) -> str:
    if not os.path.exists(main_fch_path):
        return 'ежеднев'
    try:
        tree = get_cached_xml(main_fch_path)
        if tree is None:
            return 'ежеднев'
        root = tree.getroot()
        if root.find('.//FL_Event_3_2') is not None:
            return '3-2'
        try:
            if root.xpath('.//*[local-name()="FL_Event_3_2"]'):
                return '3-2'
        except Exception:
            pass
    except Exception as e:
        print(f"[WARN] detect_group_type: {e}")
    return 'ежеднев'







