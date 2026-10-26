"""
reconcile.py — диагностика расхождений и восстановление учёта ООО «РАМЕВА»

  entities     — дублирующиеся сущности (физлица в нескольких ролях, дубли по ИНН)
  phantoms     — документы-фантомы (номера типа 12__1, дубли по сумме+дате)
  bank-vs-docs — транзакции без документа и документы без оплаты
  loans        — займы от учредителя (счета 66/67)
  person <ФИО> — сводная карточка физлица во всех ролях
  fix-entity <keep_id> <merge_id> [--apply] — объединить дубли
"""

import sqlite3, argparse, sys, re
from pathlib import Path
from datetime import date, timedelta
from difflib import SequenceMatcher

DEFAULT_DB = Path("~/.hermes/skills/rameva/rameva.db").expanduser()

# ── Нормализация имён ────────────────────────────────────────────────────────
_STRIP = re.compile(
    r"(ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ"
    r"|Общество\s+с\s+ограниченной\s+ответственностью"
    r"|АКЦИОНЕРНОЕ\s+ОБЩЕСТВО|ПУБЛИЧНОЕ\s+АКЦИОНЕРНОЕ\s+ОБЩЕСТВО"
    r"|ООО\b|АО\b|ПАО\b|ИП\b)",
    re.IGNORECASE,
)
def normalize(name: str) -> str:
    return _STRIP.sub("", str(name)).strip().strip('"').strip().lower().replace(" ", "")

def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    return SequenceMatcher(None, na, nb).ratio() if na and nb else 0.0

# ── Известные роли физлиц ────────────────────────────────────────────────────
KNOWN_PERSONS = [{
    "canonical": "Бунин Сергей Юрьевич",
    "inn": "482505236204",
    "roles": [
        {"role": "employee",    "variants": ["БУНИН СЕРГЕЙ ЮРЬЕВИЧ", "Бунин Сергей Юрьевич"], "accounts": ["70"]},
        {"role": "founder",     "variants": ["Бунин Сергей Юрьевич"],                          "accounts": ["80", "84"]},
        {"role": "lender",      "variants": ["Бунин Сергей Юрьевич", "БУНИН СЕРГЕЙ ЮРЬЕВИЧ"],  "accounts": ["66", "67"]},
        {"role": "accountable", "variants": ["Бунин Сергей Юрьевич"],                          "accounts": ["71"]},
    ]
}]

PHANTOM_RE = [re.compile(p) for p in [r"^\d+__\d+$", r"^.*_copy_\d+$", r"^.*\(\d+\)$"]]

def db(path: Path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con

def table_exists(con, name):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

# ── 1. ENTITIES ──────────────────────────────────────────────────────────────
def cmd_entities(db_path: Path):
    con = db(db_path)
    cps = [dict(r) for r in con.execute("SELECT id,name,inn,type FROM counterparties").fetchall()]

    print("\n" + "="*64)
    print("  ДИАГНОСТИКА: ДУБЛИРУЮЩИЕСЯ СУЩНОСТИ")
    print("="*64)

    found = False

    # A. Одинаковый ИНН у разных записей
    inn_map = {}
    for cp in cps:
        if cp["inn"]:
            inn_map.setdefault(cp["inn"], []).append(cp)
    for inn, group in inn_map.items():
        if len(group) > 1:
            found = True
            print(f"\n  🔴 ОДИНАКОВЫЙ ИНН {inn} у {len(group)} записей:")
            for e in group:
                print(f"     #{e['id']} [{e['type']}] {e['name']}")
            ids = " ".join(str(e["id"]) for e in group)
            print(f"     → reconcile.py fix-entity {ids} --apply")

    # B. Похожие имена (только когда ИНН не заданы / совпадают)
    for i in range(len(cps)):
        for j in range(i+1, len(cps)):
            a, b = cps[i], cps[j]
            if a["inn"] and b["inn"] and a["inn"] != b["inn"]:
                continue   # Разные ИНН — разные юрлица, пропускаем
            sim = similarity(a["name"], b["name"])
            if sim >= 0.93:
                found = True
                print(f"\n  ⚠️  ПОХОЖИЕ ИМЕНА ({sim:.0%}), ИНН неизвестны/совпадают:")
                print(f"     #{a['id']} [{a['type']}] {a['name']}")
                print(f"     #{b['id']} [{b['type']}] {b['name']}")
                print(f"     → reconcile.py fix-entity {a['id']} {b['id']} --apply")

    # C. Известные физлица в нескольких ролях
    for person in KNOWN_PERSONS:
        matches = []
        for cp in cps:
            for role_info in person["roles"]:
                if cp["name"].strip() in role_info["variants"]:
                    matches.append({"id": cp["id"], "name": cp["name"],
                                    "type": cp["type"], "role": role_info["role"]})
        if len(set(m["id"] for m in matches)) > 1 or len(set(m["role"] for m in matches)) > 1:
            found = True
            roles = list({m["role"] for m in matches})
            print(f"\n  ℹ️  ФИЗЛИЦО В НЕСКОЛЬКИХ РОЛЯХ: {person['canonical']}  ИНН={person['inn']}")
            print(f"     Роли: {', '.join(roles)}")
            ids = list({m['id'] for m in matches})
            for eid in ids:
                ename = next(m["name"] for m in matches if m["id"] == eid)
                eroles = [m["role"] for m in matches if m["id"] == eid]
                print(f"     #{eid} [{', '.join(eroles)}] {ename}")
            if len(ids) > 1:
                print(f"     ⚠️  ДУБЛИ — разные ID для одного человека!")
                print(f"     → reconcile.py fix-entity {ids[0]} {ids[1]} --apply")
            else:
                print(f"     ✅ Одна запись, несколько ролей — НОРМАЛЬНО")
                print(f"     → reconcile.py person \"{person['canonical']}\"")

    if not found:
        print("  ✅ Дублирующихся сущностей не обнаружено.")
    print()
    con.close()

# ── 2. PHANTOMS ──────────────────────────────────────────────────────────────
def cmd_phantoms(db_path: Path):
    con = db(db_path)
    docs = [dict(r) for r in con.execute(
        "SELECT id,type,number,date,amount,counterparty_id,status FROM documents"
    ).fetchall()]

    print("\n" + "="*64)
    print("  ДИАГНОСТИКА: ДОКУМЕНТЫ-ФАНТОМЫ")
    print("="*64)

    phantoms = [d for d in docs if d["number"] and
                any(p.match(str(d["number"])) for p in PHANTOM_RE)]

    # Дубли: одинаковые тип+дата+сумма+контрагент
    seen, dupes = {}, []
    for d in docs:
        key = (d["type"], d["date"], d["amount"], d["counterparty_id"])
        if key in seen:
            dupes.append((seen[key], d))
        else:
            seen[key] = d

    broken = [d for d in docs if not d["date"] or not d["amount"]]

    if not phantoms and not dupes and not broken:
        print("  ✅ Фантомных документов не найдено.")
    if phantoms:
        print(f"\n  🔴 ПОДОЗРИТЕЛЬНЫЕ НОМЕРА ({len(phantoms)} шт.) — паттерн 12__1, 17__1:")
        for d in phantoms:
            print(f"     id={d['id']} №{d['number']} {d['type']} {d['date']} {d['amount'] or 0:,.0f} ₽")
        ids = ",".join(str(d["id"]) for d in phantoms)
        print(f"\n     Пометить как отклонённые:")
        print(f"     UPDATE documents SET status='rejected' WHERE id IN ({ids});")
    if dupes:
        print(f"\n  ⚠️  ВОЗМОЖНЫЕ ДУБЛИ по (тип+дата+сумма): {len(dupes)} пар")
        for a, b in dupes[:5]:
            print(f"     №{a['number']} и №{b['number']} | {a['type']} {a['date']} {a['amount']:,.0f} ₽")
    if broken:
        print(f"\n  ⚠️  ДОКУМЕНТЫ БЕЗ СУММЫ ИЛИ ДАТЫ: {len(broken)} шт.")
    print()
    con.close()

# ── 3. BANK-VS-DOCS ──────────────────────────────────────────────────────────
def cmd_bank_vs_docs(db_path: Path, period: str = None):
    con = db(db_path)
    where, params = "", []
    if period:
        y, m = period.split("-")
        where, params = "AND t.value_date LIKE ?", [f"{y}-{m}-%"]

    unlinked_tx = con.execute(f"""
        SELECT t.id, t.value_date, t.direction, t.amount, t.category,
               t.counterparty, t.purpose
        FROM bank_transactions t
        WHERE t.linked_doc_id IS NULL
          AND t.category NOT IN ('salary','alimony','enp','sfr_ns','bank_fee','ndfl')
          {where}
        ORDER BY t.value_date DESC
    """, params).fetchall()

    unlinked_docs = con.execute("""
        SELECT d.id, d.type, d.number, d.date, d.amount, d.direction,
               d.status, c.name as cp_name
        FROM documents d LEFT JOIN counterparties c ON d.counterparty_id = c.id
        WHERE d.linked_tx_id IS NULL AND d.status IN ('signed','sent')
          AND d.type IN ('upd_out','act_out','upd_in','act_in','payment_out','payment_in')
        ORDER BY d.date DESC LIMIT 20
    """).fetchall()

    w = 60
    print("\n" + "="*w)
    print(f"  СВЕРКА БАНК ↔ ДОКУМЕНТЫ{f'  [{period}]' if period else ''}")
    print("="*w)
    print(f"\n  Транзакции без документа: {len(unlinked_tx)}")
    for t in unlinked_tx[:10]:
        arrow = "←" if t["direction"]=="IN" else "→"
        print(f"  {t['value_date']} {arrow} {t['amount']:>10,.2f}  [{t['category'] or '?'}]")
        print(f"               {(t['counterparty'] or '')[:45]}")
    print(f"\n  Документы без оплаты: {len(unlinked_docs)}")
    for d in unlinked_docs[:10]:
        arrow = "←" if d["direction"]=="IN" else "→"
        print(f"  {d['date']} {arrow} {d['amount'] or 0:>10,.2f}  {d['type']} №{d['number']}")
        print(f"               {(d['cp_name'] or '')[:45]}")
    print("="*w + "\n")
    con.close()

# ── 4. LOANS ─────────────────────────────────────────────────────────────────
def cmd_loans(db_path: Path):
    con = db(db_path)
    print("\n" + "="*60)
    print("  ЗАЙМЫ ОТ УЧРЕДИТЕЛЯ  |  Бунин С.Ю.")
    print("="*60)

    # Из ОСВ
    if table_exists(con, "osv_snapshots"):
        snap = con.execute(
            "SELECT id, generated_date FROM osv_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if snap:
            print(f"\n  Данные ОСВ от {snap['generated_date']}:")
            for acc, label in [("66","Краткосрочные 66"), ("66.02","└─ 66.02"),
                                ("67","Долгосрочные 67"),  ("67.01","└─ 67.01")]:
                r = con.execute("""SELECT close_debit, close_credit FROM osv_rows
                    WHERE snapshot_id=? AND code=?""", (snap["id"], acc)).fetchone()
                if r and (r["close_debit"] or r["close_credit"]):
                    bal = (r["close_credit"] or 0) - (r["close_debit"] or 0)
                    if bal > 0:
                        print(f"  {label}: остаток долга {bal:>12,.2f} ₽")
    else:
        print("\n  ⚠️  ОСВ не загружена. Загрузите: python3 parse_osv.py ОСВ.csv")

    # Транзакции
    txs = con.execute("""
        SELECT value_date, direction, amount, purpose
        FROM bank_transactions
        WHERE category IN ('loan_in','loan_out')
           OR lower(purpose) LIKE '%займ%' OR lower(purpose) LIKE '%заем%'
           OR lower(purpose) LIKE '%беспроцентн%'
        ORDER BY value_date
    """).fetchall()

    if txs:
        print(f"\n  Транзакции по займам:")
        tin, tout = 0.0, 0.0
        for t in txs:
            arrow = "←" if t["direction"]=="IN" else "→"
            print(f"  {t['value_date']} {arrow} {t['amount']:>10,.2f}  {str(t['purpose'] or '')[:55]}")
            if t["direction"]=="IN": tin += t["amount"]
            else: tout += t["amount"]
        print(f"\n  Получено:  {tin:>12,.2f} ₽")
        print(f"  Возвращено:{tout:>12,.2f} ₽")
        print(f"  Баланс:    {tin-tout:>12,.2f} ₽  (из загруженных выписок)")
    else:
        print("\n  Транзакций по займам не найдено в загруженных выписках.")
        print("  ℹ️  Нужны выписки за 2024–2025 год.")

    print()
    print("  ⚠️  АУСН: займы НЕ доход, возврат НЕ расход. taxable=0.")
    print()
    con.close()

# ── 5. PERSON ────────────────────────────────────────────────────────────────
def cmd_person(db_path: Path, search: str):
    con = db(db_path)
    variants = [search]
    for p in KNOWN_PERSONS:
        if search.lower() in p["canonical"].lower():
            for r in p["roles"]:
                variants.extend(r["variants"])
    variants = list(dict.fromkeys(variants))

    print(f"\n{'='*64}")
    print(f"  ФИЗЛИЦО: {search.upper()}")
    print(f"  Варианты: {', '.join(set(variants))}")
    print("="*64)

    all_txs, seen_keys = [], set()
    for v in variants:
        txs = con.execute("""SELECT value_date, direction, amount, category, purpose, code_nazn
            FROM bank_transactions WHERE counterparty = ? OR counterparty LIKE ?
            ORDER BY value_date""", (v, f"%{v[:12]}%")).fetchall()
        for t in txs:
            key = (t["value_date"], t["direction"], t["amount"])
            if key not in seen_keys:
                seen_keys.add(key)
                all_txs.append(dict(t))

    roles = {}
    for t in all_txs:
        code, cat = t.get("code_nazn",""), t.get("category","")
        if code=="1" or cat=="salary":    role = "👔 Сотрудник"
        elif code=="2" or cat=="alimony": role = "⚖️  Алименты"
        elif cat in ("loan_in","loan_out") or "беспроцент" in (t.get("purpose") or "").lower():
                                          role = "🏦 Займодавец"
        else:                             role = "❓ Прочее"
        roles.setdefault(role, []).append(t)

    for role, txs in sorted(roles.items()):
        total = sum(t["amount"] for t in txs)
        print(f"\n  {role}  ({len(txs)} оп., итого {total:,.2f} ₽)")
        for t in txs:
            arrow = "→" if t["direction"]=="OUT" else "←"
            print(f"    {t['value_date']} {arrow} {t['amount']:>10,.2f}  {str(t.get('purpose') or '')[:55]}")

    doc_total = 0
    for v in variants:
        cp = con.execute("SELECT id FROM counterparties WHERE name=?", (v,)).fetchone()
        if cp:
            docs = con.execute("""SELECT type,number,date,amount,status FROM documents
                WHERE counterparty_id=? ORDER BY date DESC LIMIT 8""", (cp[0],)).fetchall()
            if docs:
                print(f"\n  Документы (запись '{v}'):")
                for d in docs:
                    print(f"    {d['date']} {d['type']} №{d['number']} {d['amount'] or 0:>10,.0f} ₽  [{d['status']}]")
                doc_total += len(docs)

    if not all_txs and not doc_total:
        print("\n  Данных нет. Загрузите выписки за нужный период.")
    print()
    con.close()

# ── 6. FIX-ENTITY ────────────────────────────────────────────────────────────
def cmd_fix_entity(db_path: Path, keep_id: int, merge_id: int, apply: bool):
    con = db(db_path)
    keep  = con.execute("SELECT * FROM counterparties WHERE id=?", (keep_id,)).fetchone()
    merge = con.execute("SELECT * FROM counterparties WHERE id=?", (merge_id,)).fetchone()
    if not keep or not merge:
        print("❌ ID не найден"); con.close(); return

    doc_cnt = con.execute("SELECT COUNT(*) FROM documents WHERE counterparty_id=?", (merge_id,)).fetchone()[0]
    ctr_cnt = 0
    if table_exists(con, "contracts_v2"):
        ctr_cnt = con.execute("SELECT COUNT(*) FROM contracts_v2 WHERE counterparty_id=?", (merge_id,)).fetchone()[0]

    tag = "[DRY-RUN] " if not apply else ""
    print(f"\n{'='*60}")
    print(f"  {tag}ОБЪЕДИНЕНИЕ ЗАПИСЕЙ")
    print(f"  СОХРАНИТЬ: #{keep['id']} {keep['name']}")
    print(f"  УДАЛИТЬ:   #{merge['id']} {merge['name']}")
    print(f"  Переедет: документов={doc_cnt}, договоров={ctr_cnt}")

    if not apply:
        print("\n  Для применения добавьте --apply")
        con.close(); return

    con.execute("UPDATE documents SET counterparty_id=? WHERE counterparty_id=?", (keep_id, merge_id))
    if table_exists(con, "contracts_v2"):
        con.execute("UPDATE contracts_v2 SET counterparty_id=? WHERE counterparty_id=?", (keep_id, merge_id))
    con.execute("DELETE FROM counterparties WHERE id=?", (merge_id,))
    con.commit()
    print("  ✅ Объединено.")
    con.close()

# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["entities","phantoms","bank-vs-docs","loans","person","fix-entity"])
    p.add_argument("args", nargs="*")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--period")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    if not args.db.exists():
        print(f"❌ БД не найдена: {args.db}"); sys.exit(1)

    if   args.command == "entities":    cmd_entities(args.db)
    elif args.command == "phantoms":    cmd_phantoms(args.db)
    elif args.command == "bank-vs-docs":cmd_bank_vs_docs(args.db, args.period)
    elif args.command == "loans":       cmd_loans(args.db)
    elif args.command == "person":
        if not args.args: print("Укажите имя"); sys.exit(1)
        cmd_person(args.db, " ".join(args.args))
    elif args.command == "fix-entity":
        if len(args.args) < 2: print("Укажите два ID"); sys.exit(1)
        cmd_fix_entity(args.db, int(args.args[0]), int(args.args[1]), args.apply)

if __name__ == "__main__":
    main()
