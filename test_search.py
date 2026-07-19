"""
test_search.py v3.4
Тестирование поиска + детального парсинга + document_processor.
"""

import sys
import json
import re
from pathlib import Path

project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.searcher import create_searcher
from core.detailed_parser import DetailedParser
from core.tender_cache import TenderCache


def clean_law_type(raw_law: str) -> str:
    if not raw_law:
        return ""
    match = re.search(r"(\d+)", str(raw_law))
    return match.group(1) if match else raw_law


def main():
    print("=" * 70)
    print("TENDER BOT - TEST SEARCH v3.4 (DOCUMENT PROCESSOR + LLM)")
    print("=" * 70)

    cache = TenderCache(Path("data/tender_cache.db"))
    parser = DetailedParser(cache=cache)
    searcher = create_searcher(parsing_only=True)

    # === Поиск ===
    print("" + "=" * 70)
    print("Starting search...")
    print("=" * 70)

    results = searcher.search_and_save(
        output_file="test_results.json",
        max_pages=2,
        max_results=10,
    )

    print(f"TOTAL FOUND: {len(results)} tenders")

    if not results:
        print("Ничего не найдено.")
        return

    # === Детальный парсинг ===
    print("" + "=" * 70)
    print("DETAILED PARSING (first 5 tenders)")
    print("=" * 70)

    detailed_results = []
    law_counts = {"223": 0, "44": 0, "other": 0}
    stats = {
        "total_docs": 0,
        "total_text_len": 0,
        "llm_success": 0,
        "pdf_zero": 0,
        "view_html_filtered": 0,
    }

    for i, tender in enumerate(results[:5], 1):
        print(f"--- [{i}/5] {tender['tender_id']} ---")
        raw_law = tender.get("law", "")
        law_type = clean_law_type(raw_law)
        law_counts[law_type if law_type in ("223", "44") else "other"] += 1
        print(f"  ⚖️ Raw law: '{raw_law}' → Clean: '{law_type}'")

        if law_type == "223":
            common_info_url = f"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber={tender['tender_id']}"
        elif law_type == "44":
            common_info_url = f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={tender['tender_id']}"
        else:
            common_info_url = tender.get("url", "")

        detail = parser.parse_from_url(
            reg_number=tender["tender_id"],
            law_type=law_type,
            common_info_url=common_info_url,
            notice_guid="",
        )

        if detail:
            print(f"  ✅ Парсинг успешен")
            print(f"  📌 {detail.purchase_name[:60]}...")
            print(f"  🏢 {detail.customer_name[:50]}...")
            print(f"  📍 Регион: {detail.customer_region}")
            print(f"  📧 Email: {detail.contact_email or 'нет'}")
            print(f"  ⏰ Дедлайн: {detail.deadline_date}")
            print(f"  📄 Документов: {len(detail.documents)}")
            print(f"  📝 Текст документов: {len(detail.documents_text)} символов")
            print(f"  🚫 Отменена: {detail.is_cancelled}")
            print(f"  📋 Протоколы: {detail.has_protocols}")
            print(f"  📜 Договор: {detail.has_contract}")

            stats["total_docs"] += len(detail.documents)
            stats["total_text_len"] += len(detail.documents_text)

            # Проверка файлов
            if detail.documents:
                print(f"  📎 Файлы:")
                active_count = 0
                inactive_count = 0
                for doc in detail.documents[:7]:
                    active_mark = "✓" if doc.is_active else "✗"
                    if doc.is_active:
                        active_count += 1
                    else:
                        inactive_count += 1
                    print(f"      [{active_mark}] [{doc.file_type or '?'}] {doc.name[:50]}")
                    # Проверка view.html
                    if "view.html" in doc.url:
                        stats["view_html_filtered"] += 1
                        print(f"          ⚠️ view.html НЕ ОТФИЛЬТРОВАН!")
                if len(detail.documents) > 7:
                    print(f"      ... и ещё {len(detail.documents) - 7}")
                print(f"      📊 Активных: {active_count}, Неактивных: {inactive_count}")

            # Проверка PDF
            if detail.documents_text and len(detail.documents_text) < 100 and any(d.file_type == "pdf" for d in detail.documents):
                stats["pdf_zero"] += 1
                print(f"      ⚠️ PDF даёт 0 символов — установи: pip install pdfplumber")

            detailed_results.append(detail.to_dict())
        else:
            print(f"  ❌ Парсинг не удался")

        if i < 5:
            import time
            time.sleep(2)

    # === Статистика ===
    print("" + "=" * 70)
    print("STATISTICS")
    print("=" * 70)
    print(f"  223-ФЗ: {law_counts['223']}, 44-ФЗ: {law_counts['44']}, Other: {law_counts['other']}")
    print(f"  Всего документов: {stats['total_docs']}")
    print(f"  Средний текст на тендер: {stats['total_text_len'] // max(len(detailed_results), 1)} символов")
    print(f"  PDF с 0 символов: {stats['pdf_zero']}")
    print(f"  view.html не отфильтрован: {stats['view_html_filtered']}")

    # === Сохранение ===
    with open("test_detailed_results.json", "w", encoding="utf-8") as f:
        json.dump(detailed_results, f, ensure_ascii=False, indent=2)

    print(f"💾 Сохранено: test_detailed_results.json ({len(detailed_results)} тендеров)")

    # === Кэш ===
    stats_cache = cache.get_stats()
    print(f"📊 Кэш: {stats_cache['purchase_states']['total']} записей, {stats_cache['db_size_mb']} MB")


if __name__ == "__main__":
    main()