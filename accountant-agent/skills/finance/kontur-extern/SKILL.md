---
name: kontur-extern
description: Мониторинг отчётности через API Контур.Экстерн. Проверяет дедлайны форм, просроченные отчёты, входящие требования ФНС, документообороты, требующие действий, сертификаты КЭП. Только чтение — отправка отчётности не выполняется.
version: 1.0.0
author: user
metadata:
  hermes:
    tags: [kontur, reporting, production]
    immutable: true
---

# 📊 Контур.Экстерн — мониторинг отчётности (ОТЛОЖЕН)

Навык читает данные из Контур.Экстерна через REST API и сообщает о состоянии отчётности, дедлайнах и требованиях. **Только чтение** — ничего не отправляет и не изменяет.

> **Статус: проект отложен.** API Контур.Экстерна недоступен (ключи не выдают). Планируемая альтернатива: браузерная автоматизация через авторизованную сессию пользователя — агент читает веб-интерфейс (дедлайны, требования, статусы отчётности, документообороты) без API.
>
> **Срок действия:** до 24.01.2027. После этой даты планируется отказ от Контур.Экстерна при условии, что собственная программа учёта (`D:\AI\Projects\rameva_ausn\rameva_accounting`) готова и агент бухгалтера доработан под её API.
>
> **Назначение при доработке:** мониторинг дедлайнов форм, просроченных отчётов, входящих требований ФНС, документооборотов, требующих действий, сертификатов КЭП — через браузерную сессию, без API.

## Структура файлов

```
~/.nanobot/workspace/skills/kontur-extern/
    SKILL.md          ← этот файл
    TECHSPEC.md       ← техническое описание для разработчика
    extern_api.py     ← клиент API (авторизация, HTTP)
    extern_monitor.py ← функции мониторинга
    extern_alerts.py  ← heartbeat и проактивные оповещения
    extern.json       ← токены и настройки (⚠ никогда не копировать в GitHub)
```

---

## Принцип работы агента: «планируй — выполняй одним скриптом»

> **Критически важно:** лимит обращений к модели ограничен. Агент НЕ вызывает модель между отдельными запросами к API Контура.

**Правило:** для любой задачи агент:
1. Определяет, какой скрипт и с какими аргументами выполнить
2. Запускает **один** `exec` — скрипт сам делает все нужные API-запросы
3. Читает полный вывод и формирует ответ пользователю

**Запрещено:** запустить скрипт → прочитать частичный результат → снова обратиться к модели → запустить ещё один скрипт → и так по кругу.

**Правильно:** `python3 extern_monitor.py deadlines 30` → прочитать весь вывод → ответить пользователю.

---

## Таблица принятия решений

| Запрос пользователя | Команда агента |
|---|---|
| Какие отчёты нужно сдать на этой неделе? | `extern_monitor.py deadlines 7` |
| Что надо сдать в ближайший месяц? | `extern_monitor.py deadlines 30` |
| Есть просроченные отчёты? | `extern_monitor.py overdue` |
| Пришли требования от налоговой? | `extern_monitor.py demands` |
| Сводка по отчётности за квартал | `extern_monitor.py summary 2026 1` |
| Сводка за год | `extern_monitor.py summary 2026` |
| Что требует моего внимания? | `extern_monitor.py action` |
| Незавершённые черновики? | `extern_monitor.py drafts` |
| Новые события в документооборотах? | `extern_monitor.py events` |
| Статус сертификатов КЭП | `extern_monitor.py certs` |
| Всё ли в порядке с отчётностью? | `extern_alerts.py` (полная проверка) |
| Статус авторизации / токена | `extern_api.py status` |
| Переключить на тестовую площадку | `extern_api.py switch-test` |
| Переключить на рабочую площадку | `extern_api.py switch-prod` |

---

## Первичная настройка (выполняется один раз)

### Шаг 1 — Получить api-key и client_id

1. Подать заявку на подключение: https://kontur.ru/extern/api
2. Менеджер свяжется и предоставит `api-key` и `client_id`
3. Для работы с **рабочей** площадкой нужно быть абонентом Контур.Экстерн

> Документация: https://developer.kontur.ru/Docs/extern-api/auth_oidc/api-key.html

### Шаг 2 — Заполнить extern.json

Открыть `extern.json` и заполнить поля:
```json
{
  "test_mode": true,
  "credentials": {
    "api_key":   "ВАШ_API_KEY",
    "client_id": "ВАШ_CLIENT_ID"
  }
}
```

> `test_mode: true` — тестовая площадка (testkontur.ru), безопасно для проверки.
> `test_mode: false` — рабочая площадка (kontur.ru), реальные данные.

### Шаг 3 — Авторизация (Device Flow)

```
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_api.py login
```

Скрипт выведет ссылку. Открыть в браузере → войти под учётной записью Контура → подтвердить доступ. Скрипт автоматически получит и сохранит токены в `extern.json`.

> Документация по Device Flow: https://developer.kontur.ru/Docs/extern-api/auth_oidc/device_flow.html

### Шаг 4 — Получить account_id

```
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_api.py accounts
```

Скопировать нужный ID в `extern.json` → `account.account_id`. Опционально заполнить `inn`, `kpp`, `org_name` для удобства.

### Шаг 5 — Проверить работоспособность

```
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py deadlines 30
```

Должен вывести список дедлайнов.

---

## Переключение тестовой / рабочей площадки

Флаг `test_mode` в `extern.json` управляет площадкой:

| `test_mode` | Площадка | Авторизация | API |
|---|---|---|---|
| `true` | Тестовая | identity.testkontur.ru | extern-api.testkontur.ru |
| `false` | Рабочая | identity.kontur.ru | extern-api.kontur.ru |

Переключение через команды:
```
python3 extern_api.py switch-test   # → тестовая (сбрасывает токены)
python3 extern_api.py switch-prod   # → рабочая (сбрасывает токены)
```

После переключения всегда нужна повторная авторизация: `python3 extern_api.py login`

---

## Тестовые данные (Extern Test Tools)

На тестовой площадке есть роботы, имитирующие контролирующие органы.

**Коды инспекций для тестов:**

| Орган | Код | Поведение |
|---|---|---|
| ФНС | `0087` | Принимает все документы |
| ФНС | `0088` | Не принимает ни один документ |
| ФНС | `0085` | Присылает уведомления об уточнении |
| Росстат | `00-87` | Принимает все документы |
| Росстат | `00-88` | Не принимает ни один документ |
| МВД | `660-066` | Возвращает положительный результат |
| МВД | `770-077` | Возвращает детализированный отказ |

**Генерация тестовых данных:**
Swagger: https://developer.kontur.ru/doc/extern.test.tools

---

## Все команды

```bash
# Статус авторизации и режима
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_api.py status

# Первичная авторизация (один раз)
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_api.py login

# Список учётных записей (для получения account_id)
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_api.py accounts

# Переключить площадку (сбрасывает токены, требует повторного login)
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_api.py switch-test
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_api.py switch-prod

# Дедлайны (N дней вперёд, по умолчанию 7)
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py deadlines 7
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py deadlines 30

# Просроченные отчёты
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py overdue

# Требования ФНС без ответа
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py demands

# Сводка за период
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py summary 2026 1
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py summary 2026

# ДО, требующие ответных документов
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py action

# Незавершённые черновики
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py drafts

# Новые события ДО (инкрементально)
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py events
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py events LAST_SEQ_ID

# Сертификаты КЭП
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_monitor.py certs

# Полная проверка (heartbeat)
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_alerts.py

# Полная проверка в JSON (для машинной обработки)
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_alerts.py json
```

---

## Статусы форм отчётности

| Статус | Значение | Действие |
|---|---|---|
| `accepted` | Принят в срок | ОК |
| `accepted-after-deadline` | Принят с опозданием | Только информация |
| `manually-accepted` | Отмечен принятым вручную | ОК |
| `rejected` | Отклонён | Исправить и переотправить |
| `not-sent` | Не отправлен | Подготовить к отправке |
| `overdue` | Дедлайн прошёл, не отправлен | СРОЧНО — возможны штрафы |
| `sent-after-deadline` | Отправлен после дедлайна | Возможны санкции |
| `correction-rejected` | Корректировка отклонена | Требует внимания |

---

## Уровни алертов (extern_alerts.py)

| Уровень | Когда | Действие агента |
|---|---|---|
| `critical` | Просрочен / требование ≤ 2 р.дн. / дедлайн завтра или сегодня / сертификат истёк | Немедленно уведомить пользователя |
| `warning` | Дедлайн через 2–5 дней / требование > 2 р.дн. / нужен ответный документ / сертификат истекает через ≤ 30 дней | Записать в MEMORY.md, сообщить при следующем обращении |
| Пусто | Всё в порядке | Ничего не делать |

Срок ответа на требование ФНС: **6 рабочих дней** с момента получения.

---

## Настройка Heartbeat

Добавить в `~/.nanobot/workspace/HEARTBEAT.md`:

```markdown
## Контур.Экстерн — мониторинг

Каждые 4 часа в рабочее время:
    python3 ~/.nanobot/workspace/skills/kontur-extern/extern_alerts.py

Правила:
- Строки с "🔴 КРИТИЧНО" — немедленно уведомить пользователя через Telegram
- Строки с "🟡 Внимание" — записать в MEMORY.md, сообщить при следующем обращении
- "нет событий" — ничего не делать
```

---

## Настройки (extern.json → settings)

| Параметр | По умолчанию | Описание |
|---|---|---|
| `deadline_warning_days` | 5 | Предупреждать за N дней до дедлайна |
| `demand_critical_days` | 2 | Критично если осталось ≤ N р.дней на ответ по требованию |
| `ens_alert_on_negative` | true | Алерт при отрицательном сальдо ЕНС |

---

## Текущий статус и ограничения

> **⚠️ API пока недоступен.** Ключ `api_key` и `client_id` не получены. Скрипты (`extern_api.py`, `extern_monitor.py`, `extern_alerts.py`) существуют, но не могут выполнять запросы к API без авторизации.
>
> **План доработки:** когда ключ будет получен — заполнить `extern.json` и использовать API (Device Flow авторизация).
>
> **Fallback (пока нет API):** получать данные через браузер после авторизации на kontur.ru/extern с SMS-подтверждением. Парсить веб-интерфейс для получения состояния отчётности, дедлайнов, требований.

---

- **ЕНС / сверка с ФНС (ИОН):** отправка ИОН-запроса требует генерации XML с КЭП. Не реализовано в скриптах. Запрашивать сверку вручную через веб-интерфейс Контур.Экстерна, затем использовать `poll_ion_result()` для получения результата через API. ФНС отвечает до 5 рабочих дней.
- **Рабочие дни:** праздничные дни не учитываются при расчёте сроков ответа.
- **Отправка:** только чтение. Отправка отчётов и ответы на требования не реализованы.

---

## Устранение проблем

**Ошибка «Токен отсутствует»:**
```
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_api.py login
```

**Ошибка «account_id не задан»:**
```
python3 ~/.nanobot/workspace/skills/kontur-extern/extern_api.py accounts
```

**Ошибка импорта (requests / dateutil):**
```
pip install requests python-dateutil
```

**Проблемы с кодировкой на Windows:**
```
set PYTHONUTF8=1
python3 extern_alerts.py
```

**Токены тестовой площадки не работают на рабочей:**
```
python3 extern_api.py switch-prod
python3 extern_api.py login
```

---

## Ссылки на документацию

| Раздел | URL |
|---|---|
| Обзор API | https://developer.kontur.ru/Docs/extern-api/index.html |
| Начало работы | https://developer.kontur.ru/Docs/extern-api/howto/index.html |
| Аутентификация Device Flow | https://developer.kontur.ru/Docs/extern-api/auth_oidc/device_flow.html |
| Получение api-key | https://developer.kontur.ru/Docs/extern-api/auth_oidc/api-key.html |
| Таблица отчётности | https://developer.kontur.ru/Docs/extern-api/scenarios/device_api/reports_table.html |
| Работа с документооборотами | https://developer.kontur.ru/Docs/extern-api/dc/work-order-do.html |
| Требования ФНС | https://developer.kontur.ru/Docs/extern-api/scenarios/FNS/demand/index.html |
| Сверка с ФНС (ИОН) | https://developer.kontur.ru/Docs/extern-api/scenarios/FNS/ION.html |
| Тестовые данные | https://developer.kontur.ru/Docs/extern-api/test/index.html |
| Методы учётных записей (Swagger) | https://developer.kontur.ru/doc/extern |
| Методы черновиков (Swagger) | https://developer.kontur.ru/doc/extern.drafts |
| Методы документооборотов (Swagger) | https://developer.kontur.ru/doc/extern.docflows |
| Сервис контентов (Swagger) | https://developer.kontur.ru/doc/extern.contents |
| Extern Test Tools (Swagger) | https://developer.kontur.ru/doc/extern.test.tools |
