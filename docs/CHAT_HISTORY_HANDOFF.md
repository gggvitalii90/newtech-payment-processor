# NewTech: передача контекста новой сессии

Этот файл переносит проверенный рабочий контекст проекта; сам чат в GitHub не хранится.

## Репозиторий

- GitHub: `https://github.com/gggvitalii90/newtech-payment-processor`
- Ветка: `main`
- Локальная копия: `C:\Users\Vitaliy\OneDrive\work\new_tech\codex_new`
- Последний опубликованный commit: `755006a`
- Серверный каталог: `/opt/newtech-payment-processor`

## Читать вначале

`HANDOFF.md`, `PROJECT_STATUS.md`, `PROJECT_DECISIONS.md`, `TASKS.md`, `docs/obsidian-task-log.md`, `docs/source-task-notes.md`.

## Архитектура

- MAX-чаты: счета, подписи, наличные операции и пояснения.
- Google Drive: рабочее хранение счетов и ПП.
- Google Sheets: `Архив счетов`, `Архив ПП`, `Итоговая`, `Итоговая ИС`, ручная сверка.
- FinTablo: банковские транзакции и дополнительные операции.
- VPS: Docker и ежедневный systemd-сценарий.

FinTablo дает банковские операции, MAX дает счета/ПП/наличку, таблица является рабочим результатом и ручной сверкой.

## Ключевые правила

- Рабочий Google Drive: только `pcknew.tech@gmail.com`; личный Drive запрещен.
- `Итоговая ИС` содержит только операции ИнвестСтроя и разрешенных счетов.
- Переводы между счетами в итоговые листы не добавляются.
- Наличка не получает банк.
- UPay не является ответственным: ответственный берется из текста сообщения UPay.
- Комиссии и обслуживание агрегируются по согласованной схеме.
- FinTablo-операции, которых нет в MAX/чате, выделяются отдельно: доходы бледно-зеленым, расходы бледно-оранжевым.
- Дата — настоящая дата Sheets с отображением `dd.MM.yyyy`, сумма — число.
- ППР без счета допустим: строка все равно заносится и помечается для проверки.
- Неудачная запись в FinTablo повторяется до трех раз; после третьей попытки отчет содержит ID и причину.

## Деплой

```bash
cd /opt/newtech-payment-processor
git fetch origin
git checkout main
git pull --ff-only origin main
docker compose build --pull
docker compose run --rm daily python scripts/run_daily_update.py --help
git rev-parse --short HEAD
docker compose ps
systemctl status newtech-daily.timer --no-pager
```

Текущая сессия не смогла проверить VPS: SSH к `159.194.237.31:22` завершился тайм-аутом. Нельзя считать VPS обновленным, пока на сервере не подтвержден commit `755006a`.

## Секреты

Токены MAX/Telegram/FinTablo, Google OAuth secret/refresh token, SSH private key и пароль VPS не хранятся в GitHub и не должны попадать в Markdown. Для них используются локальный `.env`, каталог `secrets` и `.local_ssh`; в репозитории есть только `.env.example`.

Новая сессия должна клонировать репозиторий, прочитать документы выше, проверить `git status` и отдельно настроить секреты. Live-сессии Google, Drive, FinTablo и Telegram автоматически не переносятся.
