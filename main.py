#!/usr/bin/env python3
"""
main.py
Интеграционный скрипт TENDER-BOT v7.0.0.
Пайплайн: Поиск -> Детальный парсинг -> LLM-анализ -> Расчёт -> Риски -> Google Sheets

v7.0.0: Рефакторинг
  - Убран _detect_type_from_title() (дубль TypeService)
  - Убрано дублирование логов
  - type_hint определяется ТОЛЬКО через TypeService
  - Исправлена передача documents_text в fallback
"""

import sys
import argparse
import json
import csv
from pathlib import Path
from datetime import datetime

# === ЛОГИРОВАНИЕ (ОДИН источник) ===
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

try:
    from loguru import logger

    # Убираем дефолтный хендлер loguru чтобы не было дублей
    logger.remove()

    # Файловый лог
    logger.add(
        LOG_DIR / "tender_bot.log",
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    )

    # Консольный лог (без дублей)
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
except ImportError:
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("tender_bot")


# === ИМПОРТЫ ===
from core.calculation.calculator import TenderCalculator
from core.risk_rules import RiskAnalyzer
from core.tender_type import get_type_detector
from core.google_sheets import SHEET_COLUMNS
from utils.price_parser import (
    format_for_sheets as _format_nmck,
    format_for_sheets as _format_price,
)
from core.search import create_searcher
from core.parsers import DetailedParser
from core.analysis import TenderAnalyzer


def _parse_deadline_to_days(deadline_date_str: str) -> int:
    """Парсит строку даты дедлайна в количество дней до него."""
    if not deadline_date_str:
        return 30

    import re

    formats = [
        "%d.%m.%Y",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
    ]

    deadline_date_str = deadline_date_str.strip()

    for fmt in formats:
        try:
            deadline = datetime.strptime(deadline_date_str, fmt)
            delta = deadline - datetime.now()
            return max(0, delta.days)
        except ValueError:
            continue

    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", deadline_date_str)
    if match:
        try:
            deadline = datetime.strptime(match.group(0), "%d.%m.%Y")
            delta = deadline - datetime.now()
            return max(0, delta.days)
        except ValueError:
            pass

    return 30


def _build_tender_text(detail, documents_text: str) -> str:
    """Строит структурированный текст тендера для LLM."""
    parts = [
        f"НАЗВАНИЕ ЗАКУПКИ: {detail.purchase_name or detail.tender_id}",
        f"ЗАКАЗЧИК: {detail.customer_name or 'не указан'}",
        f"РЕГИОН: {detail.customer_region or 'не указан'}",
        f"АДРЕС ЗАКАЗЧИКА: {detail.customer_address or 'не указан'}",
        f"МЕСТО ПОСТАВКИ: {detail.delivery_address or 'не указано'}",
        f"НМЦК: {detail.nmck:,.2f} ₽" if detail.nmck else "НМЦК: не указана",
        f"СПОСОБ ЗАКУПКИ: {detail.purchase_method or 'не указан'}",
        f"ЭТП: {detail.platform_name or 'не указана'}",
        f"СРОК ПОДАЧИ ЗАЯВОК: {detail.deadline_date or 'не указан'}",
        f"ТРЕБОВАНИЯ: {detail.requirements or 'не указаны'}",
        f"ОБЕСПЕЧЕНИЕ ЗАЯВКИ: {detail.application_guarantee or 'не указано'}",
        f"ОБЕСПЕЧЕНИЕ КОНТРАКТА: {detail.contract_guarantee or 'не указано'}",
        f"АДРЕСОВ ПОСТАВКИ: {detail.addresses_count or 0}",
        f"ГОРОДОВ ПОСТАВКИ: {detail.cities_count or 0}",
        f"РЕГИОНОВ ПОСТАВКИ: {detail.regions_count or 1}",
    ]

    if documents_text and len(documents_text) > 100:
        prioritized_text = _prioritize_documents(documents_text)
        parts.append(f"ТЕКСТ ДОКУМЕНТОВ (ТЗ, извещение): {prioritized_text[:12000]}")
    else:
        parts.append("ДОКУМЕНТЫ: не удалось извлечь текст")
    return "\n".join(parts)


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
            score = 10
        priority_scores.append((score, line))

    priority_scores.sort(key=lambda x: x[0], reverse=True)
    return "\n".join(line for _, line in priority_scores)


def _get_quantity(analysis) -> int:
    """Извлекает количество из анализа."""
    if not hasattr(analysis, "details") or analysis.details is None:
        return 1

    details = analysis.details

    if isinstance(details, dict):
        quantity = (
            details.get("rm_total")
            or details.get("points_count")
            or details.get("students_count")
            or 1
        )
        return int(quantity) if quantity else 1

    quantity = (
        getattr(details, "rm_total", None)
        or getattr(details, "points_count", None)
        or getattr(details, "students_count", None)
        or 1
    )
    return int(quantity) if quantity else 1


def _get_guarantee_info(detail, analysis) -> tuple:
    """Извлекает информацию об обеспечении."""
    app_guarantee = ""
    contract_guarantee = ""
    guarantee_method = ""

    if detail:
        raw_app = (detail.application_guarantee or "").strip()
        raw_contract = (detail.contract_guarantee or "").strip()
        raw_method = (detail.guarantee_method or "").strip()

        if raw_app:
            app_guarantee = raw_app
        if raw_contract:
            contract_guarantee = raw_contract
        if raw_method:
            guarantee_method = raw_method

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
            app_guarantee = "Не требуется"
            contract_guarantee = "Не требуется"

    if (
        analysis
        and hasattr(analysis, "comment")
        and analysis.comment
        and not app_guarantee
    ):
        comment_lower = analysis.comment.lower()
        if "не требуется" in comment_lower:
            app_guarantee = "Не требуется"
            contract_guarantee = "Не требуется"

    if not app_guarantee:
        app_guarantee = "Не требуется"
    if not contract_guarantee:
        contract_guarantee = "Не требуется"
    if not guarantee_method:
        guarantee_method = "Не требуется"

    return app_guarantee, contract_guarantee, guarantee_method


def _build_sheets_row(analysis, detail, tender) -> dict:
    """Формирует строку для Google Sheets."""
    quantity = _get_quantity(analysis)
    app_guarantee, contract_guarantee, guarantee_method = _get_guarantee_info(
        detail, analysis
    )

    law = tender.law.replace("-FZ", "-ФЗ") if tender.law else ""
    if detail and detail.purchase_method:
        procurement = f"{detail.purchase_method}, {law}"
    else:
        procurement = law

    tender_url = tender.url

    needs_manual = getattr(analysis, "needs_manual_review", False)
    llm_conf = getattr(analysis, "llm_confidence", 0.0)

    comment_for_sheets = analysis.comment

    return {
        "ID тендера": tender.tender_id,
        "Наименование услуг": detail.purchase_name if detail else tender.title,
        "Количество": quantity,
        "Способ проведения закупки": procurement,
        "НМЦК": _format_nmck((detail.nmck if detail else 0) or analysis.nmck),
        "Ссылка на тендер": tender_url,
        "ЭТП": detail.platform_name if detail else (tender.etp or ""),
        "Регион": (
            detail.customer_region if detail else (getattr(tender, "region", "") or "")
        ),
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
        "Комментарии руководителя отдела по участию": comment_for_sheets,
        "Ручная проверка": "ДА" if needs_manual else "НЕТ",
        "Уверенность ИИ": f"{llm_conf:.2f}" if llm_conf > 0 else "",
    }


def run_parse_only(max_pages: int = None, max_results: int = None):
    """Режим только парсинга (без LLM)."""
    logger.info("=" * 60)
    logger.info("🔍 РЕЖИМ: Только парсинг (без LLM)")
    logger.info("=" * 60)

    searcher = create_searcher()

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
    """Полный анализ с LLM. v7.0.0: type_hint только через TypeService."""
    logger.info("=" * 60)
    logger.info("🤖 РЕЖИМ: Полный анализ с LLM")
    logger.info("=" * 60)

    from config.settings import settings
    from core.tender_cache import TenderCache

    errors = settings.validate()
    if errors:
        logger.error("❌ Ошибки конфигурации:")
        for err in errors:
            logger.error(f"   • {err}")
        sys.exit(1)

    searcher = create_searcher()
    calculator = TenderCalculator()
    risk_analyzer = RiskAnalyzer()
    type_detector = get_type_detector()
    analyzer = TenderAnalyzer(
        calculator=calculator,
        risk_analyzer=risk_analyzer,
        type_detector=type_detector,
    )

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
            detailed = DetailedParser(session_manager=searcher.session_manager)
            logger.info("📄 DetailedParser инициализирован с session_manager")
        except Exception as e:
            logger.warning(f"⚠️ DetailedParser не инициализирован: {e}")

    sheets_manager = None
    sheets_enabled = getattr(settings, "GOOGLE_SHEETS_ENABLED", True)
    if sheets_enabled:
        try:
            from core.google_sheets import get_sheets_manager

            sheets_manager = get_sheets_manager()
            logger.info("📊 Google Sheets подключен")
        except Exception as e:
            logger.warning(f"⚠️ Google Sheets не подключен: {e}")
    else:
        logger.info("📊 Google Sheets отключен в настройках")

    logger.info("🔍 Начинаю поиск тендеров...")
    results = []
    sheets_rows = []

    for tender in searcher.search(max_pages=max_pages, max_results=max_results):
        logger.info(f"\n{'-' * 60}")
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
                detail = detailed.fetch_and_parse(
                    reg_number=tender.tender_id,
                    law_type=law_clean,
                    notice_guid=getattr(tender, "notice_guid", None) or "",
                    nmck=tender.nmck or 0,
                    fallback_title=tender.title or "",
                    fallback_region=getattr(tender, "region", "") or "",
                    fallback_customer=getattr(tender, "customer", "") or "",
                )

                if detail:
                    logger.info(
                        f"   ✅ Детали получены: {detail.customer_region or 'регион не определён'}"
                    )
                    logger.info(f"   📄 Документов: {len(detail.documents)}")
                    logger.info(
                        f"   🏢 ЭТП: {detail.platform_name or detail.etp or 'не определена'}"
                    )
                    logger.info(
                        f"   🔒 Обеспечение: {detail.application_guarantee or 'не указано'}"
                    )

                    # Обработка документов через DocumentProcessor
                    documents_text = ""
                    if detail.documents:
                        try:
                            from core.document_processor import (
                                DocumentProcessor,
                                DocumentInfo,
                            )

                            doc_processor = DocumentProcessor(
                                session=searcher.session_manager.get_primary_session()
                            )
                            docs = []
                            for doc_dict in detail.documents:
                                docs.append(
                                    DocumentInfo(
                                        name=doc_dict.get("name", ""),
                                        url=doc_dict.get("link", ""),
                                        file_url=doc_dict.get("link", ""),
                                        file_type=doc_dict.get("file_type", ""),
                                    )
                                )
                            documents_text = doc_processor.process_documents(docs)
                            detail.documents_text = documents_text
                            logger.info(
                                f"   📄 Текст документов: {len(documents_text)} симв."
                            )
                        except Exception as e:
                            logger.warning(f"   ⚠️ Ошибка обработки документов: {e}")

                    if not documents_text:
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
            # v7.0.0: включаем documents_text даже в fallback
            doc_part = ""
            if documents_text and len(documents_text) > 100:
                doc_part = f"\n\nТЕКСТ ДОКУМЕНТОВ:\n{documents_text[:12000]}"

            tender_text = f"""НАЗВАНИЕ ЗАКУПКИ:
{tender.title}

ЗАКАЗЧИК:
{tender.customer or 'не указан'}

РЕГИОН:
{getattr(tender, 'region', '') or 'не указан'}

НМЦК:
{tender.nmck or 'не указана'}

ЗАКОН:
{tender.law}{doc_part}"""
            logger.info("   ℹ️ Используется упрощённый текст (title only)")

        # === ШАГ 3: LLM-анализ ===
        try:
            tender_info = {}
            if detail:
                tender_info = {
                    "purchase_name": detail.purchase_name or tender.title,
                    "customer_name": detail.customer_name or tender.customer or "",
                    "customer_region": detail.customer_region
                    or getattr(tender, "region", "")
                    or "",
                    "region": detail.customer_region
                    or getattr(tender, "region", "")
                    or "",
                    "nmck": detail.nmck or tender.nmck or 0,
                    "deadline_date": detail.deadline_date or tender.deadline_date or "",
                    "deadline_days": _parse_deadline_to_days(
                        detail.deadline_date or tender.deadline_date or ""
                    ),
                    "platform_name": detail.platform_name or "",
                    "requirements": detail.requirements or "",
                    "application_guarantee": (
                        detail.application_guarantee or ""
                    ).strip(),
                    "contract_guarantee": (detail.contract_guarantee or "").strip(),
                    "guarantee_method": (detail.guarantee_method or "").strip(),
                }

                tender_info["cities_count"] = detail.cities_count or 1
                tender_info["regions_count"] = detail.regions_count or 1
                tender_info["addresses_count"] = detail.addresses_count or 1
                tender_info["is_annual"] = bool(getattr(detail, "is_annual", False))

                if not tender_info.get("region"):
                    tender_info["region"] = (
                        tender_info.get("customer_region", "")
                        or getattr(tender, "region", "")
                        or ""
                    )

                if detail.tender_type_hint == "education":
                    tender_info["addresses_count"] = 1

                if (detail.rm_total or 0) > 0:
                    tender_info["rm_total"] = detail.rm_total
                    tender_info["rm_total_source"] = "ktru"
                if (detail.students_count or 0) > 0:
                    tender_info["students_count"] = detail.students_count
                    tender_info["students_count_source"] = "ktru"
                if (detail.points_count or 0) > 0:
                    tender_info["points_count"] = detail.points_count
                if detail.has_full_time:
                    tender_info["has_full_time"] = True
                    tender_info["is_distance"] = False
                for field in [
                    "teacher_days",
                    "accommodation_nights",
                    "transport_km",
                    "venue_rent_days",
                    "manikin_days",
                ]:
                    val = getattr(detail, field, 0) or 0
                    if val > 0:
                        tender_info[field] = val

                if (detail.trip_days or 0) > 0:
                    tender_info["trip_days"] = detail.trip_days
                tender_info["is_seasonal"] = bool(getattr(detail, "is_seasonal", False))

                if (detail.opr_positions or 0) > 0:
                    tender_info["opr_positions"] = detail.opr_positions
                if (detail.opr_persons or 0) > 0:
                    tender_info["opr_persons"] = detail.opr_persons
                tender_info["needs_subcontractor"] = getattr(
                    detail, "needs_subcontractor", False
                )

            # v7.0.0: type_hint ТОЛЬКО из detail (DetailedParser уже использует TypeService)
            # Убран _detect_type_from_title() — дубль TypeService
            type_hint = detail.tender_type_hint if detail else None

            logger.info(
                f"[DEBUG] Passing to analyzer: nmck={tender.nmck}, "
                f"region={tender_info.get('region', 'N/A')}, "
                f"text_length={len(tender_text)}, "
                f"students_count={tender_info.get('students_count', 'N/A')}, "
                f"rm_total={tender_info.get('rm_total', 'N/A')}, "
                f"regions_count={tender_info.get('regions_count', 'N/A')}, "
                f"type_hint={type_hint}"
            )
            # v7.1.0: Проверка search title на ОПР/testing ДО передачи в analyzer
            if not type_hint and tender.title:
                from core.services.type_service import TypeService

                _ts = TypeService()
                _title_lower = tender.title.lower()
                for _ttype, _keywords in _ts.TITLE_KEYWORDS.items():
                    if any(_kw in _title_lower for _kw in _keywords):
                        type_hint = _ttype
                        logger.info(f"[v7.1.0] Type hint из search title: {_ttype}")
                        break
            analysis = analyzer.analyze(
                tender_info=tender_info,
                documents_text=documents_text or tender_text,
                llm_classification=None,
                llm_confidence=0.0,
                tender_type_hint=type_hint,
            )

            logger.info(
                f"[DEBUG] Analysis result: type={analysis.tender_type}, "
                f"cost_price={analysis.cost_price}, "
                f"recommended_price={analysis.recommended_price}, "
                f"margin_percent={analysis.margin_percent}, "
                f"needs_manual_review={getattr(analysis, 'needs_manual_review', 'N/A')}, "
                f"llm_confidence={getattr(analysis, 'llm_confidence', 'N/A')}"
            )

            result_dict = analysis.to_dict()
            results.append(result_dict)

            row = _build_sheets_row(analysis, detail, tender)
            sheets_rows.append(row)

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

            print(f"\n{'=' * 60}")
            print(f"📊 РЕЗУЛЬТАТ: {tender.tender_id}")
            print(f"{'=' * 60}")
            print(f"Тип: {analysis.tender_type}")
            print(f"НМЦК: {analysis.nmck:,.0f} ₽")
            print(f"Себестоимость: {analysis.cost_price:,.0f} ₽")
            print(f"Рекомендуемая цена: {analysis.recommended_price:,.0f} ₽")
            print(f"Маржа: {analysis.margin_percent:.1f}%")
            print(f"Риск: {analysis.risk_level} | Решение: {analysis.decision}")
            if getattr(analysis, "needs_manual_review", False):
                print(f"⚠️ ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА")
            if detail and detail.customer_region:
                print(f"Регион: {detail.customer_region}")
            if detail and detail.platform_name:
                print(f"ЭТП: {detail.platform_name}")
            print(f"{'-' * 60}")
            print(f"Комментарий:")
            comment = analysis.comment
            print(comment[:400] + "..." if len(comment) > 400 else comment)
            print(f"{'=' * 60}")

        except Exception as e:
            logger.error(f"❌ Ошибка анализа тендера {tender.tender_id}: {e}")
            import traceback

            logger.error(traceback.format_exc())
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
    """Интерактивный режим."""
    from config.settings import settings

    errors = settings.validate()
    if errors:
        logger.error("❌ Ошибки конфигурации:")
        for err in errors:
            logger.error(f"   • {err}")
        sys.exit(1)

    calculator = TenderCalculator()
    risk_analyzer = RiskAnalyzer()
    type_detector = get_type_detector()
    analyzer = TenderAnalyzer(
        calculator=calculator,
        risk_analyzer=risk_analyzer,
        type_detector=type_detector,
    )

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
        tender_info = {"documents_text": tender_text}
        result = analyzer.analyze(
            tender_info=tender_info,
            documents_text=tender_text,
        )
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
        if result_dict.get("needs_manual_review"):
            print("⚠️ ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА")
        if result_dict.get("llm_confidence") is not None:
            print(f"Уверенность ИИ: {result_dict['llm_confidence']:.2f}")
        print("-" * 60)
        print("Риски:")
        for flag in result_dict.get("red_flags", []):
            print(f"  • {flag}")
        print("-" * 60)
        print("Комментарий:")
        print(result_dict["comment"])
        print("=" * 60)

    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        import traceback

        logger.error(traceback.format_exc())


def main():
    parser = argparse.ArgumentParser(
        description="TENDER-BOT v7.0.0: Анализ тендеров с ИИ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python main.py --parse-only --max-results 10        # Только поиск
  python main.py --analyze --max-pages 2               # Полный анализ
  python main.py --analyze --skip-detail               # Быстрый анализ
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
