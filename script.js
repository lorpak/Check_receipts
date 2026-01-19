let currentLocalFolderPath = '';
let downloadedFiles = [];

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
    setLast3Days(); // Устанавливаем последние 3 дня по умолчанию
    setupEventListeners();
});

// Настройка обработчиков событий
function setupEventListeners() {
    // Обработка выбора дат
    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');
    
    startDateInput.addEventListener('change', updateDateRange);
    endDateInput.addEventListener('change', updateDateRange);
}

// Установка последних 7 дней
function setLast3Days() {
    const today = new Date();
    const endDate = new Date(today);
    endDate.setDate(today.getDate() - 1); // Вчера (не включая сегодня)
    
    const startDate = new Date(endDate);
    startDate.setDate(endDate.getDate() - 2); // За 2 дня до вчера (итого 3 дня: пт-вс)
    
    document.getElementById('startDate').value = startDate.toISOString().split('T')[0];
    document.getElementById('endDate').value = endDate.toISOString().split('T')[0];
    
    // Обновляем инициализацию
    setLast3Days();
}

// Установка вчерашней даты
function setYesterday() {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayStr = yesterday.toISOString().split('T')[0];
    document.getElementById('startDate').value = yesterdayStr;
    document.getElementById('endDate').value = yesterdayStr;
    showNotification('Установлена вчерашняя дата', 'info');
}

// Выбор локальной папки
function selectLocalFolder() {
    const folderPath = prompt('Введите путь к локальной папке для сохранения файлов:');
    if (folderPath) {
        document.getElementById('localFolderPath').value = folderPath;
        currentLocalFolderPath = folderPath;
    }
}

// Скачивание файлов с сервера
async function downloadFiles() {
    const localFolderPath = document.getElementById('localFolderPath').value;
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    const eventType = document.querySelector('input[name="eventType"]:checked').value; // Добавляем тип событий
    
    if (!localFolderPath) {
        showNotification('Укажите локальную папку для сохранения', 'error');
        return;
    }
    
    if (!startDate || !endDate) {
        showNotification('Укажите диапазон дат', 'error');
        return;
    }
    
    try {
        const response = await fetch('/download-files', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                local_folder_path: localFolderPath,
                start_date: startDate,
                end_date: endDate,
                event_type: eventType // Передаем тип событий
            })
        });
        
        const data = await response.json();
        
        if (data.error) {
            showNotification('Ошибка: ' + data.error, 'error');
        } else {
            downloadedFiles = data.downloaded_files || [];
            showNotification(data.message, 'success');
        }
    } catch (error) {
        showNotification('Ошибка соединения: ' + error, 'error');
    }
}

// Запуск обработки
async function startProcessing() {
    window.startTime = null;
    const localFolderPath = document.getElementById('localFolderPath').value;
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    const eventType = document.querySelector('input[name="eventType"]:checked').value;
    
    if (!localFolderPath) {
        showNotification('Укажите локальную папку с файлами', 'error');
        return;
    }
    
    if (!startDate || !endDate) {
        showNotification('Укажите диапазон дат', 'error');
        return;
    }
    
    // Подготовка интерфейса
    const processBtn = document.getElementById('processBtn');
    processBtn.disabled = true;
    processBtn.textContent = 'Обработка...';
    
    document.getElementById('progressContainer').style.display = 'block';
    document.getElementById('resultsSection').style.display = 'none';
    
    try {
        const response = await fetch('/process', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                local_folder_path: localFolderPath,
                start_date: startDate,
                end_date: endDate,
                event_type: eventType
            })
        });
        
        const data = await response.json();
        
        if (data.error) {
            showNotification('Ошибка: ' + data.error, 'error');
            resetProcessButton();
        } else {
            showNotification('Обработка запущена успешно', 'success');
            checkProgress();
        }
    } catch (error) {
        showNotification('Ошибка соединения: ' + error, 'error');
        resetProcessButton();
    }
}

// Проверка прогресса обработки
async function checkProgress() {
    try {
        const response = await fetch('/status');
        const status = await response.json();
        
        // Обновление прогресс-бара
        const progressFill = document.getElementById('progressFill');
        const progressText = document.getElementById('progressText');
        
        progressFill.style.width = status.progress + '%';
        progressText.textContent = `${status.progress}% - ${status.message}`;
        
        // Добавляем время обработки
        if (!window.startTime) {
            window.startTime = new Date();
        }
        
        const currentTime = new Date();
        const elapsed = Math.round((currentTime - window.startTime) / 1000);
        const minutes = Math.floor(elapsed / 60);
        const seconds = elapsed % 60;
        
        progressText.textContent += ` (${minutes}м ${seconds}с)`;
        
        if (status.is_processing) {
            // Продолжаем проверять каждую секунду
            setTimeout(checkProgress, 1000);
        } else {
            // Обработка завершена
            window.startTime = null;
            showResults(status);
            resetProcessButton();
        }
    } catch (error) {
        showNotification('Ошибка при проверке статуса: ' + error, 'error');
        resetProcessButton();
    }
}

// Показ результатов
function showResults(status) {
    const resultsSection = document.getElementById('resultsSection');
    const statusMessage = document.getElementById('statusMessage');
    const resultFiles = document.getElementById('resultFiles');
    
    resultsSection.style.display = 'block';
    
    // Очистка предыдущих результатов
    statusMessage.innerHTML = '';
    resultFiles.innerHTML = '';
    
    // Сообщение о статусе
    const messageDiv = document.createElement('div');
    messageDiv.className = `notification ${status.result_files.length > 0 ? 'success' : 'info'}`;
    messageDiv.textContent = status.message;
    statusMessage.appendChild(messageDiv);
    
    // Список файлов
    if (status.result_files.length > 0) {
        const filesTitle = document.createElement('h3');
        filesTitle.textContent = 'Созданные файлы:';
        resultFiles.appendChild(filesTitle);
        
        const filesList = document.createElement('ul');
        
        status.result_files.forEach(file => {
            const listItem = document.createElement('li');
            
            const fileName = document.createElement('span');
            fileName.textContent = file;
            
            const downloadBtn = document.createElement('a');
            downloadBtn.href = `/download/${file}`;
            downloadBtn.className = 'download-btn';
            downloadBtn.textContent = 'Скачать';
            downloadBtn.target = '_blank';
            
            listItem.appendChild(fileName);
            listItem.appendChild(downloadBtn);
            filesList.appendChild(listItem);
        });
        
        resultFiles.appendChild(filesList);
    }
}

// Сброс кнопки обработки
function resetProcessButton() {
    window.startTime = null;
    const processBtn = document.getElementById('processBtn');
    processBtn.disabled = false;
    processBtn.textContent = 'Начать обработку';
}

// Показ уведомлений
function showNotification(message, type = 'info') {
    // Создаем элемент уведомления
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;
    
    // Добавляем на страницу
    const container = document.querySelector('.container');
    container.insertBefore(notification, container.firstChild);
    
    // Автоматическое удаление через 5 секунд
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

// Очистка результатов
function clearResults() {
    document.getElementById('resultsSection').style.display = 'none';
    document.getElementById('progressContainer').style.display = 'none';
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressText').textContent = '0%';
    showNotification('Результаты очищены', 'info');
}

// Экспорт функций для глобального использования
window.selectLocalFolder = selectLocalFolder;
window.setToday = setToday;
window.setYesterday = setYesterday;
window.setLast3Days = setLast3Days;
window.clearDates = clearDates;
window.downloadFiles = downloadFiles;
window.startProcessing = startProcessing;
window.clearResults = clearResults;