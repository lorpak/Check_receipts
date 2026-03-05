async function parseJsonResponse(response) {
    const rawText = await response.text();

    let data = null;
    if (rawText) {
        try {
            data = JSON.parse(rawText);
        } catch (error) {
            const bodyPreview = rawText.slice(0, 200).replace(/\s+/g, ' ').trim();
            throw new Error('HTTP ' + response.status + ': сервер вернул не JSON (' + (bodyPreview || 'пустой ответ') + ')');
        }
    }

    if (!response.ok) {
        const message = data && data.error ? data.error : ('HTTP ' + response.status);
        throw new Error(message);
    }

    return data || {};
}
document.addEventListener('DOMContentLoaded', function () {
    initializeTheme();
    const dateSection = document.getElementById('dateSection');
    if (dateSection) {
        dateSection.style.display = '';
    }
    setToday();
    window.selectedSourcePaths = [];
    window.selectedSourceType = 'folder';
    restoreSavedPaths();
    setupEventListeners();
});

function initializeTheme() {
    const savedTheme = localStorage.getItem('theme');
    const initialTheme = savedTheme || 'dark';
    applyTheme(initialTheme);
}

function applyTheme(theme) {
    document.body.setAttribute('data-theme', theme);
    const btn = document.getElementById('themeToggleBtn');
    if (btn) {
        btn.textContent = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема';
    }
}

function toggleTheme() {
    const currentTheme = document.body.getAttribute('data-theme') || 'dark';
    const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', nextTheme);
    applyTheme(nextTheme);
}

function getTauriInvoke() {
    if (typeof window === 'undefined' || !window.__TAURI__ || typeof window.__TAURI__.invoke !== 'function') {
        return null;
    }
    return window.__TAURI__.invoke;
}

function setupEventListeners() {
    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');
    const sourceInput = document.getElementById('sourceFolderPath');
    const outputInput = document.getElementById('outputFolderPath');

    if (startDateInput && endDateInput) {
        startDateInput.addEventListener('change', updateDateRange);
        endDateInput.addEventListener('change', updateDateRange);
    }
    if (sourceInput) {
        sourceInput.addEventListener('input', () => {
            const raw = sourceInput.value.trim();
            if (!raw) {
                window.selectedSourcePaths = [];
                setSourceType('folder');
                persistPaths();
                return;
            }
            window.selectedSourcePaths = raw.split(';').map(x => x.trim()).filter(Boolean);
            const first = window.selectedSourcePaths[0] || '';
            if (first.toLowerCase().endsWith('.xml') && window.selectedSourcePaths.length === 1) {
                setSourceType('file');
            } else {
                setSourceType('folder');
            }
            persistPaths();
        });
    }
    if (outputInput) {
        outputInput.addEventListener('input', persistPaths);
    }
}

function updateDateRange() {
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;

    if (startDate && endDate && startDate > endDate) {
        showNotification('Дата начала не может быть позже даты окончания', 'error');
    }
}

function setToday() {
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('startDate').value = today;
    document.getElementById('endDate').value = today;
}

function setSourceType(type) {
    window.selectedSourceType = type;
    toggleFilters(type === 'file');
}

function toggleFilters(disable) {
    const eventInputs = document.querySelectorAll('input[name="eventType"]');
    eventInputs.forEach(input => {
        input.disabled = disable;
    });
}

function toggleDateSection(hide) {
    const section = document.querySelector('.section .date-range')?.closest('.section');
    if (!section) {
        return;
    }
    section.style.display = hide ? 'none' : '';
}

function restoreSavedPaths() {
    const sourceInput = document.getElementById('sourceFolderPath');
    const outputInput = document.getElementById('outputFolderPath');

    const savedSource = localStorage.getItem('sourcePaths');
    const savedOutput = localStorage.getItem('outputPath');
    const savedType = localStorage.getItem('sourceType');

    if (sourceInput && savedSource) {
        sourceInput.value = savedSource;
        window.selectedSourcePaths = savedSource.split(';').map(x => x.trim()).filter(Boolean);
    }

    if (outputInput && savedOutput) {
        outputInput.value = savedOutput;
    }

    if (savedType === 'file') {
        setSourceType('file');
    } else {
        setSourceType('folder');
    }
}

function persistPaths() {
    const sourceInput = document.getElementById('sourceFolderPath');
    const outputInput = document.getElementById('outputFolderPath');

    if (sourceInput) {
        localStorage.setItem('sourcePaths', sourceInput.value.trim());
    }
    if (outputInput) {
        localStorage.setItem('outputPath', outputInput.value.trim());
    }
    localStorage.setItem('sourceType', window.selectedSourceType || 'folder');
}
function setLast3Days() {
    const today = new Date();
    const endDate = new Date(today);
    endDate.setDate(today.getDate() - 1);

    const startDate = new Date(endDate);
    startDate.setDate(endDate.getDate() - 2);

    document.getElementById('startDate').value = startDate.toISOString().split('T')[0];
    document.getElementById('endDate').value = endDate.toISOString().split('T')[0];
}

function setYesterday() {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayStr = yesterday.toISOString().split('T')[0];
    document.getElementById('startDate').value = yesterdayStr;
    document.getElementById('endDate').value = yesterdayStr;
    showNotification('Установлена вчерашняя дата', 'info');
}

function isTauriAvailable() {
    return typeof window !== 'undefined'
        && window.__TAURI__
        && window.__TAURI__.dialog
        && typeof window.__TAURI__.dialog.open === 'function';
}

async function selectFolder(title, inputId, selectionType = 'folder') {
    const input = document.getElementById(inputId);
    const currentPaths = Array.isArray(window.selectedSourcePaths) ? window.selectedSourcePaths : [];
    const initialPath = currentPaths.length > 0
        ? currentPaths[0]
        : (input ? input.value.trim().split(';')[0].trim() : '');

    try {
        if (!isTauriAvailable()) {
            showNotification('Tauri не доступен. Запустите приложение как десктопную версию.', 'error');
            return;
        }

        const dialogOptions = {
            title,
            defaultPath: initialPath || undefined,
            multiple: selectionType === 'multi_folder',
            directory: selectionType !== 'file',
        };

        if (selectionType === 'file') {
            dialogOptions.filters = [{ name: 'XML', extensions: ['xml'] }];
        }

        const selected = await window.__TAURI__.dialog.open(dialogOptions);
        if (!selected) {
            return;
        }

        const paths = Array.isArray(selected) ? selected : [selected];

        if (input) {
            if (inputId === 'sourceFolderPath') {
                window.selectedSourcePaths = paths;
                setSourceType(selectionType === 'file' ? 'file' : 'folder');
            }
            input.value = paths.join(';');
            persistPaths();
        }
    } catch (error) {
        showNotification('Ошибка выбора папки: ' + error, 'error');
    }
}

function selectSource() {
    const pickFile = window.confirm(
        'Выбрать один XML-файл?\n\nНажмите "ОК" для выбора файла.\nНажмите "Отмена" для выбора папки/папок.'
    );
    if (pickFile) {
        selectFolder('Выберите XML-файл для точечной обработки', 'sourceFolderPath', 'file');
        return;
    }
    selectFolder('Выберите одну или несколько папок', 'sourceFolderPath', 'multi_folder');
}

function selectOutputFolder() {
    selectFolder('Выберите папку для сохранения результатов', 'outputFolderPath');
}

function validateBeforeStart() {
    const sourceFolder = document.getElementById('sourceFolderPath').value.trim();
    const outputFolder = document.getElementById('outputFolderPath').value.trim();
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    const isFileSelection = window.selectedSourceType === 'file';

    if (!sourceFolder) {
        return 'Укажите папку или XML-файл с исходными данными';
    }

    if (!outputFolder) {
        return 'Укажите папку для сохранения результатов';
    }

    if (!isFileSelection && (!startDate || !endDate)) {
        return 'Укажите обе даты: начало и конец диапазона';
    }

    if (!isFileSelection && startDate && endDate && startDate > endDate) {
        return 'Дата начала не может быть позже даты окончания';
    }

    return null;
}

async function startProcessing() {
    window.startTime = null;
    const invoke = getTauriInvoke();

    const sourceFolder = document.getElementById('sourceFolderPath').value.trim();
    const sourcePaths = Array.isArray(window.selectedSourcePaths)
        ? window.selectedSourcePaths.filter(Boolean)
        : [];
    const outputFolder = document.getElementById('outputFolderPath').value.trim();
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    const eventType = document.querySelector('input[name="eventType"]:checked').value;
    const isFileSelection = window.selectedSourceType === 'file';

    const validationError = validateBeforeStart();
    if (validationError) {
        showNotification(validationError, 'error');
        return;
    }

    if (!invoke) {
        showNotification('Tauri не доступен. Запустите приложение как десктопную версию.', 'error');
        return;
    }

    const processBtn = document.getElementById('processBtn');
    processBtn.disabled = true;
    processBtn.textContent = 'Обработка...';

    try {
        const today = new Date().toISOString().split('T')[0];
        const payload = {
            source_folder: sourceFolder,
            source_paths: sourcePaths,
            output_folder: outputFolder,
            start_date: isFileSelection ? (startDate || today) : startDate,
            end_date: isFileSelection ? (endDate || today) : endDate,
            event_type: isFileSelection ? 'all' : eventType,
        };

        const statusFile = await invoke('start_processing', { payload: JSON.stringify(payload) });
        window.statusFilePath = statusFile;

        document.getElementById('resultsSection').style.display = 'none';
        document.getElementById('progressContainer').style.display = 'block';
        document.getElementById('progressFill').style.width = '0%';
        document.getElementById('progressText').textContent = '0% - запуск обработки...';
        showNotification('Обработка запущена успешно', 'success');
        checkProgress();
    } catch (error) {
        showNotification('Ошибка соединения: ' + error, 'error');
        resetProcessButton();
    }
}

async function checkProgress() {
    try {
        const invoke = getTauriInvoke();
        if (!invoke) {
            throw new Error('Tauri не доступен');
        }
        if (!window.statusFilePath) {
            throw new Error('Не найден файл статуса');
        }

        const status = await invoke('get_status', { statusFile: window.statusFilePath });

        const progressFill = document.getElementById('progressFill');
        const progressText = document.getElementById('progressText');

        progressFill.style.width = status.progress + '%';
        progressText.textContent = `${status.progress}% - ${status.message}`;

        if (!window.startTime) {
            window.startTime = new Date();
        }

        const elapsed = Math.round((new Date() - window.startTime) / 1000);
        const minutes = Math.floor(elapsed / 60);
        const seconds = elapsed % 60;
        progressText.textContent += ` (${minutes}м ${seconds}с)`;

        if (status.is_processing) {
            setTimeout(checkProgress, 1000);
            return;
        }

        if (!status.has_error && status.progress === 0 && (!status.result_files || status.result_files.length === 0)) {
            setTimeout(checkProgress, 500);
            return;
        }

        window.startTime = null;
        showResults(status);
        resetProcessButton();
    } catch (error) {
        showNotification('Ошибка при проверке статуса: ' + error, 'error');
        resetProcessButton();
    }
}

function showResults(status) {
    const resultsSection = document.getElementById('resultsSection');
    const statusMessage = document.getElementById('statusMessage');
    const resultFiles = document.getElementById('resultFiles');

    resultsSection.style.display = 'block';
    statusMessage.innerHTML = '';
    resultFiles.innerHTML = '';

    const messageDiv = document.createElement('div');
    messageDiv.className = `notification ${status.has_error ? 'error' : (status.result_files.length > 0 ? 'success' : 'info')}`;
    messageDiv.textContent = status.message;
    statusMessage.appendChild(messageDiv);

    if (status.result_files.length > 0) {
        const filesTitle = document.createElement('h3');
        filesTitle.textContent = 'Созданные файлы:';
        resultFiles.appendChild(filesTitle);

        const filesList = document.createElement('ul');
        const filePathMap = status.result_file_paths || {};

        if (Object.keys(filePathMap).length > 0) {
            Object.entries(filePathMap).forEach(([file, fullPath]) => {
                const listItem = document.createElement('li');

                const fileName = document.createElement('span');
                fileName.textContent = fullPath || file;
                listItem.appendChild(fileName);
                filesList.appendChild(listItem);
            });
        } else {
            status.result_files.forEach(file => {
                const listItem = document.createElement('li');
                const fileName = document.createElement('span');
                fileName.textContent = file;
                listItem.appendChild(fileName);
                filesList.appendChild(listItem);
            });
        }

        resultFiles.appendChild(filesList);

        const hint = document.createElement('p');
        hint.className = 'input-hint';
        hint.textContent = 'Файлы автоматически открываются после создания и сохраняются в целевой папке.';
        resultFiles.appendChild(hint);
    }
}

function resetProcessButton() {
    window.startTime = null;
    const processBtn = document.getElementById('processBtn');
    processBtn.disabled = false;
    processBtn.textContent = 'Обработать файлы';
}

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;

    const container = document.querySelector('.container');
    container.insertBefore(notification, container.firstChild);

    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transition = 'opacity 0.5s';
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 500);
    }, 5000);
}

function clearResults() {
    document.getElementById('resultsSection').style.display = 'none';
    document.getElementById('progressContainer').style.display = 'none';
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressText').textContent = '0%';
    showNotification('Результаты очищены', 'info');
}

window.selectSource = selectSource;
window.selectOutputFolder = selectOutputFolder;
window.setToday = setToday;
window.setYesterday = setYesterday;
window.setLast3Days = setLast3Days;
window.toggleTheme = toggleTheme;
window.startProcessing = startProcessing;
window.clearResults = clearResults;





