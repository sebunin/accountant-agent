# Технический план субагента-бухгалтера ООО «РАМЕВА»

**Версия:** 1.0  
**Дата:** 19.05.2026  
**Платформа:** Hermes Agent (NousResearch), Python, WSL2 / Windows 11  
**Режим налогообложения:** АУСН 8% (с 01.05.2026)

---

## 1. Концепция и место в архитектуре Hermes

Субагент реализуется как **именованный навык** (`SKILL.md`) с собственной базой данных, набором Python-скриптов и конфигурационным файлом. Вызывается из основного агента Hermes командой `/rameva` или через Telegram-gateway.

```
Hermes (основной агент)
    └── /rameva  →  Субагент «Рамева-Бухгалтер»
                        ├── SKILL.md         (инструкции и процедуры)
                        ├── config.yaml      (реквизиты, ставки, справочники)
                        ├── rameva.db        (SQLite — все данные)
                        └── scripts/
                              ├── parse_bank.py
                              ├── classify.py
                              ├── reports.py
                              ├── tax_calc.py
                              └── calendar.py
```

**Хранение данных:** только локально, SQLite-файл в `~/.hermes/skills/rameva/`.  
Никакие данные не покидают машину пользователя.

---

## 2. Конфигурационный файл `config.yaml`

Все параметры, которые могут меняться, вынесены в отдельный файл:

```yaml
company:
  name: ООО "РАМЕВА"
  inn: "4824091910"
  kpp: "482401001"
  ogrn: ""
  legal_address: ""
  actual_address: ""
  okved_main: ""
  okved_additional: []
  bank_account: "40702810602000129531"
  bank_name: "ЯРОСЛАВСКИЙ Ф-Л ПАО «Банк ПСБ»"
  bank_bik: "047888760"
  bank_corr: "30101810300000000760"

taxation:
  mode: АУСН
  rate: 8                      # процент от дохода минус расход
  min_rate: 3                  # минимальный налог — % от дохода
  start_date: "2026-05-01"

sfr:
  reg_number: "1027379435"     # единый регномер СФР
  ns_rate: 0.2                 # взносы на НС и ПЗ, % от ФОТ

director:
  name: "Фамилия Имя Отчество"
  inn: ""
  rate: 0.25                   # ставка занятости
  salary_gross: 0              # начисленная зарплата в месяц

diadok:
  operator: "Диадок"
  # API-ключ не используется пока, документы загружаются вручную

calendar:
  salary_day_1: 5              # день выплаты за вторую половину прошлого месяца
  salary_day_2: 20             # день выплаты аванса
  sfr_ns_day: 15               # крайний срок взносов НС
  enp_day: 28                  # крайний срок ЕНП (АУСН — уведомление от ФНС)

alerts:
  days_before: 3               # за сколько дней предупреждать о платеже
```

---

## 3. База данных (SQLite) — схема

### 3.1 Таблица `bank_transactions`

```sql
CREATE TABLE bank_transactions (
    id              INTEGER PRIMARY KEY,
    doc_type        TEXT,      -- 'Платежное поручение' / 'Банковский ордер'
    doc_number      TEXT,
    doc_date        TEXT,      -- дата документа
    value_date      TEXT,      -- дата зачисления / списания
    amount          REAL,
    direction       TEXT,      -- 'IN' / 'OUT'
    counterparty    TEXT,
    counterparty_inn TEXT,
    counterparty_account TEXT,
    purpose         TEXT,      -- назначение платежа
    kbk             TEXT,
    category        TEXT,      -- классификация (см. раздел 5)
    taxable         INTEGER,   -- 1 = учитывается в налоговой базе АУСН
    linked_doc_id   INTEGER,   -- ссылка на первичный документ
    imported_at     TEXT,
    source_file     TEXT       -- имя файла выписки
);
```

### 3.2 Таблица `counterparties`

```sql
CREATE TABLE counterparties (
    id          INTEGER PRIMARY KEY,
    name        TEXT,
    inn         TEXT UNIQUE,
    kpp         TEXT,
    type        TEXT,     -- 'client' / 'supplier' / 'fund' / 'bank' / 'employee'
    diadok_id   TEXT,     -- идентификатор в Диадоке
    notes       TEXT
);
```

### 3.3 Таблица `contracts`

```sql
CREATE TABLE contracts (
    id              INTEGER PRIMARY KEY,
    counterparty_id INTEGER,
    number          TEXT,
    date            TEXT,
    subject         TEXT,
    amount          REAL,
    currency        TEXT DEFAULT 'RUB',
    type            TEXT,    -- 'service' / 'supply' / 'gpc' / 'license'
    status          TEXT,    -- 'active' / 'closed' / 'suspended'
    notes           TEXT,
    FOREIGN KEY (counterparty_id) REFERENCES counterparties(id)
);
```

### 3.4 Таблица `documents` (первичка)

```sql
CREATE TABLE documents (
    id              INTEGER PRIMARY KEY,
    type            TEXT,     -- 'act' / 'invoice' / 'upd' / 'счёт' / 'gpc_act'
    number          TEXT,
    date            TEXT,
    counterparty_id INTEGER,
    contract_id     INTEGER,
    amount          REAL,
    vat             REAL DEFAULT 0,
    direction       TEXT,     -- 'IN' / 'OUT'
    status          TEXT,     -- 'draft' / 'sent' / 'signed' / 'rejected'
    diadok_id       TEXT,
    linked_tx_id    INTEGER,  -- связанная банковская транзакция
    notes           TEXT,
    FOREIGN KEY (counterparty_id) REFERENCES counterparties(id),
    FOREIGN KEY (contract_id) REFERENCES contracts(id)
);
```

### 3.5 Таблица `payroll`

```sql
CREATE TABLE payroll (
    id              INTEGER PRIMARY KEY,
    period          TEXT,     -- 'YYYY-MM'
    employee_name   TEXT,
    type            TEXT,     -- 'salary' / 'advance' / 'vacation' / 'gpc'
    gross           REAL,
    ndfl            REAL,
    alimony         REAL DEFAULT 0,
    net             REAL,
    paid_date       TEXT,
    tx_id           INTEGER,
    FOREIGN KEY (tx_id) REFERENCES bank_transactions(id)
);
```

### 3.6 Таблица `tax_obligations`

```sql
CREATE TABLE tax_obligations (
    id          INTEGER PRIMARY KEY,
    period      TEXT,       -- 'YYYY-MM'
    type        TEXT,       -- 'АУСН' / 'ЕНП' / 'СФР_НС' / 'НДФЛ'
    amount      REAL,
    due_date    TEXT,
    paid_date   TEXT,
    paid_amount REAL,
    tx_id       INTEGER,
    status      TEXT,       -- 'pending' / 'paid' / 'overdue'
    notes       TEXT
);
```

### 3.7 Таблица `tasks`

```sql
CREATE TABLE tasks (
    id          INTEGER PRIMARY KEY,
    title       TEXT,
    category    TEXT,    -- 'payment' / 'document' / 'report' / 'check'
    due_date    TEXT,
    priority    TEXT,    -- 'high' / 'normal' / 'low'
    status      TEXT,    -- 'open' / 'done' / 'snoozed'
    notes       TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
```

### 3.8 Таблица `state` (сводные показатели)

```sql
CREATE TABLE state (
    key     TEXT PRIMARY KEY,
    value   TEXT,
    updated TEXT
);
-- Примеры ключей:
-- balance_current, receivables_total, payables_total,
-- tax_ausn_ytd, tax_paid_ytd, period_income, period_expense
```

---

## 4. Python-скрипты

### 4.1 `parse_bank.py` — парсер выписки 1С

Читает файл формата `1CClientBankExchange` (Windows-1251), создаёт записи в `bank_transactions`.

**Особенности:**
- Автоматически определяет направление (IN/OUT) по полю `ДатаПоступило` vs `ДатаСписано`
- Дедупликация: не создаёт дубли при повторной загрузке того же файла (ключ: `doc_number + doc_date + amount + direction`)
- Обновляет `state.balance_current` из поля `КонечныйОстаток`

**Вход:** путь к `.txt`-файлу  
**Выход:** количество добавленных / пропущенных транзакций

### 4.2 `classify.py` — классификатор транзакций

Автоматически размечает транзакции по категориям и налогооблагаемости для АУСН.

**Правила классификации:**

| Признак | Категория | Налогооблагается |
|---|---|---|
| КБК `18201061201010000510` | ЕНП | Нет |
| КБК `79710212...` | СФР взносы | Нет |
| Получатель = ФНС / казначейство | Налоги и взносы | Нет |
| `КодНазПлатежа=1` | Зарплата | Нет (расход) |
| `КодНазПлатежа=2` | Алименты | Нет |
| Назначение содержит «комиссия» | Банковская комиссия | Да (расход) |
| Назначение содержит «хостинг» / «сопровождение» | Выручка IT | Да (доход) |
| Назначение содержит «займ» / «кредит» | Займ | Нет |
| Получатель = контрагент из реестра | Расчёты с контрагентами | Да |
| Прочее | Не классифицировано | Требует проверки |

Некласифицированные транзакции выносятся в задачу с приоритетом `high`.

### 4.3 `tax_calc.py` — расчёт налога АУСН

Считает налоговую базу за текущий период:

```
База = Сумма налогооблагаемых доходов - Сумма налогооблагаемых расходов
Налог = max(База × 8%, Доходы × 3%)
```

На АУСН налог исчисляет ФНС на основе данных банка, но агент ведёт параллельный расчёт для контроля и прогнозирования.

### 4.4 `reports.py` — генератор отчётов

Формирует текстовые таблицы (Rich/tabulate) и данные для графиков:

- **Текущее состояние:** остаток по счёту, дебиторка, кредиторка, налоговые обязательства
- **Выписка за период:** доходы / расходы по категориям
- **Налоговый расчёт:** прогноз АУСН за месяц
- **Незакрытые документы:** акты без оплаты, оплаты без актов
- **Календарь платежей:** ближайшие обязательные платежи

Вывод: UTF-8 таблицы в консоль / Telegram, JSON для построения графиков.

### 4.5 `calendar.py` — планировщик задач

При запросе или по cron генерирует список задач на ближайшие N дней:

- Зарплата директора (2 раза в месяц)
- Взносы СФР на НС (15-е число)
- ЕНП (на основе уведомления от ФНС, по умолчанию 28-е)
- Контроль незакрытой дебиторки (просроченные оплаты по договорам)
- Напоминания о документах из Диадока

---

## 5. SKILL.md — инструкции субагента

Навык описывает агенту:

1. **Когда загружать выписку:** пользователь говорит «загрузи выписку» или прикладывает файл → вызов `parse_bank.py` → вызов `classify.py` → отчёт о результатах
2. **Как отвечать на вопросы о состоянии:** всегда читать из `state`-таблицы, не угадывать
3. **Что делать с неклассифицированными транзакциями:** запрашивать уточнение у пользователя
4. **Как строить отчёты:** вызов `reports.py` с нужными параметрами
5. **Что нельзя делать:** не изменять классификацию без подтверждения, не удалять транзакции

---

## 6. Интеграция с Hermes

### 6.1 Telegram Gateway

Команды в Telegram:
```
/rameva статус          — текущий остаток, ближайшие задачи
/rameva выписка         — прикрепить файл для загрузки
/rameva налог           — прогноз АУСН за текущий месяц
/rameva задачи          — список открытых задач
/rameva отчёт           — краткий финансовый отчёт
```

### 6.2 Cron-задачи

```yaml
# В ~/.hermes/cron/jobs.json
- name: "rameva_daily_check"
  schedule: "0 9 * * *"          # каждый день в 9:00
  skill: rameva
  prompt: "Проверь ближайшие платежи и незакрытые задачи. Сообщи если есть срочное."
  deliver_to: telegram

- name: "rameva_monthly_tax"
  schedule: "0 10 1 * *"         # 1-го числа каждого месяца
  skill: rameva
  prompt: "Сформируй налоговый расчёт АУСН за прошлый месяц и список обязательных платежей."
  deliver_to: telegram
```

---

## 7. Диадок — работа без API

Пока API Диадока не подключён, документооборот ведётся так:

1. Пользователь выгружает документы из Диадока вручную (PDF или Excel)
2. Прикладывает к сообщению агенту
3. Агент распознаёт реквизиты (дата, сумма, контрагент, номер) и создаёт запись в `documents`
4. Сверяет с транзакциями в банке

Когда появится API Диадока — добавить интеграцию через MCP-сервер не составит труда.

---

## 8. Специфика АУСН 8% для агента

**Что агент контролирует:**

| Обязательство | Срок | Как формируется |
|---|---|---|
| Взносы СФР (НС и ПЗ) | 15-е число | 0,2% от начисленной зарплаты |
| НДФЛ с зарплаты | Автоматически банком | Агент фиксирует факт |
| АУСН (ЕНП) | 28-е число | ФНС считает, агент прогнозирует |
| Зарплата директора | 5-е и 20-е | По config.yaml |

**Что агент НЕ делает:**
- Не подаёт декларации (на АУСН не нужны)
- Не формирует квитанции (только отслеживает факт оплаты)
- Не заменяет бухгалтера при ГПХ-проверке

---

## 9. Порядок разработки (очередь задач)

### Фаза 1 — Основа (2–3 дня)
1. Создать структуру папки навыка `~/.hermes/skills/rameva/`
2. Написать `config.yaml` с реквизитами компании
3. Создать `init_db.py` — инициализация SQLite
4. Написать `parse_bank.py` — протестировать на текущей выписке

### Фаза 2 — Классификация и учёт (3–5 дней)
5. Написать `classify.py` с правилами для IT-компании
6. Заполнить реестр контрагентов из ОСВ
7. Написать `reports.py` — базовые отчёты
8. Создать `SKILL.md` с инструкциями

### Фаза 3 — Налоги и календарь (2–3 дня)
9. Написать `tax_calc.py`
10. Написать `calendar.py`
11. Настроить cron-задачи в Hermes
12. Подключить Telegram-gateway

### Фаза 4 — Первичка и Диадок (по мере необходимости)
13. Распознавание PDF-документов из Диадока
14. Сверка документов с транзакциями
15. Контроль дебиторки

---

## 10. Что нужно предоставить для начала разработки

- [ ] Полные реквизиты компании (ОГРН, юр. адрес, ОКВЭД)
- [ ] Регистрационный номер в СФР (из выписки видно: `1027379435`)
- [ ] Размер зарплаты директора (начисленная сумма)
- [ ] Список основных контрагентов (можно из Диадока или ОСВ)
- [ ] Подтверждение структуры папки в WSL2 для установки Hermes
