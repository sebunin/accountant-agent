#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extern_monitor.py — сценарии мониторинга Контур.Экстерн.

Принцип работы: ОДИН вызов скрипта — ОДИН блок API-запросов к Контуру.
Скрипт сам обходит пагинацию, сводит данные и выводит результат.
Агент читает итоговый вывод и формирует ответ пользователю
БЕЗ промежуточных обращений к модели между запросами.

Использование из командной строки:
  python3 extern_monitor.py deadlines [days]          # дедлайны ближайших N дней
  python3 extern_monitor.py overdue                   # просроченные отчёты
  python3 extern_monitor.py demands                   # требования ФНС без ответа
  python3 extern_monitor.py summary [year] [quarter]  # сводка за период
  python3 extern_monitor.py action                    # ДО, требующие действий
  python3 extern_monitor.py drafts                    # незавершённые черновики
  python3 extern_monitor.py events [last_seq_id]      # новые события ДО
  python3 extern_monitor.py certs                     # сертификаты КЭП

Использование из других скриптов:
  from extern_monitor import check_reporting_deadlines
  from extern_api import KonturExternClient
  client = KonturExternClient()
  result = check_reporting_deadlines(client, days_ahead=5)
"""

import sys
import json
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from extern_api import KonturExternClient

# ─── Константы ────────────────────────────────────────────────────────────────

STATUS_OK = {
    "accepted",
    "accepted-after-deadline",
    "manually-accepted",
}

STATUS_PROBLEM = {
    "rejected",
    "overdue",
    "not-sent",
    "sent-after-deadline",
    "correction-rejected",
}

# Русские названия органов из типа ДО
AUTHORITY_NAMES = {
    "fns":   "ФНС",
    "sfr":   "СФР",
    "stat":  "Росстат",
    "fsrar": "ФСРАР",
    "rpn":   "РПН",
    "mvd":   "МВД",
}

# Срок ответа на требование ФНС в рабочих днях
DEMAND_RESPONSE_DAYS = 6

# Статусы ДО, означающие что ДО завершён
FINISHED_STATUSES = {
    "urn:docflow:status:finished",
    "urn:docflow:status:accepted",
    "urn:docflow:status:rejected",
}


# ─── Вспомогательные функции ─────────────────────────────────────────────────

def _today() -> datetime.date:
    return datetime.date.today()


def _parse_date(s: str) -> datetime.date | None:
    """Парсинг даты из строки формата ISO или DD.MM.YYYY."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(s[:len(fmt)], fmt).date()
        except ValueError:
            continue
    # Попытка обрезать до 10 символов (дата без времени)
    try:
        return datetime.date.fromisoformat(s[:10])
    except Exception:
        return None


def _authority_from_docflow_type(doc_type: str) -> str:
    """Определить контролирующий орган из типа ДО."""
    doc_type = (doc_type or "").lower()
    for key, name in AUTHORITY_NAMES.items():
        if key in doc_type:
            return name
    return "—"


def _workdays_since(from_date: datetime.date, to_date: datetime.date = None) -> int:
    """Количество рабочих дней от from_date до to_date (по умолч. сегодня)."""
    if to_date is None:
        to_date = _today()
    if to_date <= from_date:
        return 0
    count, current = 0, from_date
    while current < to_date:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return count


def _workdays_until(target: datetime.date, from_date: datetime.date = None) -> int:
    """Количество рабочих дней от from_date до target."""
    if from_date is None:
        from_date = _today()
    return _workdays_since(from_date, target)


def _fmt_date(d) -> str:
    if not d:
        return "—"
    if isinstance(d, datetime.date):
        return d.strftime("%d.%m.%Y")
    return str(d)


# ─── Функции мониторинга ──────────────────────────────────────────────────────

def check_reporting_deadlines(client: KonturExternClient, days_ahead: int = 7) -> list:
    """
    Формы отчётности, дедлайн которых наступает в ближайшие days_ahead дней.

    Делает 1 запрос к API (таблица отчётности за текущий квартал).

    Поля результата:
      form_name, knd, authority, deadline (date), days_left, status, is_sent
    """
    today   = _today()
    cutoff  = today + datetime.timedelta(days=days_ahead)
    year    = today.year
    quarter = (today.month - 1) // 3 + 1

    forms  = client.get_forms_table(year=year, quarter=quarter)
    result = []

    for form in forms:
        deadline_str = form.get("deadline") or form.get("period-end")
        deadline     = _parse_date(deadline_str)
        if not deadline:
            continue
        if not (today <= deadline <= cutoff):
            continue

        status    = form.get("status", "unknown")
        is_sent   = status not in ("not-sent", "overdue", "unknown")
        days_left = (deadline - today).days

        result.append({
            "form_name": form.get("form-name") or form.get("name", "—"),
            "knd":       form.get("knd", "—"),
            "authority": form.get("authority", "—"),
            "deadline":  deadline,
            "days_left": days_left,
            "status":    status,
            "is_sent":   is_sent,
        })

    result.sort(key=lambda x: x["deadline"])
    return result


def check_overdue_reports(client: KonturExternClient) -> list:
    """
    Формы, дедлайн которых прошёл и статус не «принят».

    Делает 1 запрос к API (таблица отчётности за текущий квартал).

    Поля результата:
      form_name, knd, authority, deadline (date), days_overdue, status
    """
    today   = _today()
    year    = today.year
    quarter = (today.month - 1) // 3 + 1

    forms  = client.get_forms_table(year=year, quarter=quarter)
    result = []

    for form in forms:
        status = form.get("status", "unknown")
        if status in STATUS_OK:
            continue

        deadline_str = form.get("deadline") or form.get("period-end")
        deadline     = _parse_date(deadline_str)
        if not deadline or deadline >= today:
            continue

        result.append({
            "form_name":    form.get("form-name") or form.get("name", "—"),
            "knd":          form.get("knd", "—"),
            "authority":    form.get("authority", "—"),
            "deadline":     deadline,
            "days_overdue": (today - deadline).days,
            "status":       status,
        })

    result.sort(key=lambda x: x["days_overdue"], reverse=True)
    return result


def check_pending_demands(client: KonturExternClient) -> list:
    """
    Входящие требования ФНС, по которым не отправлен ответ.

    Срок ответа на требование — 6 рабочих дней.
    Делает столько запросов к API, сколько нужно для обхода пагинации.

    Поля результата:
      docflow_id, received_at (date), days_elapsed, days_remaining,
      is_critical, status, reply_links
    """
    critical_threshold = client.settings.get("demand_critical_days", 2)
    today  = _today()

    docflows = client.get_all_docflows(
        doc_type="fns534-demand",
        incoming=True,
        finished=False,
    )

    result = []
    for df in docflows:
        status = df.get("status", "")

        # Фильтруем уже завершённые (проверка через URN)
        if any(s in status for s in ["finished", "accepted", "closed"]):
            continue

        # Проверяем, есть ли в ДО ответные документы — если есть, требование «отвечено»
        links       = df.get("links", [])
        reply_links = [lnk["href"] for lnk in links if lnk.get("rel") == "reply"]
        # Если ссылок-ответа нет — не требуется действий именно по требованию.
        # Но нас интересуют ДО, где ещё не подтверждена получения — смотрим по статусу.

        date_str    = df.get("date") or df.get("last-change-date") or ""
        received_at = _parse_date(date_str)
        if not received_at:
            continue

        days_elapsed   = _workdays_since(received_at, today)
        days_remaining = max(0, DEMAND_RESPONSE_DAYS - days_elapsed)
        is_critical    = days_remaining <= critical_threshold

        result.append({
            "docflow_id":    df.get("id", "—"),
            "received_at":   received_at,
            "days_elapsed":  days_elapsed,
            "days_remaining": days_remaining,
            "is_critical":   is_critical,
            "status":        status,
            "reply_links":   reply_links,
        })

    # Сначала самые критичные
    result.sort(key=lambda x: x["days_remaining"])
    return result


def get_accepted_reports_summary(
    client: KonturExternClient, year: int, quarter: int = None
) -> dict:
    """
    Сводка по статусам отчётности за период.

    year    — отчётный год
    quarter — квартал 1-4 или None для всего года

    Делает 1 запрос к API.

    Возвращает:
      period, total, accepted, rejected, not_sent, overdue, other, forms_detail
    """
    forms = client.get_forms_table(year=year, quarter=quarter)

    counts = {
        "accepted": 0, "rejected": 0, "not_sent": 0, "overdue": 0, "other": 0
    }
    for form in forms:
        st = form.get("status", "other")
        if st in ("accepted", "accepted-after-deadline", "manually-accepted"):
            counts["accepted"] += 1
        elif st in ("rejected", "correction-rejected"):
            counts["rejected"] += 1
        elif st == "not-sent":
            counts["not_sent"] += 1
        elif st in ("overdue", "sent-after-deadline"):
            counts["overdue"] += 1
        else:
            counts["other"] += 1

    period_label = f"{year} Q{quarter}" if quarter else str(year)

    return {
        "period":       period_label,
        "total":        len(forms),
        "accepted":     counts["accepted"],
        "rejected":     counts["rejected"],
        "not_sent":     counts["not_sent"],
        "overdue":      counts["overdue"],
        "other":        counts["other"],
        "forms_detail": forms,
    }


def get_docflows_requiring_action(client: KonturExternClient) -> list:
    """
    Незавершённые документообороты с ссылками типа 'reply' —
    контролирующий орган ждёт ответного документа.

    Делает столько запросов, сколько нужно для обхода пагинации.

    Поля результата:
      docflow_id, doc_type, authority, status, date (date), reply_links
    """
    docflows = client.get_all_docflows(finished=False)
    result   = []

    for df in docflows:
        links       = df.get("links", [])
        reply_links = [lnk["href"] for lnk in links if lnk.get("rel") == "reply"]
        if not reply_links:
            continue

        doc_type  = df.get("type", "")
        authority = _authority_from_docflow_type(doc_type)
        date_str  = df.get("date") or df.get("last-change-date") or ""

        result.append({
            "docflow_id":  df.get("id", "—"),
            "doc_type":    doc_type,
            "authority":   authority,
            "status":      df.get("status", "—"),
            "date":        _parse_date(date_str),
            "reply_links": reply_links,
        })

    result.sort(key=lambda x: x["date"] or datetime.date.min)
    return result


def check_open_drafts(client: KonturExternClient) -> list:
    """
    Черновики (незавершённые отправки).

    Поля результата:
      draft_id, status, sender, recipient, created_at
    """
    drafts = client.get_drafts()
    result = []

    for d in drafts:
        meta = d.get("meta", {})
        result.append({
            "draft_id":   d.get("id", "—"),
            "status":     d.get("status", "—"),
            "created_at": _parse_date(d.get("creation-date", "")),
            "sender":     meta.get("sender", {}).get("inn", "—"),
            "recipient":  str(meta.get("recipient", {}) or "—"),
        })

    result.sort(key=lambda x: x["created_at"] or datetime.date.min)
    return result


def get_new_events(client: KonturExternClient, last_seq_id: str = None) -> dict:
    """
    Новые события в документооборотах с момента last_seq_id.

    Если last_seq_id не указан — вернёт последние 100 событий.

    Используется для инкрементального отслеживания изменений без
    полного перебора всех ДО.

    Возвращает:
      events_count     — количество новых событий
      last_event_id    — ID для следующего вызова (сохранить в MEMORY.md)
      docflows_changed — список ДО с изменениями
    """
    data = client.get_events(take=100, seq_id=last_seq_id)

    docflows_changed = data.get("docflows-with-events", [])
    return {
        "events_count":     len(docflows_changed),
        "first_event_id":   data.get("first-event-id"),
        "last_event_id":    data.get("last-event-id"),
        "docflows_changed": [
            {
                "docflow_id": df.get("id", "—"),
                "type":       df.get("type", "—"),
                "status":     df.get("status", "—"),
                "authority":  _authority_from_docflow_type(df.get("type", "")),
            }
            for df in docflows_changed
        ],
    }


def get_certificates_info(client: KonturExternClient) -> list:
    """
    Сертификаты КЭП учётной записи с проверкой сроков действия.

    Поля результата:
      fio, inn, thumbprint, valid_from (date), valid_to (date),
      days_until_expiry, is_expired, is_expiring_soon
    """
    certs  = client.get_certificates()
    today  = _today()
    result = []

    for c in certs:
        valid_to      = _parse_date(c.get("valid-to", ""))
        valid_from    = _parse_date(c.get("valid-from", ""))
        days_left     = (valid_to - today).days if valid_to else None
        is_expired    = days_left is not None and days_left < 0
        is_expiring   = days_left is not None and 0 <= days_left <= 30

        result.append({
            "fio":              c.get("fio", "—"),
            "inn":              c.get("inn", "—"),
            "thumbprint":       c.get("content", {}).get("thumbprint", "—"),
            "valid_from":       valid_from,
            "valid_to":         valid_to,
            "days_until_expiry": days_left,
            "is_expired":       is_expired,
            "is_expiring_soon": is_expiring,
        })

    return result


def poll_ion_result(client: KonturExternClient, docflow_id: str) -> dict:
    """
    Проверить статус ранее отправленного ИОН-запроса и получить результат.

    docflow_id — ID документооборота из предыдущего запроса сверки

    Возвращает:
      status ('pending' | 'ready' | 'error'), message, documents
    """
    df         = client.get_docflow(docflow_id)
    status_urn = df.get("status", "")

    if any(s in status_urn for s in ["sent", "processing", "pending", "delivered"]):
        return {
            "status":  "pending",
            "message": f"Запрос обрабатывается ФНС. Статус: {status_urn}. Срок ответа до 5 рабочих дней.",
        }

    documents = df.get("documents", {}).get("docflow-document-list", [])
    doc_info  = []
    for doc in documents:
        doc_info.append({
            "document_id": doc.get("id"),
            "type":        doc.get("description", {}).get("type", ""),
            "filename":    doc.get("description", {}).get("filename", ""),
        })

    return {
        "status":     "ready",
        "message":    f"Ответ от ФНС получен. Статус: {status_urn}",
        "docflow_id": docflow_id,
        "documents":  doc_info,
    }


# ─── Форматирование для CLI-вывода ────────────────────────────────────────────

def _print_deadlines(items: list, days_ahead: int) -> None:
    if not items:
        print(f"Нет форм с дедлайном в ближайшие {days_ahead} дней.")
        return
    print(f"Дедлайны в ближайшие {days_ahead} дней ({len(items)} форм):\n")
    for f in items:
        mark = "✓ отправлена" if f["is_sent"] else "✗ НЕ отправлена"
        print(f"  [{mark}] {f['form_name']} (КНД {f['knd']})")
        print(f"     Орган: {f['authority']}  |  "
              f"Дедлайн: {_fmt_date(f['deadline'])}  |  "
              f"Осталось: {f['days_left']} дн.  |  "
              f"Статус: {f['status']}")


def _print_overdue(items: list) -> None:
    if not items:
        print("Просроченных отчётов не обнаружено.")
        return
    print(f"ПРОСРОЧЕННЫЕ ОТЧЁТЫ ({len(items)}):\n")
    for f in items:
        print(f"  ⚠  {f['form_name']} (КНД {f['knd']})")
        print(f"     Орган: {f['authority']}  |  "
              f"Дедлайн был: {_fmt_date(f['deadline'])}  |  "
              f"Просрочено на: {f['days_overdue']} дн.  |  "
              f"Статус: {f['status']}")


def _print_demands(items: list) -> None:
    if not items:
        print("Требований ФНС без ответа не найдено.")
        return
    print(f"ТРЕБОВАНИЯ ФНС БЕЗ ОТВЕТА ({len(items)}):\n")
    for d in items:
        level = "🔴 КРИТИЧНО" if d["is_critical"] else "🟡 Внимание"
        print(f"  {level}  ДО: {d['docflow_id']}")
        print(f"     Получено: {_fmt_date(d['received_at'])}  |  "
              f"Прошло: {d['days_elapsed']} р.дн.  |  "
              f"Осталось: {d['days_remaining']} р.дн. из {DEMAND_RESPONSE_DAYS}")
        if d["reply_links"]:
            print(f"     Ссылок для ответа: {len(d['reply_links'])}")


def _print_summary(s: dict) -> None:
    print(f"Сводка по отчётности за {s['period']}:\n")
    print(f"  Всего форм      : {s['total']}")
    print(f"  Принято         : {s['accepted']}")
    print(f"  Отклонено       : {s['rejected']}")
    print(f"  Не отправлено   : {s['not_sent']}")
    print(f"  Просрочено      : {s['overdue']}")
    if s["other"]:
        print(f"  Прочие          : {s['other']}")


def _print_action_required(items: list) -> None:
    if not items:
        print("Документооборотов, требующих действий, не найдено.")
        return
    print(f"ТРЕБУЮТ ОТВЕТНЫХ ДОКУМЕНТОВ ({len(items)} ДО):\n")
    for df in items:
        print(f"  ДО: {df['docflow_id']}")
        print(f"     Орган: {df['authority']}  |  "
              f"Тип: {df['doc_type']}  |  "
              f"Статус: {df['status']}  |  "
              f"Дата: {_fmt_date(df['date'])}")
        print(f"     Ссылок для ответа: {len(df['reply_links'])}")


def _print_drafts(items: list) -> None:
    if not items:
        print("Незавершённых черновиков не найдено.")
        return
    print(f"НЕЗАВЕРШЁННЫЕ ЧЕРНОВИКИ ({len(items)}):\n")
    for d in items:
        print(f"  ID: {d['draft_id']}  |  Статус: {d['status']}  |  "
              f"Создан: {_fmt_date(d['created_at'])}")


def _print_events(data: dict) -> None:
    print(f"НОВЫЕ СОБЫТИЯ ДО: {data['events_count']}")
    if data.get("last_event_id"):
        print(f"Последний seq_id: {data['last_event_id']}  (сохраните для следующего запроса)")
    if data["docflows_changed"]:
        print()
        for df in data["docflows_changed"]:
            print(f"  ДО {df['docflow_id']}  "
                  f"Орган: {df['authority']}  "
                  f"Статус: {df['status']}")


def _print_certs(items: list) -> None:
    if not items:
        print("Сертификатов КЭП не найдено.")
        return
    print(f"СЕРТИФИКАТЫ КЭП ({len(items)}):\n")
    for c in items:
        if c["is_expired"]:
            mark = "🔴 ИСТЁК"
        elif c["is_expiring_soon"]:
            mark = f"🟡 истекает через {c['days_until_expiry']} дн."
        else:
            days = c["days_until_expiry"]
            mark = f"✓ действителен ещё {days} дн." if days is not None else "✓"
        print(f"  {mark}")
        print(f"     {c['fio']}  ИНН: {c['inn']}")
        print(f"     Действителен: {_fmt_date(c['valid_from'])} — {_fmt_date(c['valid_to'])}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd    = sys.argv[1] if len(sys.argv) > 1 else "deadlines"
    client = KonturExternClient()

    if cmd == "deadlines":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        _print_deadlines(check_reporting_deadlines(client, days), days)

    elif cmd == "overdue":
        _print_overdue(check_overdue_reports(client))

    elif cmd == "demands":
        _print_demands(check_pending_demands(client))

    elif cmd == "summary":
        year    = int(sys.argv[2]) if len(sys.argv) > 2 else datetime.date.today().year
        quarter = int(sys.argv[3]) if len(sys.argv) > 3 else None
        _print_summary(get_accepted_reports_summary(client, year, quarter))

    elif cmd == "action":
        _print_action_required(get_docflows_requiring_action(client))

    elif cmd == "drafts":
        _print_drafts(check_open_drafts(client))

    elif cmd == "events":
        seq_id = sys.argv[2] if len(sys.argv) > 2 else None
        _print_events(get_new_events(client, seq_id))

    elif cmd == "certs":
        _print_certs(get_certificates_info(client))

    else:
        print(f"Неизвестная команда: {cmd}")
        print("Команды: deadlines [N], overdue, demands, summary [year] [quarter], "
              "action, drafts, events [seq_id], certs")
        sys.exit(1)
