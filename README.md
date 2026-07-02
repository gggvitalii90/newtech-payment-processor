# Обработка платежных поручений
## Основной результат в Google

Основной рабочий файл теперь задается `GOOGLE_ARCHIVE_SPREADSHEET_ID` и содержит три листа:

- `Архив счетов` - распознанные счета и подписи MAX;
- `Итоговая` - готовые строки выбранного дня для копирования;
- `Архив ПП` - накопительная история банковских платежных поручений без дублей.

После проверки и сохранения строк программа обновляет Google-таблицу. Локальный `result.xlsx` сохраняется как резервная копия. Наличные операции входят в `Итоговую`, но не входят в `Архив ПП`.

Локальная программа для чтения PDF платежных поручений из папок `_ПП` и обновления одного Excel-файла результата.

## Запуск

Двойной клик по `start_payment_processor.bat` открывает окно программы.

Файл запуска использует `pythonw`, поэтому окно терминала после старта программы не висит.

В окне:
- выберите режим `ПСК` или `ИС`;
- проверьте папку PDF или выберите ее вручную;
- нажмите `Обработать`;
- проверьте строки во встроенной таблице;
- при необходимости отфильтруйте, отсортируйте или отредактируйте ячейки двойным кликом;
- нажмите `Сохранить в Excel`;
- результат обновится в `C:\Users\Vitaliy\OneDrive\work\new_tech\_ПП\result.xlsx`.

## Настройки

- `config.json` хранит корневую папку, путь к результату и режимы.
- `rules.json` хранит соответствия счетов банкам и правила классификации для `Объект`, `Проект`, `Статья бюджета`, `Ответственный`.
- `dictionaries.json` хранит словари нормализации для подписей MAX: как написали сотрудники -> как должно быть в справочнике.
- `Справочник.xlsx` читается при запуске программы и используется для выпадающих списков в таблице и Excel.
- `.env` хранит локальные настройки MAX API и не попадает в git. Создайте его рядом с программой по примеру `.env.example`.

Если правило классификации не найдено, поле остается пустым.

## Планы

Подробная дорожная карта проекта лежит в `docs/plans/`:

- `01-payment-pdf-processor.md`
- `02-payment-processor-fixes.md`
- `03-max-api-download-bot.md`
- `04-invoice-archive.md`
- `05-payment-autofill-from-invoices.md`

## MAX

Кнопка `Скачать из MAX` берет дату из поля `Дата MAX`, создает папку `_ПП\YYYY.MM.DD` и скачивает вложения из указанного чата за этот день по МСК. В рабочую папку попадают только PDF, которые похожи на платежные поручения. Остальные вложения сохраняются отдельно в `_ПП\_прочее_MAX\YYYY.MM.DD`, чтобы ничего не потерять.

После скачивания создается локальный черновик архива счетов: `_ПП\_архив_счетов_черновик\YYYY.MM.DD.xlsx`. Это временный слой до подключения Google Drive и Google Sheets.

Некоторые сообщения без файла тоже попадают в архив. Сейчас поддержана форма `ИП Мочалов` со строками `зп - сумма` и `налоги - сумма`: программа создает строки без ссылки на счет, с типом `Расход`, оплатой `Безналичные без НДС`, банком `б/н ИП Мочалов`, проектами `ФОТ` и `Налоги`, статьями `Официальная ЗП` и `Налоги НДФЛ`.

После кнопки `Скачать из MAX` программа показывает итог:
- сколько вложений скачано;
- сколько PDF попало в платежные поручения;
- сколько файлов ушло в прочее/архив счетов;
- сколько файлов пропущено как уже скачанные;
- сколько вложений найдено без ссылки;
- путь к локальному черновику архива;
- сколько строк отправлено в Google архив.

Ошибки и технические детали пишутся в `logs/payment_processor.log`. В окне есть кнопка `Открыть лог`.

В `.env` нужны значения:

```text
MAX_BOT_TOKEN=ваш_токен
MAX_CHAT_ID=ваш_chat_id
MAX_MESSAGE_COUNT=100
GOOGLE_CLIENT_SECRET_FILE=C:\Users\Vitaliy\OneDrive\downloads_one_drive\client_secret_...json
GOOGLE_TOKEN_FILE=.google_token.json
GOOGLE_ARCHIVE_SPREADSHEET_ID=ссылка_или_id_google_таблицы
GOOGLE_ARCHIVE_SHEET_NAME=Архив счетов
GOOGLE_ARCHIVE_ROOT_FOLDER_ID=ссылка_или_id_папки_drive
```

`MAX_MESSAGE_COUNT` — размер одной страницы API, максимум 100. Если за день сообщений больше, программа делает несколько запросов внутри выбранной даты. Повторный запуск не должен создавать дубли: в служебной папке скачивания хранится журнал `.max_downloaded.json`.

Проверка Google OAuth:

```powershell
python check_google_oauth.py
```

При первом запуске откроется браузер Google. После входа программа сохранит локальный `.google_token.json`; этот файл игнорируется git.

## Командный запуск

```powershell
python process_folder.py "C:\Users\Vitaliy\OneDrive\work\new_tech\_ПП\2026.06.08" --mode ПСК
python process_folder.py "C:\Users\Vitaliy\OneDrive\work\new_tech\_ПП\2026.03.06 ИС" --mode ИС
```

## Историческая пересборка Архива ПП и Итоговой

Для HTTPS-загрузок MAX через системные сертификаты Windows:

```powershell
python -m pip install truststore
```

Полная пересборка с 1 апреля 2026 года:

```powershell
python -u scripts\backfill_payment_history.py --start 2026-04-01 --staging-root ".staging\payment-history"
```

Сначала безопасно проверить результат без записи в Google:

```powershell
python -u scripts\backfill_payment_history.py --start 2026-04-01 --dry-run --staging-root ".staging\payment-history"
```

Загрузка возобновляется по дням из `.staging\payment-history\state.json`. Повторный разбор уже скачанных файлов выполняется с `--skip-download`. Отчеты создаются в `reports/payment_archive_full.csv`, `reports/payment_final_full.csv`, `reports/payment_history_issues.csv` и `reports/payment_history_summary.json`. Несопоставленный со счетом ПП остается в `Итоговой`; пустыми остаются только поля, которые можно получить исключительно из счета или его подписи.

Проверка фактических строк Google после записи:

```powershell
python scripts\verify_payment_sheets.py
```
## Структура файлов ПП в Google Drive

Корневая папка задается через `GOOGLE_PAYMENT_ROOT_FOLDER_ID`. Внутри используется структура `Год / Месяц / Дата`, например `2026 / 06 / 2026.06.22`. Для режима ИС суффикс сохраняется: `2026.06.22 ИС`.

Существующие дневные папки перемещаются без копирования, поэтому ID папок, файлов и ссылки сохраняются. Проверка структуры:

```powershell
python scripts\verify_payment_drive.py
```

`Архив ПП` содержит только 10 колонок: `№`, `Дата`, `Тип операции`, `Тип оплаты`, `Банк`, `Контрагент`, `Номер счета`, `Назначение платежа`, `Ссылка на ПП`, `Сумма`. Ежедневный сценарий загружает отсутствующий ПП в папку его даты и записывает ссылку на файл. В `Итоговой` колонка `Ссылка на счет` заполняется только ссылкой сопоставленного счета.

Ручная безопасная проверка реорганизации папок:

```powershell
python scripts\reorganize_payment_drive.py
```

Реальное перемещение выполняется только с явным флагом:

```powershell
python scripts\reorganize_payment_drive.py --apply
```

## FinTablo

FinTablo API settings are stored in local `.env`. The first integration phase is read-only. The token must not be committed to git.

```text
FINTABLO_API_TOKEN=your_api_token
FINTABLO_BASE_URL=https://api.fintablo.ru
```

Safe read-only access check:

```powershell
python scripts\fintablo_readonly_check.py --start 2026-07-01 --end 2026-07-01
```

The script reads moneybags, categories, partners, directions, deals, employees, and cash-flow transactions for the selected period. It does not create, update, or delete FinTablo data.
