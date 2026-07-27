# START HERE: NewTech Payment Processor

Это рабочая инструкция для новой сессии Codex. Репозиторий сам по себе не дает доступ к Google, FinTablo, Telegram или VPS: секреты и SSH-ключ находятся только локально/на сервере.

## 1. Сначала проверь, что сессия действительно исполняющая

Выполни команды, а не пересказывай их:

```powershell
git status --short
git rev-parse --short HEAD
Test-Path .env
Test-Path .local_ssh\newtech_vps_ed25519
```

Если рабочая папка не `C:\Users\Vitaliy\OneDrive\work\new_tech\codex_new`, это не локальная исполняющая сессия. GitHub можно читать, но локальные `.env`, OAuth-токен и SSH-ключ там не появятся сами.

## 2. Обязательный порядок работы

1. Прочитай `HANDOFF.md`, `PROJECT_STATUS.md`, `PROJECT_DECISIONS.md`, `TASKS.md`, `docs/CHAT_HISTORY_HANDOFF.md`, `docs/SECRETS_HANDOFF.md` и `docs/obsidian-task-log.md`.
2. Проверь `git log -5 --oneline` и фактические файлы проекта.
3. Для каждой задачи: найти причину -> внести точечную правку -> добавить/обновить тест -> запустить тест -> показать `git diff` -> сделать commit и push.
4. Не писать «исправлено», пока нет результата теста или фактической проверки таблицы/Telegram.
5. Не перезаписывать исторические листы и не делать глобальную очистку форматирования без отдельной проверки диапазона.

## 3. Проверка VPS

Локальная команда из PowerShell:

```powershell
Test-NetConnection 159.194.237.31 -Port 22
ssh -i .local_ssh\newtech_vps_ed25519 -o IdentitiesOnly=yes root@159.194.237.31 "cd /opt/newtech-payment-processor && git rev-parse --short HEAD"
```

Если порт 22 закрыт или timeout, это не повод выдумывать результат: зайди в Beget VNC/консоль и выполни на Ubuntu:

```bash
systemctl status ssh --no-pager
ss -tlnp | grep ':22'
ufw status verbose
journalctl -u ssh -n 50 --no-pager
```

После восстановления SSH обнови сервер и проверь фактический commit:

```bash
cd /opt/newtech-payment-processor
git fetch origin
git checkout main
git pull --ff-only origin main
docker compose build --pull daily
docker compose up -d --force-recreate daily
git rev-parse --short HEAD
```

Если `git pull` или сборка не выполнены, VPS не обновлен. Сообщи точную ошибку.

## 4. Секреты

Не проси пользователя вставлять токены в GitHub и не записывай их в этот файл. Локальные значения читаются из `.env`, Google OAuth-токенов и `.local_ssh/`; на VPS они должны быть настроены отдельно в `/opt/newtech-payment-processor/.env` и `secrets/`. При отсутствии секрета сообщи имя переменной/файла, но не проси публиковать значение.

## 5. Минимальная проверка перед боевым запуском

```powershell
python -m pytest tests/test_daily_update.py tests/test_telegram_notify.py tests/test_fintablo_sync_daily.py tests/test_fintablo_sync_from_manual_final.py
python -m compileall payment_processor scripts
```

Боевой запуск выполняй только после dry-run и проверки дат:

```bash
cd /opt/newtech-payment-processor
DRY_RUN=1 START_DATE=YYYY-MM-DD END_DATE=YYYY-MM-DD ./deploy/run_daily.sh
START_DATE=YYYY-MM-DD END_DATE=YYYY-MM-DD ./deploy/run_daily.sh
```

В отчете отдельно показывай: счета, ПП, наличку, банковские операции FinTablo, операции добавленные из FinTablo (желтая заливка), приходы (бледно-зеленая заливка), ошибки сопоставления и технические ошибки API с числом повторных попыток.

## Главное правило

Новая сессия должна либо реально выполнить изменение и проверить его, либо честно остановиться на конкретной блокировке (например, `SSH port 22 timeout`). Один только текстовый план не считается выполнением.
