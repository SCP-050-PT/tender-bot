#!/usr/bin/env python3
"""
main.py
Интеграционный скрипт TENDER-BOT v2.
Пайплайн: Поиск → Детальный парсинг → LLM-анализ → Расчёт → Риски → Google Sheets

ИСПРАВЛЕНО (27.07.2026 v6.2):
  - БАГ 1: Передача tender_type_hint в analyzer для корректного определения типа
  - v6.0: Передача cities_count вместо addresses_count для СОУТ (БАГ 9.1)
  - v6.0: Передача v5.4 параметров: trip_days, is_seasonal, opr_positions, opr_persons (БАГ 9.2)
  - v6.0: _get_quantity() без addresses_count (БАГ 9.3)
  - v6.0: Колонки needs_manual_review, llm_confidence в Sheets (БАГ 9.5)
  - v6.0: Приоритизация документов перед обрезкой (БАГ 9.6)
"""

import sys
import argparse
import json
import csv
from pathlib import Path
from datetime import datetime

# Настройка логирования
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

try:
    from loguru import logger

    logger.remove()
    logger.add(
        LOG_DIR / "tender_bot.log",
        rotation="10 MB",
        retention="30 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    )
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
except ImportError:
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("tender_bot")


# === КОЛОНКИ ТАБЛИЦЫ ЗАКАЗЧИЦЫ (лист "Тендера 2026 ИИ-бот") ===
# ← v6.0: Добавлены колонки needs_manual_review, llm_confidence
SHEET_COLUMNS = [
    "ID тендера",  # A
    "Наименование услуг",  # B
    "Количество",  # C
    "Способ проведения закупки",  # D
    "НМЦК",  # E
    "Ссылка на тендер",  # F
    "ЭТП",  # G
    "Регион",  # H
    "Обеспечение заявки",  # I
    "Обеспечение контракта",  # J
    "Способ обеспечения исполнения",  # K
    "Срок подачи заявки до",  # L
    "Решение по участию",  # M
    "Цена предложения",  # N
    "Результат",  # O — бот не трогает
    "Дата заключения контракта",  # P — бот не трогает
    "Дата выполнения работ",  # Q — бот не трогает
    "Комментарии руководителя отдела по участию",  # R — бот не трогает
    "Ручная проверка",  # S ← v6.0
    "Уверенность ИИ",  # T ← v6.0
]


def _format_nmck(nmck: float) -> str:
    if not nmck or nmck == 0:
        return ""
    return f"{nmck:,.2f}".replace(",", " ").replace(".", ",")


def _format_price(price: float) -> str:
    if not price or price == 0:
        return ""
    return f"{price:,.2f}".replace(",", " ").replace(".", ",")


def _build_tender_text(detail, documents_text: str) -> str:
    parts = [
        f"НАЗВАНИЕ ЗАКУПКИ: {detail.purchase_name or detail.reg_number}",
        f"ЗАКАЗЧИК: {detail.customer_name or 'не указан'}",
        f"РЕГИОН: {detail.customer_region or 'не указан'}",
        f"АДРЕС ЗАКАЗЧИКА: {detail.customer_address or 'не указан'}",
        f"НМЦК: {detail.nmck:,.2f} ₽" if detail.nmck else "НМЦК: не указана",
        f"СПОСОБ ЗАКУПКИ: {detail.purchase_method or 'не указан'}",
        f"ЭТП: {detail.platform_name or 'не указана'}",
        f"СРОК ПОДАЧИ ЗАЯВОК: {detail.deadline_date or 'не указан'}",
        f"ТРЕБОВАНИЯ: {detail.requirements or 'не указаны'}",
        f"ОБЕСПЕЧЕНИЕ ЗАЯВКИ: {detail.application_guarantee or 'не указано'}",
        f"ОБЕСПЕЧЕНИЕ КОНТРАКТА: {detail.contract_guarantee or 'не указано'}",
        f"АДРЕСОВ ПОСТАВКИ: {detail.addresses_count or 0}",
    ]

    # ← v6.0: Приоритизация документов перед обрезкой
    if documents_text and len(documents_text) > 100:
        # Сортируем документы: ТЗ первым, контракты — в конец
        prioritized_text = _prioritize_documents(documents_text)
        parts.append(f"ТЕКСТ ДОКУМЕНТОВ (ТЗ, извещение): {prioritized_text[:12000]}")
    else:
        parts.append("ДОКУМЕНТЫ: не удалось извлечь текст")
    return "\n".join(parts)


# ← v6.0: Приоритизация документов
def _prioritize_documents(documents_text: str) -> str:
    """Сортирует документы по приоритету: ТЗ > извещение > КД > прочее > контракты."""
    lines = documents_text.split("\n")
    priority_scores = []
    for line in lines:
        score = 0
        lower = line.lower()
        if any(kw in lower for kw in ["техническое задание", "тз", "описание объекта"]):
            score = 100
        elif any(kw in lower for kw in ["извещение", "приглашение", "документация"]):
            score = 80
        elif any(kw in lower for kw in ["квалификационные", "требования", "критерии"]):
            score = 60
        elif any(kw in lower for kw in ["контракт", "договор", "проект контракта"]):
            score = 10  # Контракты — в конец
        priority_scores.append((score, line))

    priority_scores.sort(key=lambda x: x[0], reverse=True)
    return "\n".join(line for _, line in priority_scores)


def _get_quantity(analysis) -> int:
    """← v6.0: Убран addresses_count из fallback."""
    if not hasattr(analysis, "details") or analysis.details is None:
        return 1

    details = analysis.details

    if isinstance(details, dict):
        quantity = (
            details.get("rm_total")
            or details.get("points_count")
            or details.get("students_count")
            # ← v6.0: Убрано details.get("addresses_count")
            or 1
        )
        return int(quantity) if quantity else 1

    quantity = (
        getattr(details, "rm_total", None)
        or getattr(details, "points_count", None)
        or getattr(details, "students_count", None)
        # ← v6.0: Убрано getattr(details, "addresses_count", None)
        or 1
    )
    return int(quantity) if quantity else 1


def _get_guarantee_info(detail, analysis) -> tuple:
    app_guarantee = ""
    contract_guarantee = ""
    guarantee_method = ""

    if detail:
        if detail.application_guarantee:
            app_guarantee = detail.application_guarantee
        if detail.contract_guarantee:
            contract_guarantee = detail.contract_guarantee
        if detail.guarantee_method:
            guarantee_method = detail.guarantee_method

    if analysis and hasattr(analysis, "details") and analysis.details:
        details = analysis.details
        if isinstance(details, dict):
            if not app_guarantee and details.get("application_guarantee"):
                app_guarantee = details["application_guarantee"]
            if not contract_guarantee and details.get("contract_guarantee"):
                contract_guarantee = details["contract_guarantee"]
            if not guarantee_method and details.get("guarantee_method"):
                guarantee_method = details["guarantee_method"]
        else:
            if not app_guarantee and getattr(details, "application_guarantee", None):
                app_guarantee = details.application_guarantee
            if not contract_guarantee and getattr(details, "contract_guarantee", None):
                contract_guarantee = details.contract_guarantee
            if not guarantee_method and getattr(details, "guarantee_method", None):
                guarantee_method = details.guarantee_method

    if detail and detail.requirements and not app_guarantee:
        req_lower = detail.requirements.lower()
        if "не требуется" in req_lower:
            app_guarantee = "не требуется"
            contract_guarantee = "не требуется"

    if (
        analysis
        and hasattr(analysis, "comment")
        and analysis.comment
        and not app_guarantee
    ):
        comment_lower = analysis.comment.lower()
        if "не требуется" in comment_lower:
            app_guarantee = "не требуется"
            contract_guarantee = "не требуется"

    return app_guarantee, contract_guarantee, guarantee_method


def _build_sheets_row(analysis, detail, tender) -> dict:
    quantity = _get_quantity(analysis)
    app_guarantee, contract_guarantee, guarantee_method = _get_guarantee_info(
        detail, analysis
    )

    law = tender.law.replace("-FZ", "-ФЗ") if tender.law else ""
    if detail and detail.purchase_method:
        procurement = f"{detail.purchase_method}, {law}"
    else:
        procurement = law

    tender_url = ""
    if detail and detail.common_info_url:
        tender_url = detail.common_info_url
    elif hasattr(tender, "url") and tender.url:
        tender_url = tender.url
    else:
        law_num = tender.law.replace("-FZ", "") if tender.law else "44"
        if law_num == "44":
            tender_url = (
                f"https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html"
                f"?regNumber={tender.tender_id}"
            )
        elif law_num == "223":
            tender_url = (
                f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html"
                f"?searchString={tender.tender_id}"
            )
        else:
            tender_url = f"https://zakupki.gov.ru/epz/order/notice/notice{law_num}/common-info.html?regNumber={tender.tender_id}"

    # ← v6.0: Добавляем needs_manual_review и llm_confidence
    needs_manual = getattr(analysis, "needs_manual_review", False)
    llm_conf = getattr(analysis, "llm_confidence", 0.0)

    return {
        "ID тендера": tender.tender_id,
        "Наименование услуг": detail.purchase_name if detail else tender.title,
        "Количество": quantity,
        "Способ проведения закупки": procurement,
        "НМЦК": _format_nmck((detail.nmck if detail else 0) or analysis.nmck),
        "Ссылка на тендер": tender_url,
        "ЭТП": detail.platform_name if detail else (tender.etp or ""),
        "Регион": detail.customer_region if detail else (tender.region or ""),
        "Обеспечение заявки": app_guarantee,
        "Обеспечение контракта": contract_guarantee,
        "Способ обеспечения исполнения": guarantee_method,
        "Срок подачи заявки до": (
            detail.deadline_date if detail else (tender.deadline_date or "")
        ),
        "Решение по участию": analysis.decision,
        "Цена предложения": _format_price(analysis.recommended_price),
        "Результат": "",
        "Дата заключения контракта": "",
        "Дата выполнения работ": "",
        "Комментарии руководителя отдела по участию": (
            analysis._format_comment()
            if hasattr(analysis, "_format_comment")
            else analysis.comment
        ),
        "Ручная проверка": "ДА" if needs_manual else "НЕТ",  # ← v6.0
        "Уверенность ИИ": f"{llm_conf:.2f}" if llm_conf > 0 else "",  # ← v6.0
    }


def run_parse_only(max_pages: int = None, max_results: int = None):
    logger.info("=" * 60)
    logger.info("🔍 РЕЖИМ: Только парсинг (без LLM)")
    logger.info("=" * 60)

    from core.searcher import create_searcher

    searcher = create_searcher(parsing_only=True)

    output_file = f"data/search_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    results = searcher.search_and_save(
        output_file=output_file,
        max_pages=max_pages,
        max_results=max_results,
    )

    logger.info(f"✅ Найдено тендеров: {len(results)}")
    logger.info(f"💾 Сохранено в: {output_file}")
    return results


def run_analyze(
    max_pages: int = None, max_results: int = None, skip_detail: bool = False
):
    logger.info("=" * 60)
    logger.info("🤖 РЕЖИМ: Полный анализ с LLM")
    logger.info("=" * 60)

    from config.settings import settings
    from core.searcher import create_searcher
    from core.analyzer import TenderAnalyzer
    from core.detailed_parser import DetailedParser
    from core.tender_cache import TenderCache

    errors = settings.validate()
    if errors:
        logger.error("❌ Ошибки конфигурации:")
        for err in errors:
            logger.error(f"   • {err}")
        sys.exit(1)

    searcher = create_searcher(parsing_only=False)
    analyzer = TenderAnalyzer()

    cache = None
    try:
        cache_db = Path(__file__).resolve().parent / "data" / "tender_cache.db"
        cache = TenderCache(db_path=cache_db)
        logger.info(f"📂 Кэш: {cache_db}")
    except Exception as e:
        logger.warning(f"⚠️ Кэш не инициализирован: {e}")

    detailed = None
    if not skip_detail:
        try:
            detailed = DetailedParser(cache=cache)
            logger.info("📄 DetailedParser инициализирован")
        except Exception as e:
            logger.warning(f"⚠️ DetailedParser не инициализирован: {e}")
            logger.warning("   Будет использоваться упрощённый анализ (только title)")

    sheets_manager = None
    sheets_enabled = getattr(settings, "GOOGLE_SHEETS_ENABLED", True)
    if sheets_enabled:
        try:
            from core.google_sheets import get_sheets_manager

            sheets_manager = get_sheets_manager()
            logger.info("📊 Google Sheets подключен")
        except Exception as e:
            logger.warning(f"⚠️ Google Sheets не подключен: {e}")
            logger.warning("   Тендеры будут сохранены только в CSV/JSON")
    else:
        logger.info("📊 Google Sheets отключен в настройках")

    logger.info("🔍 Начинаю поиск тендеров...")
    results = []
    sheets_rows = []

    for tender in searcher.search(max_pages=max_pages, max_results=max_results):
        logger.info(f"\n{'─' * 60}")
        logger.info(f"🆔 {tender.tender_id} | {tender.law}")
        logger.info(f"📌 {tender.title[:80]}...")
        logger.info(
            f"💰 НМЦК: {tender.nmck:,.0f} ₽" if tender.nmck else "💰 НМЦК: не указана"
        )

        detail = None
        documents_text = ""
        tender_text = ""

        # === ШАГ 1: Детальный парсинг ===
        if detailed and not skip_detail:
            try:
                law_clean = tender.law.replace("-FZ", "") if tender.law else "44"
                detail = detailed.parse(
                    reg_number=tender.tender_id,
                    law_type=law_clean,
                    notice_guid=tender.notice_guid or "",
                    nmck=tender.nmck,
                )

                if detail:
                    logger.info(
                        f"   ✅ Детали получены: {detail.customer_region or 'регион не определён'}"
                    )
                    logger.info(f"   📄 Документов: {len(detail.documents)}")
                    logger.info(f"   🏢 ЭТП: {detail.platform_name or 'не определена'}")
                    logger.info(
                        f"   🔒 Обеспечение: {detail.application_guarantee or 'не указано'}"
                    )
                    documents_text = detail.documents_text or ""
                    if documents_text and len(documents_text) > 1000:
                        tender_text = documents_text
                        logger.info(
                            f"   Используется полный текст документов ({len(documents_text)} симв.)"
                        )
                    else:
                        tender_text = _build_tender_text(detail, documents_text)
                else:
                    logger.warning(f"   ⚠️ Детальный парсинг вернул None")
            except Exception as e:
                logger.error(f"   ❌ Ошибка детального парсинга: {e}")
                detail = None

        # === ШАГ 2: Fallback — упрощённый текст ===
        if not tender_text:
            tender_text = f"""
НАЗВАНИЕ ЗАКУПКИ:
{tender.title}

ЗАКАЗЧИК:
{tender.customer or 'не указан'}

РЕГИОН:
{tender.region or 'не указан'}

НМЦК:
{tender.nmck or 'не указана'}

ЗАКОН:
{tender.law}
"""
            logger.info("   ℹ️ Используется упрощённый текст (title only)")

        # === ШАГ 3: LLM-анализ ===
        try:
            tender_info = {}
            if detail:
                tender_info = {
                    "purchase_name": detail.purchase_name or tender.title,
                    "customer_name": detail.customer_name or tender.customer or "",
                    "customer_region": detail.customer_region or tender.region or "",
                    "nmck": detail.nmck or tender.nmck or 0,
                    "deadline_date": detail.deadline_date or tender.deadline_date or "",
                    "platform_name": detail.platform_name or "",
                    "requirements": detail.requirements or "",
                    "application_guarantee": detail.application_guarantee or "",
                    "contract_guarantee": detail.contract_guarantee or "",
                }

                # ← v6.0: cities_count для СОУТ (уникальные города)
                # addresses_count — для обучения всегда 1 (или из ТЗ)
                tender_type_lower = (detail.tender_type or "").lower()
                is_sout_or_combined = tender_type_lower in (
                    "sout",
                    "combined",
                    "соут",
                    "комбинированный",
                )

                # Fallback: если тип не определён — проверяем по расширенным ключевым словам
                if not tender_type_lower:
                    name_lower = (detail.purchase_name or "").lower()
                    is_sout_or_combined = any(
                        kw in name_lower
                        for kw in [
                            "соут",
                            "специальная оценка условий труда",
                            "специальная оценка труда",
                            "оценка условий труда",
                            "оценка рабочих мест",
                            "специальная оценка",
                        ]
                    )

                if is_sout_or_combined:
                    tender_info["cities_count"] = (
                        getattr(detail, "cities_count", detail.addresses_count) or 1
                    )
                    tender_info["addresses_count"] = 1  # Для обратной совместимости
                    logger.info(
                        f"[v6.1] Передано cities_count={tender_info['cities_count']} для СОУТ/combined"
                    )
                else:
                    tender_info["addresses_count"] = detail.addresses_count or 1
                # v5.2: Параметры из Detail напрямую
                if detail.students_count > 0:
                    tender_info["students_count"] = detail.students_count
                if detail.rm_total > 0:
                    tender_info["rm_total"] = detail.rm_total
                if detail.points_count > 0:
                    tender_info["points_count"] = detail.points_count

                # v5.2: Очные параметры
                if detail.has_full_time:
                    tender_info["has_full_time"] = True
                    tender_info["is_distance"] = False
                    if detail.teacher_days > 0:
                        tender_info["teacher_days"] = detail.teacher_days
                    if detail.accommodation_nights > 0:
                        tender_info["accommodation_nights"] = (
                            detail.accommodation_nights
                        )
                    if detail.transport_km > 0:
                        tender_info["transport_km"] = detail.transport_km
                    if detail.venue_rent_days > 0:
                        tender_info["venue_rent_days"] = detail.venue_rent_days
                    if detail.manikin_days > 0:
                        tender_info["manikin_days"] = detail.manikin_days

                # ← v6.0: v5.4 параметры (БАГ 9.2)
                if detail.trip_days > 0:
                    tender_info["trip_days"] = detail.trip_days
                tender_info["is_seasonal"] = getattr(detail, "is_seasonal", False)
                if detail.opr_positions > 0:
                    tender_info["opr_positions"] = detail.opr_positions
                if detail.opr_persons > 0:
                    tender_info["opr_persons"] = detail.opr_persons

                tender_info["cities_count"] = (
                    getattr(detail, "cities_count", detail.addresses_count) or 1
                )

                # Для обучения: 1 площадка = 1 адрес (логика из _count_addresses)
                if detail.tender_type == "education":
                    tender_info["addresses_count"] = 1
                else:
                    tender_info["addresses_count"] = detail.addresses_count or 1

            # === DEBUG LOG ===
            logger.info(
                f"[DEBUG] Passing to analyzer: nmck={tender.nmck}, "
                f"region={detail.customer_region if detail else tender.region}, "
                f"text_length={len(tender_text)}, "
                f"students_count={tender_info.get('students_count', 'N/A')}, "
                f"rm_total={tender_info.get('rm_total', 'N/A')}, "
                f"points_count={tender_info.get('points_count', 'N/A')}, "
                f"teacher_days={tender_info.get('teacher_days', 'N/A')}, "
                f"accommodation_nights={tender_info.get('accommodation_nights', 'N/A')}, "
                f"transport_km={tender_info.get('transport_km', 'N/A')}, "
                f"venue_rent_days={tender_info.get('venue_rent_days', 'N/A')}, "
                f"manikin_days={tender_info.get('manikin_days', 'N/A')}, "
                f"trip_days={tender_info.get('trip_days', 'N/A')}, "  # ← v6.0
                f"is_seasonal={tender_info.get('is_seasonal', 'N/A')}, "  # ← v6.0
                f"opr_positions={tender_info.get('opr_positions', 'N/A')}, "  # ← v6.0
                f"opr_persons={tender_info.get('opr_persons', 'N/A')}, "  # ← v6.0
                f"cities_count={tender_info.get('cities_count', 'N/A')}"  # ← v6.0
            )
            # === END DEBUG ===

            analysis = analyzer.analyze(
                tender_text=tender_text,
                tender_id=tender.tender_id,
                nmck=tender.nmck,
                region=detail.customer_region if detail else tender.region,
                procurement_method=detail.purchase_method if detail else tender.etp,
                etp=detail.platform_name if detail else tender.etp,
                deadline_date=detail.deadline_date if detail else tender.deadline_date,
                law_type=tender.law.replace("-FZ", "") if tender.law else "44",
                tender_info=tender_info,
            )

            # === DEBUG LOG ===
            logger.info(
                f"[DEBUG] Analysis result: type={analysis.tender_type}, "
                f"cost_price={analysis.cost_price}, "
                f"recommended_price={analysis.recommended_price}, "
                f"margin_percent={analysis.margin_percent}, "
                f"needs_manual_review={getattr(analysis, 'needs_manual_review', 'N/A')}, "  # ← v6.0
                f"llm_confidence={getattr(analysis, 'llm_confidence', 'N/A')}"  # ← v6.0
            )
            # === END DEBUG ===

            result_dict = analysis.to_dict()
            results.append(result_dict)

            # === ШАГ 4: Формирование строки таблицы ===
            row = _build_sheets_row(analysis, detail, tender)
            sheets_rows.append(row)

            # === ШАГ 4.5: Запись в Google Sheets ===
            if sheets_manager:
                try:
                    success = sheets_manager.add_tender_to_top(
                        row, check_duplicate=True
                    )
                    if success:
                        logger.info(f"   ✅ Записано в Google Sheets")
                    else:
                        logger.info(f"   ⚠️ Дубликат в Sheets — пропущено")
                except Exception as e:
                    logger.warning(f"   ⚠️ Ошибка записи в Sheets: {e}")

            # === Красивый вывод ===
            print(f"\n{'=' * 60}")
            print(f"📊 РЕЗУЛЬТАТ: {tender.tender_id}")
            print(f"{'=' * 60}")
            print(f"Тип: {analysis.tender_type}")
            print(f"НМЦК: {analysis.nmck:,.0f} ₽")
            print(f"Себестоимость: {analysis.cost_price:,.0f} ₽")
            print(f"Рекомендуемая цена: {analysis.recommended_price:,.0f} ₽")
            print(f"Маржа: {analysis.margin_percent:.1f}%")
            print(f"Риск: {analysis.risk_level} | Решение: {analysis.decision}")
            if getattr(analysis, "needs_manual_review", False):  # ← v6.0
                print(f"⚠️ ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА")
            if detail and detail.customer_region:
                print(f"Регион: {detail.customer_region}")
            if detail and detail.platform_name:
                print(f"ЭТП: {detail.platform_name}")
            print(f"{'─' * 60}")
            print(f"Комментарий:")
            comment = analysis.comment
            print(comment[:400] + "..." if len(comment) > 400 else comment)
            print(f"{'=' * 60}")

        except Exception as e:
            logger.error(f"❌ Ошибка анализа тендера {tender.tender_id}: {e}")
            continue

    # === Сохранение результатов ===
    if results:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        json_file = f"data/analysis_results_{timestamp}.json"
        Path(json_file).parent.mkdir(parents=True, exist_ok=True)
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "analysis_date": datetime.now().isoformat(),
                    "total": len(results),
                    "results": results,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        logger.info(f"\n💾 JSON сохранён: {json_file}")

        csv_file = f"data/sheets_export_{timestamp}.csv"
        with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(sheets_rows)
        logger.info(f"💾 CSV сохранён: {csv_file}")

        print(f"\n{'=' * 60}")
        print(f"📋 СВОДКА")
        print(f"{'=' * 60}")
        print(f"Всего обработано: {len(results)}")

        decisions = {}
        for r in results:
            d = r.get("decision", "unknown")
            decisions[d] = decisions.get(d, 0) + 1
        for d, count in decisions.items():
            emoji = (
                "✅" if d == "рекомендуется" else "❌" if d == "не участвуем" else "📋"
            )
            print(f"{emoji} {d}: {count}")

        risks = {}
        for r in results:
            risk = r.get("risk_level", "unknown")
            risks[risk] = risks.get(risk, 0) + 1
        print(f"\nРиски:")
        for risk, count in risks.items():
            emoji = "🟢" if risk == "low" else "🟡" if risk == "medium" else "🔴"
            print(f"{emoji} {risk}: {count}")
        print(f"{'=' * 60}")

    logger.info(f"\n✅ Обработано тендеров: {len(results)}")
    return results, sheets_rows


def run_interactive():
    from config.settings import settings
    from core.analyzer import TenderAnalyzer

    errors = settings.validate()
    if errors:
        logger.error("❌ Ошибки конфигурации:")
        for err in errors:
            logger.error(f"   • {err}")
        sys.exit(1)

    analyzer = TenderAnalyzer()

    print("\n" + "=" * 60)
    print("📝 ИНТЕРАКТИВНЫЙ РЕЖИМ")
    print("=" * 60)
    print("Вставьте текст тендера (ТЗ, извещение) и нажмите Enter дважды:")
    print("-" * 60)

    lines = []
    while True:
        try:
            line = input()
            if line.strip() == "" and lines and lines[-1].strip() == "":
                break
            lines.append(line)
        except EOFError:
            break

    tender_text = "\n".join(lines)
    if not tender_text.strip():
        print("Пустой текст. Отмена.")
        return

    print("\n🤖 Анализирую...")
    try:
        result = analyzer.analyze(tender_text=tender_text)
        result_dict = result.to_dict()

        print("\n" + "=" * 60)
        print("📊 РЕЗУЛЬТАТ АНАЛИЗА")
        print("=" * 60)
        print(f"Тип: {result_dict['tender_type']}")
        print(f"НМЦК: {result_dict['nmck']:,.0f} ₽")
        print(f"Себестоимость: {result_dict['cost_price']:,.0f} ₽")
        print(f"Рекомендуемая цена: {result_dict['recommended_price']:,.0f} ₽")
        print(f"Маржа: {result_dict['margin_percent']:.1f}%")
        print(f"Риск: {result_dict['risk_level']}")
        print(f"Решение: {result_dict['decision']}")
        if result_dict.get("needs_manual_review"):  # ← v6.0
            print("⚠️ ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА")
        if result_dict.get("llm_confidence"):  # ← v6.0
            print(f"Уверенность ИИ: {result_dict['llm_confidence']:.2f}")
        print("-" * 60)
        print("Риски:")
        for flag in result_dict["red_flags"]:
            print(f"  • {flag}")
        print("-" * 60)
        print("Комментарий:")
        print(result_dict["comment"])
        print("=" * 60)

    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="TENDER-BOT v2: Анализ тендеров с ИИ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python main.py --parse-only --max-results 10        # Только поиск
  python main.py --analyze --max-pages 2               # Полный анализ
  python main.py --analyze --skip-detail               # Быстрый анализ (без парсинга документов)
  python main.py --interactive                       # Ручной ввод
  python main.py --test                              # Тест калькулятора
        """,
    )

    parser.add_argument(
        "--parse-only",
        action="store_true",
        help="Только поиск и парсинг тендеров (без LLM)",
    )
    parser.add_argument("--analyze", action="store_true", help="Полный анализ с LLM")
    parser.add_argument(
        "--skip-detail", action="store_true", help="Пропустить детальный парсинг"
    )
    parser.add_argument(
        "--interactive", action="store_true", help="Интерактивный режим"
    )
    parser.add_argument(
        "--test", action="store_true", help="Запуск тестов калькулятора"
    )
    parser.add_argument(
        "--max-pages", type=int, default=None, help="Максимум страниц поиска"
    )
    parser.add_argument(
        "--max-results", type=int, default=None, help="Максимум тендеров для обработки"
    )

    args = parser.parse_args()

    if not any([args.parse_only, args.analyze, args.interactive, args.test]):
        parser.print_help()
        sys.exit(0)

    if args.test:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from tests.test_calculator import run_all_tests

        success = run_all_tests()
        sys.exit(0 if success else 1)

    if args.parse_only:
        run_parse_only(max_pages=args.max_pages, max_results=args.max_results)
    elif args.analyze:
        run_analyze(
            max_pages=args.max_pages,
            max_results=args.max_results,
            skip_detail=args.skip_detail,
        )
    elif args.interactive:
        run_interactive()


if __name__ == "__main__":
    main()