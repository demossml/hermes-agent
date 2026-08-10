# Secretary Live Setup Guide

Пошаговая инструкция для поднятия Hermes Secretary на живом gateway.

## 1. .env переменные

Добавить в `~/.hermes/.env` (или в `.env` профиля `profiles/secretary-*/`):

```bash
# === Почта (IMAP) ===
SECRETARY_MAIL_BACKEND=***SECRETARY_MAIL_IMAP_HOST=***      # imap.gmail.com / imap.mail.ru / imap.yandex.ru
SECRETARY_MAIL_IMAP_PORT=***
SECRETARY_MAIL_EMAIL=***SECRETARY_MAIL_PASSWORD=***           # пароль приложения (не обычный!)

# Опционально — SMTP (для отправки):
SECRETARY_MAIL_SMTP_HOST=***     # smtp.gmail.com / ... (авто-определяется по IMAP хосту если не задан)
SECRETARY_MAIL_SMTP_PORT=***
SECRETARY_MAIL_SMTP_PASSWORD=*** # если отличается от IMAP

# Режим отправки: 1 = только логировать, 0 = реальная отправка
SECRETARY_MAIL_DRY_RUN=1

# === Календарь (ICS) ===
SECRETARY_CAL_ICS_URL=https://... # секретный адрес iCal
```

### Gmail

1. Включить IMAP: Gmail → Настройки → Пересылка и POP/IMAP → Включить IMAP
2. Создать пароль приложения: Google Account → Security → 2-Step Verification → App passwords
3. Выбрать "Mail" → "Other" → скопировать 16-значный пароль
4. `SECRETARY_MAIL_IMAP_HOST=imap.gmail.com`
5. `SECRETARY_MAIL_PASSWORD=<16-значный пароль приложения>`

### Mail.ru / Yandex

1. Включить IMAP в настройках почты
2. Создать пароль приложения (Mail.ru: Настройки → Безопасность → Пароли приложений)
3. Хост: `imap.mail.ru` / `imap.yandex.ru`

### Google Calendar ICS

1. Google Calendar → Настройки календаря → Интеграция → Секретный адрес iCal
2. Скопировать URL вида `https://calendar.google.com/calendar/ical/.../basic.ics`
3. `SECRETARY_CAL_ICS_URL=<этот URL>`

## 2. Рестарт gateway

```bash
cd ~/.hermes/hermes-agent
# Остановить текущий gateway
hermes gateway restart
# или
hermes gateway start
```

## 3. Проверка

| # | Действие в Telegram | Ожидание |
|---|-------------------|---------|
| 1 | `/start` → онбординг (если новый) или `/меню` (если уже) | — |
| 2 | `/меню` → [📧 Почта] | Подменю с кнопками |
| 3 | [За ночь 12ч] | Список писем или «Пусто» |
| 4 | [✉️ Отправитель] → [✅ Отправить] | `[DRY_RUN] would send to ...` |
| 5 | `/digest` | «Доброе утро» + почта |
| 6 | `/меню` → [📅 Календарь] → [Сегодня] | События или «Нет событий» |
| 7 | `/secretary_health` | Статус mail/cal/dry_run/profile |
| 8 | `/who` | 8 полей |
| 9 | `/mode secretary` / `/mode dev` | Переключение режимов |
| 10 | `sec:...` | Переключение профиля |

## 4. DRY_RUN → реальная отправка

Когда уверен в черновиках:
```bash
# В .env:
SECRETARY_MAIL_DRY_RUN=***
```
После рестарта gateway кнопка [Отправить] реально шлёт письмо.

## 5. Troubleshooting

| Ошибка | Причина | Решение |
|--------|---------|---------|
| «Mail not configured» | Нет .env переменных | Проверить `env \| grep SECRETARY` |
| «Login failed» / auth | Неверный пароль/хост | Проверить пароль приложения, порт 993 |
| «Timeout» | Сеть / VPN | Проверить доступ до imap-хоста |
| «Calendar not configured» | Нет ICS URL | Проверить `SECRETARY_CAL_ICS_URL` |
| Письма не видны | IMAP folder name | Gmail использует `[Gmail]/Sent Mail` — авто |
