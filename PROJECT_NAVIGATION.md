# Навигация по проекту NewTech Payment Processor

Этот файл нужен, чтобы быстро ввести нового разработчика или ИИ в проект без перечитывания всего чата.

## Главная цепочка

`deploy/run_daily.sh` запускает Docker Compose сервис `daily`, который выполняет:

```bash
python scripts/run_daily_update.py --start YYYY-MM-DD --end YYYY-MM-DD --payment-source fintablo --staging-root /data
```

`run_daily_update.py` не делает один общий отчет за диапазон. Он проходит по датам внутри диапазона и для каждой даты делает отдельный цикл и отдельный Telegram-отчет.

## Ключевые файлы

| Файл | За что отвечает |
| --- | --- |
| `scripts/run_daily_update.py` | Основной ежедневный оркестратор: MAX, ПП, FinTablo, Drive lifecycle, Telegram. |
| `scripts/backfill_max_archive.py` | Забирает сообщения/файлы из MAX, распознает счета, пишет `Архив счетов`, грузит файлы на Drive. |
| `scripts/backfill_payment_history.py` | Обрабатывает ПП и наличку, пишет `Архив ПП`, `Итоговая`, `Итоговая ИС`. |
| `scripts/fintablo_sync_daily.py` | Синхронизация FinTablo по сгенерированной итоговой таблице: наличка и классификация операций. |
| `scripts/fintablo_sync_from_manual_final.py` | Наложение ручной итоговой таблицы Виталия на FinTablo как доверенного источника классификации. |
| `scripts/fintablo_sync_references.py` | Сверка/создание справочников FinTablo: статьи, сделки, этапы, направления. |
| `scripts/fintablo_apply_reference_review.py` | Применение ручного файла решений по лишним/новым справочникам FinTablo. |
| `scripts/organize_drive_archive.py` | Наведение порядка в архиве счетов на Google Drive. |
| `scripts/reorganize_payment_drive.py` | Структура папок для ПП на Google Drive. |
| `scripts/verify_payment_drive.py` | Проверка структуры и ссылок ПП на Google Drive. |
| `scripts/report_drive_root_folders.py` | Отчет по файлам, которые лежат в корнях объектов. |
| `payment_processor/max_message_matching.py` | Правила сопоставления счетов и подписей из MAX. |
| `payment_processor/invoice_archive.py` | Модель и логика архива счетов. |
| `payment_processor/invoice_drive_lifecycle.py` | Перенос оплаченных счетов в папку месяца, работа с папкой `__`. |
| `payment_processor/payment_history.py` | Структура и запись истории платежей. |
| `payment_processor/cash_archive.py` | Разбор наличных сообщений из чата. |
| `payment_processor/fintablo_client.py` | Низкоуровневый клиент API FinTablo. |
| `payment_processor/fintablo_transactions.py` | Модели/операции FinTablo. |
| `payment_processor/fintablo_references.py` | Справочники FinTablo: статьи, сделки, направления, этапы. |
| `payment_processor/telegram_notify.py` | Формирование и отправка Telegram-отчетов. |
| `payment_processor/google_api.py` | Авторизация и клиенты Google Drive/Sheets. |
| `payment_processor/google_archive.py` | Чтение/запись архива счетов в Google Sheets. |
| `payment_processor/google_payments.py` | Чтение/запись итоговых листов и архива ПП. |
| `payment_processor/dictionaries.py` | Загрузка справочника, предпочтительно из Google таблицы. |
| `rules.json` | Локальные правила нормализации и классификации. |
| `dictionaries.json` | Локальный fallback справочника, если Google недоступен. |
| `docker-compose.yml` | Контейнер ежедневного сценария. |
| `deploy/systemd/newtech-daily.timer` | Таймер для регулярного запуска на сервере. |

## Источники данных

- MAX чаты: счета, подписи к счетам, наличка, иногда пояснения к платежам.
- Google Drive: хранение счетов и ПП.
- Google Sheets `Архив счетов`: рабочая таблица автоматизации.
- Ручная Google таблица Виталия `1.1 Учет ПСК приход / расход`: источник истины для справочника и ручной классификации.
- FinTablo: банковские транзакции, комиссии, приходы и место для финансовой классификации.

## Основные листы Google таблицы автоматизации

- `Архив счетов`: все найденные счета и их классификация из счетов/сообщений.
- `Архив ПП`: все платежные поручения, без объектных колонок.
- `Итоговая`: итоговые операции ПСК.
- `Итоговая ИС`: итоговые операции Инвестстрой/ИС.

## Частые команды

Локальный тест дневного сценария без Telegram:

```powershell
python scripts/run_daily_update.py --date 2026-07-10 --dry-run --no-telegram
```

Локальный боевой запуск за один день:

```powershell
python scripts/run_daily_update.py --date 2026-07-10
```

Серверный запуск за период:

```bash
cd /opt/newtech-payment-processor
START_DATE=2026-07-10 END_DATE=2026-07-11 ./deploy/run_daily.sh
```

Запуск только тестов вокруг daily/Telegram/FinTablo:

```powershell
python -m pytest tests/test_daily_update.py tests/test_telegram_notify.py tests/test_fintablo_sync_daily.py tests/test_fintablo_sync_from_manual_final.py
```

## Что нельзя коммитить

- `.env`
- Google token/client secret файлы
- Telegram bot token
- FinTablo token
- пароли от VPS
- локальные выгрузки PDF/Excel с персональными или банковскими данными

Если нужен пример переменных, использовать `.env.example`, но без реальных значений.
