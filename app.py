from flask import Flask, render_template, request, jsonify, send_file
import os
import sys
import threading
import tempfile
from datetime import datetime
import requests 
import json
import re
from datetime import timedelta
import shutil  # Добавили для локального копирования файлов

app = Flask(__name__)

def cleanup_caches():
    """Очистка всех кэшей"""
    try:
        from check_receipts import MAIN_FILE_CACHE, TITLE_CACHE, ERROR_CACHE
        MAIN_FILE_CACHE.clear()
        TITLE_CACHE.clear()
        ERROR_CACHE.clear()
        print("Кэши очищены")
    except:
        pass

# Добавьте вызов в функцию run_processing
def run_processing(local_folder_path, selected_dates, event_type):
    global processing_status
    
    # Очищаем кэши перед началом
    cleanup_caches()

REMOTE_API_CONFIG = {
    'BASE_URL': 'http://192.168.20.89:8000', 
    'API_KEY': 'ВАШ_СЕКРЕТНЫЙ_API_КЛЮЧ',        
    'folders': {
        'ЭКС': {
            'reports': r'C:\CreditLine\EquifaxNew\Reports',
            'responses': r'C:\CreditLine\EquifaxNew\Receipts'
        },
        'ОКБ': {
            'reports': r'C:\CreditLine\UCBNew\Reports',
            'responses': r'C:\CreditLine\UCBNew\Receipts'
        },
        'НБКИ': {
            'reports': r'C:\CreditLine\UCHFNew\Reports',
            'responses': r'C:\CreditLine\UCHFNew\Receipts'
        }
    }
}

# Глобальные переменные для хранения состояния
processing_status = {"is_processing": False, "message": "", "result_files": []}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download-files', methods=['POST'])
def download_files_from_server():
    """Скачивает файлы с удаленного сервера по API в указанную локальную папку."""
    try:
        data = request.json
        local_folder_path = data.get('local_folder_path')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        if not local_folder_path:
            return jsonify({"error": "Укажите локальную папку для сохранения"})
        if not start_date or not end_date:
            return jsonify({"error": "Укажите диапазон дат"})

        os.makedirs(local_folder_path, exist_ok=True)
        
        api_url = REMOTE_API_CONFIG['BASE_URL']
        api_key = REMOTE_API_CONFIG['API_KEY']
        headers = {'X-API-Key': api_key}
        
        downloaded_files_count = 0
        
        # Проходим по всем настроенным БКИ и папкам
        for bki_name, paths in REMOTE_API_CONFIG['folders'].items():
            print(f"Запрашиваем файлы для {bki_name}...")
            
            # Скачиваем отчеты
            downloaded_files_count += fetch_and_download_files(
                api_url, headers, paths['reports'], start_date, end_date, local_folder_path
            )
            # Скачиваем отбивки
            downloaded_files_count += fetch_and_download_files(
                api_url, headers, paths['responses'], start_date, end_date, local_folder_path
            )
            
        return jsonify({
            "message": f"Загрузка завершена. Скачано {downloaded_files_count} файлов.",
            "downloaded_files": [], # Возвращаем пустой список, т.к. имена файлов нам не важны
            "local_folder": local_folder_path
        })
        
    except Exception as e:
        print(f"Критическая ошибка при скачивании файлов: {e}")
        return jsonify({"error": str(e)})

def fetch_and_download_files(api_url, headers, remote_path, start_date, end_date, local_dest_folder):
    """Получает список файлов по API и скачивает каждый из них."""
    count = 0
    
    # 1. Получаем список файлов
    print(f"  - Получение списка файлов из: {remote_path}")
    try:
        list_params = {'path': remote_path, 'start_date': start_date, 'end_date': end_date}
        response = requests.get(f"{api_url}/list-files", params=list_params, headers=headers, timeout=60)
        response.raise_for_status() # Вызовет ошибку, если статус не 2xx
        
        files_to_download = response.json().get('files', [])
        if not files_to_download:
            print(f"  - Файлы не найдены.")
            return 0
        print(f"  - Найдено {len(files_to_download)} файлов для скачивания.")

    except requests.exceptions.RequestException as e:
        print(f"  - ОШИБКА API при получении списка файлов: {e}")
        return 0

    # 2. Скачиваем каждый файл из списка
    for filename in files_to_download:
        try:
            download_params = {'path': remote_path, 'filename': filename}
            print(f"    - Скачивание: {filename}")
            
            with requests.get(f"{api_url}/download-file", params=download_params, headers=headers, stream=True, timeout=300) as r:
                r.raise_for_status()
                
                local_filepath = os.path.join(local_dest_folder, filename)
                with open(local_filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            count += 1
        except requests.exceptions.RequestException as e:
            print(f"    - ОШИБКА API при скачивании файла {filename}: {e}")
            continue # Переходим к следующему файлу
            
    return count

@app.route('/process', methods=['POST'])
def process_files():
    """Обрабатывает файлы из локальной папки"""
    global processing_status
    
    if processing_status["is_processing"]:
        return jsonify({"error": "Обработка уже выполняется"})
    
    try:
        data = request.json
        local_folder_path = data.get('local_folder_path')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        event_type = data.get('event_type', 'all')
        
        if not local_folder_path or not os.path.exists(local_folder_path):
            return jsonify({"error": "Локальная папка не существует"})
        
        if not start_date or not end_date:
            return jsonify({"error": "Укажите диапазон дат"})
        
        # Преобразуем диапазон дат в список отдельных дат
        selected_dates = get_dates_range(start_date, end_date)
        
        # Запускаем обработку в отдельном потоке
        thread = threading.Thread(
            target=run_processing, 
            args=(local_folder_path, selected_dates, event_type)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({"message": "Обработка запущена"})
    except Exception as e:
        return jsonify({"error": str(e)})

def get_dates_range(start_date, end_date):
    """Преобразует диапазон дат в список дат"""
    dates = []
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    
    current = start
    while current <= end:
        dates.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    
    return dates

def run_processing(local_folder_path, selected_dates, event_type):
    """Запускает обработку файлов (в отдельном потоке)"""
    global processing_status
    print(f"\n=== НАЧАЛО ОБРАБОТКИ ===")
    print(f"Папка: {local_folder_path}")
    print(f"Даты: {selected_dates}")
    print(f"Тип событий: {event_type}")
    
    processing_status = {
        "is_processing": True,
        "message": "Начинаем обработку...",
        "result_files": [],
        "progress": 0
    }
    
    try:
        from check_receipts import main_with_date
        
        # Создаем callback функцию для обновления прогресса
        def update_progress(progress, message):
            global processing_status
            processing_status["progress"] = progress
            processing_status["message"] = message
            print(f"Прогресс: {progress}% - {message}")  # Логируем в консоль
        
        # Используем модифицированную функцию с callback для прогресса
        result_files = main_with_date(local_folder_path, selected_dates, event_type, update_progress)
        
        processing_status.update({
            "is_processing": False,
            "message": f"Обработка завершена! Создано файлов: {len(result_files)}",
            "result_files": result_files,
            "progress": 100
        })
        
    except Exception as e:
        processing_status.update({
            "is_processing": False,
            "message": f"Ошибка обработки: {str(e)}",
            "result_files": [],
            "progress": 0
        })

@app.route('/status')
def get_status():
    return jsonify(processing_status)

@app.route('/download/<filename>')
def download_file(filename):
    """Скачивание обработанного файла"""
    try:
        if filename in processing_status["result_files"]:
            # Ищем файл в текущей директории или временной папке
            if os.path.exists(filename):
                return send_file(filename, as_attachment=True)
            else:
                # Пробуем найти в временной папке
                temp_path = os.path.join(tempfile.gettempdir(), filename)
                if os.path.exists(temp_path):
                    return send_file(temp_path, as_attachment=True)
        return jsonify({"error": "Файл не найден"})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
    
def cleanup_old_files():
    """Удаляет старые файлы ошибок при запуске приложения"""
    try:
        for filename in os.listdir('.'):
            if filename.startswith('ошибки_') and filename.endswith('.xlsx'):
                os.remove(filename)
                print(f"Удален старый файл: {filename}")
    except Exception as e:
        print(f"Ошибка при очистке старых файлов: {e}")