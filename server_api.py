from flask import Flask, request, jsonify, send_from_directory
import os
from datetime import datetime
import functools

# --- НАСТРОЙКИ БЕЗОПАСНОСТИ ---
VALID_API_KEY = 'ВАШ_СЕКРЕТНЫЙ_API_КЛЮЧ' 
ALLOWED_DIRECTORIES = {
    'C:\\CreditLine\\EquifaxNew\\Reports',
    'C:\\CreditLine\\EquifaxNew\\Receipts',
    'C:\\CreditLine\\UCBNew\\Reports',
    'C:\\CreditLine\\UCBNew\\Receipts',
    'C:\\CreditLine\\UCHFNew\\Reports',
    'C:\\CreditLine\\UCHFNew\\Receipts'
}

app = Flask(__name__)

# Декоратор для проверки API ключа
def require_api_key(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if request.headers.get('X-API-Key') and request.headers.get('X-API-Key') == VALID_API_KEY:
            return f(*args, **kwargs)a
        else:
            return jsonify({"error": "Unauthorized"}), 401
    return decorated_function

@app.route('/list-files', methods=['GET'])
@require_api_key
def list_files():
    """
    Возвращает список файлов в указанной директории,
    отфильтрованный по дате изменения.
    """
    path = request.args.get('path')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if path not in ALLOWED_DIRECTORIES:
        return jsonify({"error": "Access to this path is forbidden"}), 403

    if not os.path.exists(path):
        return jsonify({"error": "Path not found"}), 404

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    found_files = []
    for filename in os.listdir(path):
        filepath = os.path.join(path, filename)
        if os.path.isfile(filepath):
            mod_time = datetime.fromtimestamp(os.path.getmtime(filepath))
            if start_date.date() <= mod_time.date() <= end_date.date():
                found_files.append(filename)

    return jsonify({"files": found_files})

@app.route('/download-file', methods=['GET'])
@require_api_key
def download_file():
    """
    Отдает запрошенный файл.
    """
    path = request.args.get('path')
    filename = request.args.get('filename')

    if path not in ALLOWED_DIRECTORIES:
        return jsonify({"error": "Access to this path is forbidden"}), 403
    
    if '..' in filename or filename.startswith('/'):
        return jsonify({"error": "Invalid filename"}), 400

    return send_from_directory(directory=path, path=filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)