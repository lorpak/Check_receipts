# ch_r3.py — оптимизированная версия с ProcessPoolExecutor и локальным запуском
# Изменения:
# - LOCAL_FOLDERS: локальные папки для НБКИ/ОКБ/ЭКС
# - process_pairs_processpool + worker_process_main для агрессивного CPU-bound режима
# - профилирование (время индексирования, время обработки, статы)
# - локальный запуск без app.py, обрабатывает все 3 папки и сохраняет Excel

import os
import sys
import math
import time
import re
import tempfile
import concurrent.futures
import threading
from collections import defaultdict, OrderedDict
from datetime import datetime
from typing import Dict, Optional, List, Tuple, Any
import multiprocessing

import pandas as pd
import openpyxl
from lxml import etree as ET

# try импорт psutil — если нет, монитор памяти будет неактивен
try:
    import psutil
except Exception:
    psutil = None

# ------------------ НАСТРОЙКИ ------------------
# Поменяйте пути на свои, если нужно
LOCAL_FOLDERS = {
    'НБКИ': {
        'reports': r"C:\Users\Я\Desktop\отчеты\20250804\Новая папка\Новая папка\нбки отч",
        'responses': r"C:\Users\Я\Desktop\отчеты\20250804\Новая папка\Новая папка\ньки отб"
    },
    'ОКБ': {
        'reports': r"C:\Users\Я\Desktop\отчеты\20250804\Новая папка\Новая папка\окб отч",
        'responses': r"C:\Users\Я\Desktop\отчеты\20250804\Новая папка\Новая папка\окб отб"
    },
    'ЭКС': {
        'reports': r"C:\Users\Я\Desktop\отчеты\20250804\Новая папка\Новая папка\экс отч",
        'responses': r"C:\Users\Я\Desktop\отчеты\20250804\Новая папка\Новая папка\экс отб"
    }
}

# LRU XML cache size (каждый процесс имеет свой кэш)
XML_CACHE_MAX_SIZE = 2000  # уменьшил по умолчанию для экономии памяти
XML_CACHE_LOCK = threading.Lock()
XML_CACHE: "OrderedDict[str, ET._ElementTree]" = OrderedDict()

# кэши процесса-родителя (не разделяются с подпроцессами)
MAIN_FILE_CACHE: Dict[int, Dict] = {}
TITLE_CACHE: Dict[str, str] = {}
ERROR_CACHE: Dict[str, List[Dict]] = {}

# ограничение числа процессов (по умолчанию — CPU cores)
MAX_PROCESSES = min(6, max(1, (multiprocessing.cpu_count() or 2)))

# ------------------ УТИЛИТЫ ------------------
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

def check_memory_limit(limit_mb: int = 1500) -> bool:
    mem = monitor_memory_usage_mb()
    if mem and mem > limit_mb:
        print(f"[MEM] {mem:.1f}MB > {limit_mb}MB — очищаем старые элементы кэша")
        with XML_CACHE_LOCK:
            to_remove = max(1, len(XML_CACHE) // 2)
            for _ in range(to_remove):
                try:
                    XML_CACHE.popitem(last=False)
                except Exception:
                    break
        import gc
        gc.collect()
        return True
    return False

# ------------------ LRU-кеш XML (потокобезопасный, локальный для процесса) ------------------
def get_cached_xml(xml_path: str):
    """Возвращает парсенное дерево XML, используя LRU-кеш в текущем процессе."""
    if not xml_path or not os.path.exists(xml_path):
        return None
    with XML_CACHE_LOCK:
        tree = XML_CACHE.get(xml_path)
        if tree is not None:
            try:
                XML_CACHE.move_to_end(xml_path, last=True)
            except Exception:
                pass
            return tree
    try:
        parser = ET.XMLParser(recover=True)
        tree = ET.parse(xml_path, parser=parser)
    except Exception as e:
        print(f"[XML PARSE ERROR] {xml_path}: {e}")
        return None
    with XML_CACHE_LOCK:
        XML_CACHE[xml_path] = tree
        while len(XML_CACHE) > XML_CACHE_MAX_SIZE:
            try:
                XML_CACHE.popitem(last=False)
            except Exception:
                break
    return tree

# Быстрая парс-обёртка
def parse_xml_direct(xml_path: str):
    parser = ET.XMLParser(recover=True)
    return ET.parse(xml_path, parser=parser)

# ------------------ Вспомогательные ------------------
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

# ------------------ Поиск пар между отдельными папками reports / responses ------------------
def find_file_pairs_between_dirs(reports_dir: str, responses_dir: str) -> List[Tuple[str, str]]:
    """
    Находит пары (main_report, response_file) между двумя каталогами.
    Логика сопоставления аналогична предыдущей (по префиксам и шаблонам файлов).
    """
    pairs: List[Tuple[str, str]] = []
    if not os.path.isdir(reports_dir) or not os.path.isdir(responses_dir):
        print(f"[WARN] Один из путей не существует: reports='{reports_dir}', responses='{responses_dir}'")
        return pairs

    try:
        report_files = os.listdir(reports_dir)
        response_files = os.listdir(responses_dir)
    except Exception as e:
        print(f"[ERROR] Не удалось прочитать директории: {e}")
        return pairs

    # индекс response по имени для быстрого поиска
    response_set = set(response_files)

    for main_file in report_files:
        if not (main_file.startswith('0XY_FCH') or main_file.startswith('BD0') or main_file.startswith('CHP') or main_file.startswith('CHT_')):
            continue
        main_no_ext = os.path.splitext(main_file)[0]

        found_response = None
        # 0XY_FCH -> ищем main_no_ext + '.XML.' префикс у response
        if main_file.startswith('0XY_FCH'):
            for rf in response_files:
                if rf.startswith(main_no_ext + '.XML.'):
                    found_response = rf
                    break
        elif main_file.startswith('BD0'):
            for rf in response_files:
                if main_no_ext in rf and 'ticket2' in rf.lower():
                    found_response = rf
                    break
        elif main_file.startswith('CHP'):
            for rf in response_files:
                if main_no_ext in rf and 'T' in rf:
                    found_response = rf
                    break
        elif main_file.startswith('CHT_'):
            for rf in response_files:
                if rf.startswith(main_no_ext) and rf.lower().endswith('.xml'):
                    found_response = rf
                    break

        if found_response:
            pairs.append((os.path.join(reports_dir, main_file), os.path.join(responses_dir, found_response)))
            print(f"[PAIR] {main_file} -> {found_response}")
        else:
            print(f"[WARN] Не найдена отбивка для {main_file} в {responses_dir}")

    print(f"[SCAN] Найдено пар в {reports_dir}: {len(pairs)}")
    return pairs

# ------------------ ErrorReceiptProcessor (как раньше, используется и подпроцессами) ------------------
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
        """
        Индексация основного файла — теперь сохраняем также 'title' (если есть) в app_id_mapping
        и в subject_info_mapping, чтобы потом правильно подставлять Title при сохранении ошибок.
        """
        try:
            tree = get_cached_xml(xml_path)
            if tree is None:
                return
            root = tree.getroot()

            fl_packet = root.find('FL_PACKET')
            if fl_packet is not None:
                # пытаемся сохранить дату документа для этого типа файла
                self.file_dates[file_type] = fl_packet.get('dateDoc', '') or self.file_dates.get(file_type, '')

            # Проход по Subject_FL (субъектно-ориентированная индексация)
            for subject_fl in root.iterfind('.//Subject_FL'):
                subject_uid, subject_ls, subject_appid = '', '', ''
                subject_title = ''   # новый: возможный Title, извлечённый из событий
                all_order_nums = set()
                # извлекаем ФИО/документы из Title внутри Subject_FL (существующая логика)
                client_info = self._extract_subject_title_info_fast(subject_fl)

                # пробегаем по элементам субъекта — собираем orderNum и данные событий
                for elem in subject_fl.iter():
                    order_num = elem.get('orderNum')
                    if order_num:
                        all_order_nums.add(order_num)

                    if elem.tag.startswith('FL_Event_'):
                        uid, ls, app_info = self._extract_event_data_fast(elem)
                        # app_info может содержать 'main_app_id', 'ls_number', 'title'
                        if uid and not subject_uid:
                            subject_uid = uid
                        if ls and not subject_ls:
                            subject_ls = ls
                        # prefer app id from event if we didn't have one yet
                        if app_info.get('main_app_id') and not subject_appid:
                            subject_appid = app_info.get('main_app_id')
                        # collect title if present
                        if app_info.get('title') and not subject_title:
                            subject_title = app_info.get('title')

                # index child events for 3-2 (если есть) — оставляем прежнюю логику, но добавляем title если есть
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

                # если нашли orderNum'ы — заполним кэши
                if all_order_nums:
                    final_app_info = {'main_app_id': subject_appid, 'ls_number': subject_ls, 'title': subject_title, 'child_events': {}}
                    for order_num in all_order_nums:
                        # main_event_by_ordernum: orderNum -> uid
                        if subject_uid:
                            self.main_event_by_ordernum[file_type][order_num] = subject_uid
                        if subject_ls:
                            self.ls_mapping[file_type][order_num] = subject_ls
                        # app_id_mapping хранит appId + title + ls
                        if order_num not in self.app_id_mapping[file_type]:
                            self.app_id_mapping[file_type][order_num] = final_app_info.copy()
                        # subject_info_mapping хранит fio/doc и теперь можем дополнить title
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
        """
        Возвращает (deal_uid, ls_or_title, app_info)
        app_info = {'main_app_id': str, 'ls_number': str, 'title': str}
        Логика:
          - UID: из FL_17_DealUid.uid или FL_55_Application.uid
          - appId: преимущественно из FL_55/num; если там нет — берём из FL_17/num (вторая часть после '/')
          - title (или лиц. №): если FL_17/num содержит '/', всё до '/' - title/ls; если нет — эвристика:
              * если FL_55 содержит appId, тогда FL_17/num скорее всего Title
              * если FL_55 пуст, а FL_17.num — цифры, то это, вероятно, appId (ставим в main_app_id)
        """
        deal_uid = ''
        ls_number = ''
        title = ''
        app_info = {'main_app_id': '', 'ls_number': '', 'title': ''}

        try:
            fl_17 = event_elem.find('.//FL_17_DealUid')
            fl_55 = event_elem.find('.//FL_55_Application')

            # UID
            if fl_17 is not None:
                uid17 = (fl_17.findtext('uid') or '').strip()
                if uid17:
                    deal_uid = uid17
            if not deal_uid and fl_55 is not None:
                uid55 = (fl_55.findtext('uid') or '').strip()
                if uid55:
                    deal_uid = uid55

            # appId — чаще всего в FL_55/num
            if fl_55 is not None:
                num55 = (fl_55.findtext('num') or '').strip()
                if num55:
                    app_info['main_app_id'] = num55

            # Обработка FL_17/num
            if fl_17 is not None:
                num17 = (fl_17.findtext('num') or '').strip()
                if num17:
                    if '/' in num17:
                        left, right = [p.strip() for p in num17.split('/', 1)]
                        # всё до '/' обычно Title/LS, после '/' — appId (если нет в FL_55)
                        if left:
                            title = left
                        if right and not app_info['main_app_id']:
                            app_info['main_app_id'] = right
                        # считаем ls_number как numeric часть left, если это похоже на лиц. счет
                        if left and left.replace(' ', '').isdigit():
                            ls_number = left
                    else:
                        # при отсутствии '/' — эвристика:
                        # если в FL_55 уже есть appId, то num17 скорее Title
                        if app_info['main_app_id']:
                            title = num17
                        else:
                            # если num17 — длинное число — возможно это appId
                            if num17.isdigit() and len(num17) >= 4:
                                app_info['main_app_id'] = num17
                            else:
                                title = num17

            # ls_number fallback
            if not ls_number and title and title.replace(' ', '').isdigit():
                ls_number = title

            app_info['ls_number'] = ls_number
            app_info['title'] = title
        except Exception:
            # не ломать — возвращаем частично заполненное
            pass

        return deal_uid, (ls_number or title), app_info

    def _extract_errors_actual(self, xml_path: str) -> List[Dict]:
        """
        Извлечение ошибок из отбивки с улучшенной логикой:
          - устойчивое чтение orderNum и orderNum_3_2 (несколько fallback-имен)
          - берём Title из app_id_mapping[file_type][orderNum]['title'] (если есть),
            иначе из ls_mapping или subject_info_mapping
          - ставим OrderNum_3_2 корректно, даже если поле имеет альтернативное имя
        """
        try:
            tree = get_cached_xml(xml_path)
            if tree is None:
                return []
            root = tree.getroot()
            filename = os.path.basename(xml_path)

            # определение типа файла по имени (безопасно)
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

            common_data = {
                'FileName': main_filename,
                'FileDate': file_date,
                'IncomingDocNumber': filename,
                'IncomingDocDate': root.findtext('.//incomingDocDate', '') or '',
                'BKI': bki_mapping.get(file_type, '')
            }

            errors: List[Dict] = []
            for error in root.iterfind('.//Error'):
                # message filter for daily
                error_message = (error.findtext('errorMessage') or '').strip()
                if self.group_type == 'ежеднев' and error_message == "2010 Данная запись выгружалась ранее":
                    continue

                # robust orderNum extraction (element, attribute, alternative names)
                orderNum = (error.findtext('orderNum') or '').strip()
                if not orderNum:
                    orderNum = (error.get('orderNum') or '').strip()
                if not orderNum:
                    orderNum = (error.findtext('OrderNum') or '') or ''
                    orderNum = orderNum.strip()

                # robust orderNum_3_2 extraction (several posible names)
                orderNum_3_2 = ''
                if self.group_type == '3-2':
                    orderNum_3_2 = (error.findtext('orderNum_3_2') or '').strip()
                    if not orderNum_3_2:
                        orderNum_3_2 = (error.get('orderNum_3_2') or '').strip()
                    if not orderNum_3_2:
                        # try alternative tag names
                        orderNum_3_2 = (error.findtext('orderNum3_2') or error.findtext('orderNum3.2') or '').strip()

                # lookup caches
                uid = self.main_event_by_ordernum.get(file_type, {}).get(orderNum, '')
                ls_number = self.ls_mapping.get(file_type, {}).get(orderNum, '')
                subject_info = self.subject_info_mapping.get(file_type, {}).get(orderNum, {}) or {}
                fio = subject_info.get('fio', '') or ''
                doc = subject_info.get('doc_info', '') or ''

                # resolve appId: check app_id_mapping (may include child event info)
                appid = ''
                event_info = self.app_id_mapping.get(file_type, {}).get(orderNum)
                if event_info:
                    appid = event_info.get('main_app_id', '') or ''
                # fallback for 3-2: try by orderNum_3_2
                if not appid and self.group_type == '3-2' and orderNum_3_2:
                    child_info = self.app_id_mapping.get(file_type, {}).get(orderNum_3_2)
                    appid = (child_info.get('main_app_id') if child_info else '') or ''

                # Title resolution: priority -
                # 1) app_id_mapping[file_type][orderNum]['title']
                # 2) subject_info['title']
                # 3) ls_mapping value
                title_val = ''
                if event_info and isinstance(event_info, dict):
                    title_val = event_info.get('title', '') or ''
                if not title_val:
                    title_val = subject_info.get('title', '') or ''
                if not title_val:
                    title_val = ls_number or ''

                # build error row
                err_row = {
                    'FIO': fio,
                    'Doc': doc,
                    'Title': title_val,
                    'AppId': appid,
                    'Uid': uid,
                    'OrderNum': orderNum,
                    'OrderNum_3_2': orderNum_3_2 if self.group_type == '3-2' else '',
                    'EventName': (error.findtext('eventName') or '').strip(),
                    'BlockName': (error.findtext('blockName') or '').strip(),
                    'FieldName': (error.findtext('fieldName') or '').strip(),
                    'FieldValue': (error.findtext('fieldValue') or '').strip(),
                    'ErrorCode': (error.findtext('errorCode') or '').strip(),
                    'ErrorMessage': error_message,
                    'EventNum': (error.findtext('eventNum') or '').strip()
                }
                # add common data
                err_row.update(common_data)
                errors.append(err_row)
            return errors
        except Exception as e:
            print(f"[EXTRACT ERR] {xml_path}: {e}")
            return []

    def extract_error_data_fast(self, xml_path: str) -> List[Dict]:
        """Алиас для совместимости"""
        return self._extract_errors_actual(xml_path)



# ------------------ ProcessPool worker (TOP-LEVEL, picklable) ------------------
def worker_process_main(args):
    """
    Worker function для ProcessPool. Внутри подпроцесса создаёт ErrorReceiptProcessor,
    обрабатывает список response файлов и возвращает JSON-serializable результат:
    {
        'main_file': path,
        'file_type': 'BD0'|'CHP'|'0XY_FCH'|None,
        'profiling': {'index_time':..., 'total_resp_time':..., 'responses': N, 'errors': M},
        'results': [{'file_date': 'YYYY-MM-DD', 'errors': [ {..}, ... ] , 'response_file': path}, ...]
    }
    """
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

    # создаём локальный процессор (в подпроцессе)
    t_idx_start = now_s()
    try:
        mapping = {file_type: main_file} if file_type else {}
        proc = ErrorReceiptProcessor(mapping, group_type)
    except Exception as e:
        print(f"[WORKER ERROR] Не удалось создать processor для {main_file}: {e}")
        proc = ErrorReceiptProcessor({}, group_type)
    t_idx_end = now_s()
    profiling['index_time'] = t_idx_end - t_idx_start

    # обработка отбивок
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
                # приводим paths к строкам (они уже строки), errors — dicts (serializable)
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
    # возвращаем результат
    return {'main_file': main_file, 'file_type': file_type, 'profile': profile_summary, 'results': results}

# ------------------ ProcessPool orchestration (агрессивный режим) ------------------
def process_pairs_processpool(file_pairs: List[Tuple[str, str]], group_type: str, max_workers: int = MAX_PROCESSES):
    """
    Группируем response-файлы по main_file и запускаем подпроцессы (по main_file).
    Каждый подпроцесс индексирует main_file и обрабатывает все его response-файлы.
    Возвращаем структуру results_by_date совместимую с save_errors_to_excel — dict: date -> [ (errors, meta), ... ]
    Также печатаем профилирование.
    """
    print(f"[PROCESSPOOL] Запуск агрессивного режима с max_workers={max_workers} для {len(file_pairs)} пар ({group_type})")
    per_main: Dict[str, List[str]] = {}
    for main_file, resp in file_pairs:
        per_main.setdefault(main_file, []).append(resp)

    tasks = []
    for main, responses in per_main.items():
        tasks.append((main, responses, group_type))

    results_by_date: Dict[str, List[Tuple[List[Dict], Dict]]] = defaultdict(list)
    profiles = []

    # запускаем ProcessPool
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(worker_process_main, task): task[0] for task in tasks}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    r = fut.result()
                except Exception as e:
                    print(f"[PROCESSPOOL FUTURE ERR] {e}")
                    continue
                # r содержит 'results' — список результатов по отбивкам
                profiles.append({'main_file': r.get('main_file'), 'profile': r.get('profile', {})})
                for item in r.get('results', []):
                    fd = item.get('file_date') or extract_date_from_filename(os.path.basename(item.get('main_file', '')))
                    errors = item.get('errors', [])
                    meta = {'main_file': item.get('main_file'), 'response_file': item.get('response_file')}
                    results_by_date[fd].append((errors, meta))
    except Exception as e:
        print(f"[PROCESSPOOL LAUNCH ERROR] {e}")
        # fallback: потоковый вариант
        print("[FALLBACK] Перейдём на ThreadPoolExecutor (fallback).")
        return process_pairs_parallel_optimized(file_pairs, group_type)

    # печатаем профилирование
    total_index = sum(p['profile'].get('index_time', 0.0) for p in profiles)
    total_resp = sum(p['profile'].get('total_resp_time', 0.0) for p in profiles)
    total_errors = sum(p['profile'].get('errors', 0) for p in profiles)
    total_responses = sum(p['profile'].get('responses', 0) for p in profiles)
    print(f"[PROFILE SUMMARY] main_files={len(profiles)}, responses={total_responses}, errors={total_errors}")
    print(f"  total_index_time={fmt_dt(total_index)}, total_resp_time={fmt_dt(total_resp)}, avg_resp_time={(total_resp/total_responses) if total_responses else 0:.3f}s")
    return results_by_date

# ------------------ ThreadPool variant (безопасный старт) ------------------
def process_pairs_parallel_optimized(file_pairs: List[Tuple[str, str]], group_type: str,
                                     progress_callback=None, start_progress=0, end_progress=100) -> Dict[str, List[Tuple[List[Dict], Dict]]]:
    # (реализация как в предыдущей версии — оставлена для fallback / сравнения)
    print(f"[OPT PROC] ThreadPool: обработка {len(file_pairs)} пар типа '{group_type}'")
    if progress_callback:
        progress_callback(start_progress, f"Обработка {group_type}")

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
                progress_callback(progress, f"Обработано {completed}/{total_tasks}")
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

# ------------------ Save to Excel ------------------
def _add_client_info_ultrafast(errors: List[Dict], main_file: str) -> List[Dict]:
    return errors

def save_errors_to_excel(groups_by_date: Dict[str, List[Tuple[List[Dict], Dict]]], filename_suffix: str) -> List[str]:
    created_files: List[str] = []
    all_errors: List[Dict] = []

    for file_date, date_groups in groups_by_date.items():
        for errors, group_info in date_groups:
            if errors:
                all_errors.extend(_add_client_info_ultrafast(errors, group_info.get('main_file', '')))

    if not all_errors:
        print(f"[SAVE] Нет ошибок для сохранения ({filename_suffix}).")
        return []

    excel_filename = f"ошибки_{filename_suffix}.xlsx"
    try:
        t0 = now_s()
        df = pd.DataFrame(all_errors)

        base_columns = ['FIO', 'Doc', 'Title', 'AppId', 'Uid', 'OrderNum',
                        'OrderNum_3_2', 'EventName', 'BlockName', 'FieldName',
                        'FieldValue', 'ErrorCode', 'ErrorMessage', 'EventNum',
                        'FileName', 'FileDate', 'IncomingDocNumber', 'IncomingDocDate', 'BKI']

        for col in base_columns:
            if col not in df.columns:
                df[col] = ''

        df = df.reindex(columns=base_columns)
        df.to_excel(excel_filename, index=False, engine='openpyxl')
        elapsed = now_s() - t0
        print(f"[SAVE] '{excel_filename}' создан за {fmt_dt(elapsed)} ({len(df)} строк)")
        created_files.append(excel_filename)
    except Exception as e:
        print(f"[SAVE ERROR] {e}")
        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = filename_suffix[:31]
            ws.append(base_columns)
            wb.save(excel_filename)
            created_files.append(excel_filename)
            print(f"[SAVE] Создан минимальный файл '{excel_filename}'")
        except Exception as e2:
            print(f"[SAVE FAIL] {e2}")
    return created_files

# ------------------ Высокоуровневые точки входа ------------------
def main_with_date(local_folder_path: str, selected_dates: List[str], event_type: str = "all", progress_callback=None) -> List[str]:
    # совместимая точка входа (как раньше)
    global MAIN_FILE_CACHE, TITLE_CACHE, ERROR_CACHE
    MAIN_FILE_CACHE.clear()
    TITLE_CACHE.clear()
    ERROR_CACHE.clear()

    file_pairs = find_file_pairs_between_dirs(local_folder_path, local_folder_path)  # не используем здесь
    return []

# ------------------ Локальный runner для LOCAL_FOLDERS (без app) ------------------
def run_local_processing(local_folders: dict, use_processpool: bool = True, max_workers: int = None):
    """
    Проход по LOCAL_FOLDERS (словарь с bki -> {'reports': path, 'responses': path}).
    Обрабатываем все БКИ, но сохраняем агрегированно:
      - один файл ошибок для '3-2' (если есть)
      - один файл ошибок для 'ежеднев' (если есть)
    Возвращает список созданных файлов.
    """
    if max_workers is None:
        try:
            max_workers = min(6, max(1, os.cpu_count() or 2))
        except Exception:
            max_workers = 2

    all_results_by_type = {'3-2': defaultdict(list), 'ежеднев': defaultdict(list)}
    created_files = []

    for bki_name, paths in local_folders.items():
        reports = paths.get('reports')
        responses = paths.get('responses')
        print(f"[RUN] BKI={bki_name} reports={reports} responses={responses}")
        if not reports or not responses or not os.path.isdir(reports) or not os.path.isdir(responses):
            print(f"[SKIP] Неверные директории для {bki_name}, пропускаем.")
            continue

        # Найдём пары в этих папках
        pairs = find_file_pairs_between_dirs(reports, responses)
        if not pairs:
            print(f"[SKIP] Нет пар для {bki_name}")
            continue

        # разделим по типам
        pairs_3_2 = []
        pairs_daily = []
        for m, r in pairs:
            gt = detect_group_type(m)
            if gt == '3-2':
                pairs_3_2.append((m, r))
            else:
                pairs_daily.append((m, r))

        # process
        if use_processpool:
            if pairs_3_2:
                res3 = process_pairs_processpool(pairs_3_2, '3-2', max_workers=max_workers)
                for date, items in res3.items():
                    all_results_by_type['3-2'][date].extend(items)
            if pairs_daily:
                resd = process_pairs_processpool(pairs_daily, 'ежеднев', max_workers=max_workers)
                for date, items in resd.items():
                    all_results_by_type['ежеднев'][date].extend(items)
        else:
            if pairs_3_2:
                res3 = process_pairs_parallel_optimized(pairs_3_2, '3-2')
                for date, items in res3.items():
                    all_results_by_type['3-2'][date].extend(items)
            if pairs_daily:
                resd = process_pairs_parallel_optimized(pairs_daily, 'ежеднев')
                for date, items in resd.items():
                    all_results_by_type['ежеднев'][date].extend(items)

        # очистка промежуточных кэшей после каждой БКИ
        MAIN_FILE_CACHE.clear()
        TITLE_CACHE.clear()
        ERROR_CACHE.clear()
        with XML_CACHE_LOCK:
            XML_CACHE.clear()

    # после обработки всех БКИ — сохраняем по типам (если есть)
    if any(all_results_by_type['3-2'].values()):
        created_files.extend(save_errors_to_excel(all_results_by_type['3-2'], '3-2'))
    else:
        print("[SAVE] Нет данных для 3-2")

    if any(all_results_by_type['ежеднев'].values()):
        created_files.extend(save_errors_to_excel(all_results_by_type['ежеднев'], 'ежеднев'))
    else:
        print("[SAVE] Нет данных для ежедневных")

    return created_files


# ------------------ Вспомогательное: определение group type ------------------
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
    except Exception as e:
        print(f"[WARN] detect_group_type: {e}")
    return 'ежеднев'

# ------------------ Утилиты: open / cleanup ------------------
def open_file(filepath: str):
    try:
        if sys.platform == "win32":
            os.startfile(filepath)
        else:
            if sys.platform == "darwin":
                os.system(f"open '{filepath}'")
            else:
                os.system(f"xdg-open '{filepath}'")
    except Exception:
        pass

def cleanup_old_files():
    try:
        for filename in os.listdir('.'):
            if filename.startswith('ошибки_') and filename.endswith('.xlsx'):
                try:
                    os.remove(filename)
                    print(f"[CLEANUP] Удален старый файл: {filename}")
                except Exception:
                    pass
    except Exception as e:
        print(f"[CLEANUP ERROR] {e}")

# ------------------ main запускаемый локально ------------------
if __name__ == '__main__':
    total_start_time = now_s()
    # очистим старые файлы для ясности
    cleanup_old_files()
    print("ch_r3.py — локальный runner. Запускаем обработку LOCAL_FOLDERS с ProcessPoolExecutor.")
    try:
        # ПЕРЕДАЕМ LOCAL_FOLDERS КАК ПЕРВЫЙ АРГУМЕНТ
        created = run_local_processing(LOCAL_FOLDERS, use_processpool=True, max_workers=MAX_PROCESSES)
        if created:
            print("\n[FINISH] Созданы файлы:")
            for f in created:
                print("  ", f)
        else:
            print("[FINISH] Нет созданных файлов.")
    except Exception as e:
        print(f"[FATAL ERROR] {e}")
        print("[FALLBACK] Попытка выполнить потоковую обработку.")
        try:
            # ПЕРЕДАЕМ LOCAL_FOLDERS КАК ПЕРВЫЙ АРГУМЕНТ
            created = run_local_processing(LOCAL_FOLDERS, use_processpool=False)
            print("[DONE FALLBACK]")
        except Exception as e2:
            print(f"[FATAL 2] {e2}")
    total_end_time = now_s()
    total_elapsed = total_end_time - total_start_time
    print(f"\nОбщее время выполнения: {fmt_dt(total_elapsed)}")
    print("Готово.")
