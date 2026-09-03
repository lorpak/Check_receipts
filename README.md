# CheckReceipts

Десктопное приложение для локальной проверки XML-отчётов и отбивок БКИ и формирования Excel-файлов с найденными ошибками.

## Начало разработки после клонирования

Требуются Python 3.13, Node.js и Rust.

Команды для `cmd.exe`:

```bat
cd local
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install
call .venv\Scripts\activate.bat
npm run tauri:dev
```

Встроенный Python не хранится в Git. В dev-режиме приложение использует Python из активной `.venv` или из `PATH`.

Перед release-сборкой подготовьте локальный встроенный runtime и запустите сборку:

```bat
powershell -ExecutionPolicy Bypass -File scripts\prepare-embedded-python.ps1
npm run tauri:build
```

Скрипт скачивает официальный Windows embeddable package Python 3.13.12, проверяет SHA-256 и устанавливает зависимости из `requirements.txt` в игнорируемую папку `resources\python`.

## Запуск готового приложения

Запустите `CheckReceipts.exe`. Откроется окно «Обработка ошибок БКИ».

## Подготовка данных

Поддерживаются два сценария.

### Обработка папок

Выберите одну или несколько папок с данными. Для нескольких путей используйте `;`. Рекомендуемая структура:

- `Reports` или `report`, `отчеты`;
- `Receipts`/`Responses` или `receipts`, `responses`, `отбивки`.

Также поддерживается структура CreditLine:

- `EquifaxNew/Reports` + `EquifaxNew/Receipts`;
- `UCBNew/Reports` + `UCBNew/Receipts`;
- `UCHFNew/Reports` + `UCHFNew/Receipts`.

### Обработка одного XML-файла

Выберите один XML-файл отчёта. Поддерживаются префиксы `0XY_FCH`, `BD0`, `CHP` и `CHT_`. Соответствующая отбивка должна находиться рядом или в подпапке `responses`, `response` либо `receipts`.

## Работа в интерфейсе

1. Выберите источник: папку, несколько папок или XML-файл.
2. Выберите папку для сохранения результата.
3. Для папок укажите период и тип событий.
4. Запустите проверку и дождитесь завершения.

Результаты автоматически открываются и сохраняются в выбранную папку. Возможные файлы:

- `errors_3-2.xlsx`;
- `errors_daily.xlsx`;
- `missing_receipts.xlsx`;
- `reconciliation.xlsx`.

## Проверки

```bat
cd local
.venv\Scripts\python.exe -m unittest discover -s tests
cargo check --manifest-path src-tauri\Cargo.toml
```

## Что намеренно не хранится в Git

Зависимости, встроенный Python, кэши, временные файлы, результаты тестов и сборки, настройки редактора, рабочие планы и старый PyInstaller-конфиг. Иконки, lock-файлы и тесты остаются: они нужны интерфейсу, воспроизводимой сборке и проверке кода.
