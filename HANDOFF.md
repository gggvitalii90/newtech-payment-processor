# NewTech Payment Processor: handoff

Этот файл нужен для передачи проекта другой сессии Codex/ИИ без повторного чтения всей истории чата.

## Репозиторий

- GitHub: `https://github.com/gggvitalii90/newtech-payment-processor`
- Основная ветка: `main`
- Локальная рабочая копия: `C:\Users\Vitaliy\OneDrive\work\new_tech\codex_new`
- Серверный каталог: `/opt/newtech-payment-processor`

## Слои системы

1. MAX-чаты: счета, подписи, наличные операции и пояснения.
2. Google Drive: хранение счетов и ПП.
3. Google Sheets: `Архив счетов`, `Архив ПП`, `Итоговая`, `Итоговая ИС`, а также ручная сверка.
4. FinTablo: банковские операции и классификация операций.
5. VPS: Docker-контейнер и ежедневный запуск без включенного компьютера.

## Запуск

- Оркестратор: `scripts/run_daily_update.py`.
- Docker: `docker-compose.yml`, `Dockerfile`.
- Серверный wrapper: `deploy/run_daily.sh`.
- Systemd: `deploy/systemd/newtech-daily.service` и `deploy/systemd/newtech-daily.timer`.
- Серверный dry-run:

```bash
cd /opt/newtech-payment-processor
DRY_RUN=1 START_DATE=YYYY-MM-DD END_DATE=YYYY-MM-DD ./deploy/run_daily.sh
```

Боевой запуск выполняется только после проверки даты, потока и текущего коммита.

## Текущий исходный код

Последние важные изменения находятся в истории Git:

- `601eebb` — исправление кодировки Telegram-отчета.
- `5e1c164` — очистка устаревших черных заливок строк Google Sheets.
- `6680480` — полный разбор номеров счетов.

Важно: наличие коммита в GitHub не означает, что он уже работает на VPS. Версию на VPS нужно проверять командой `git rev-parse --short HEAD` по SSH.

## Документация проекта

- `PROJECT_STATUS.md` — статус и известные хвосты.
- `PROJECT_DECISIONS.md` — согласованные правила обработки.
- `PROJECT_NAVIGATION.md` — карта модулей и сценариев.
- `TASKS.md` — очередь задач.
- `docs/plans/` — планы реализации.
- `docs/source-task-notes.md` — исходные заметки требований.

## Что нельзя хранить в GitHub

В репозитории не должны находиться `.env`, токены MAX/Telegram/FinTablo, Google OAuth client secret и refresh token, SSH private key и пароль VPS.

В Git хранится только `.env.example` с именами переменных. Реальные значения должны быть настроены отдельно на локальной машине и на VPS в `.env`/`secrets/`.

## Безопасные точки доступа

- Рабочий Google Drive: аккаунт `pcknew.tech@gmail.com`.
- Рабочая таблица и её ID задаются через переменные окружения.
- FinTablo API и Telegram задаются через переменные окружения.
- VPS: IP и пользователь не являются секретом, но пароль и SSH-ключ в репозиторий не помещаются.

## Правило передачи другой сессии

Сначала прочитать этот файл, затем `PROJECT_STATUS.md`, `PROJECT_DECISIONS.md` и `TASKS.md`. После этого проверить `git log`, рабочее дерево и фактический commit на VPS. Не считать задачу выполненной по одному изменению кода: нужен запуск и проверка Google Sheets/Telegram.
