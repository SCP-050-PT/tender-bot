#!/usr/bin/env python3
"""
main.py
Интеграционный скрипт TENDER-BOT.
Пайплайн: Поиск → Детальный парсинг → LLM-анализ → Расчёт → Риски → Вывод
"""

import sys
import argparse
import json
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


def run_parse_only(max_pages: int = None, max_results: int = None):
    """
    Режим только парсинга: поиск тендеров без LLM-анализа.
    """
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


def run_analyze(max_pages: int = None, max_results: int = None):
    """
    Полный анализ: поиск → парсинг → LLM-анализ → расчёт → риски.
    """
    logger.info("=" * 60)
    logger.info("🤖 РЕЖИМ: Полный анализ с LLM")
    logger.info("=" * 60)

    from config.settings import settings
    from core.searcher import create_searcher
    from core.analyzer import TenderAnalyzer

    # Валидация настроек
    errors = settings.validate()
    if errors:
        logger.error("❌ Ошибки конфигурации:")
        for err in errors:
            logger.error(f"   • {err}")
        sys.exit(1)

    # Инициализация
    searcher = create_searcher(parsing_only=False)
    analyzer = TenderAnalyzer()

    logger.info("🔍 Начинаю поиск тендеров...")
    results = []

    for tender in searcher.search(max_pages=max_pages, max_results=max_results):
        logger.info(f"\n{'─' * 60}")
        logger.info(f"🆔 {tender.tender_id} | {tender.law}")
        logger.info(f"📌 {tender.title[:80]}...")
        logger.info(
            f"💰 НМЦК: {tender.nmck:,.0f} ₽" if tender.nmck else "💰 НМЦК: не указана"
        )

        # TODO: Детальный парсинг (detailed_parser) — когда будет готов
        # tender_text = detailed_parser.parse(tender.url)

        # Пока используем заглушку — только title для теста
        tender_text = f"""
        Название: {tender.title}
        Заказчик: {tender.customer or 'не указан'}
        НМЦК: {tender.nmck or 'не указана'}
        Закон: {tender.law}
        Регион: {tender.region or 'не указан'}
        """

        # LLM-анализ
        try:
            analysis = analyzer.analyze(
                tender_text=tender_text,
                tender_id=tender.tender_id,
                nmck=tender.nmck,
                region=tender.region,
                procurement_method=tender.etp,
                deadline_date=tender.deadline_date,
                law_type=tender.law.replace("-FZ", ""),
            )

            result_dict = analysis.to_dict()
            results.append(result_dict)

            # Красивый вывод
            print(f"\n{'=' * 60}")
            print(f"📊 РЕЗУЛЬТАТ АНАЛИЗА: {tender.tender_id}")
            print(f"{'=' * 60}")
            print(f"Тип: {result_dict['tender_type']}")
            print(f"НМЦК: {result_dict['nmck']:,.0f} ₽")
            print(f"Себестоимость: {result_dict['cost_price']:,.0f} ₽")
            print(f"Рекомендуемая цена: {result_dict['recommended_price']:,.0f} ₽")
            print(f"Маржа: {result_dict['margin_percent']:.1f}%")
            print(
                f"Риск: {result_dict['risk_level']} | Решение: {result_dict['decision']}"
            )
            print(f"{'─' * 60}")
            print(f"Комментарий:")
            print(
                result_dict["comment"][:300] + "..."
                if len(result_dict["comment"]) > 300
                else result_dict["comment"]
            )
            print(f"{'=' * 60}")

        except Exception as e:
            logger.error(f"❌ Ошибка анализа тендера {tender.tender_id}: {e}")
            continue

    # Сохранение результатов
    if results:
        output_file = (
            f"data/analysis_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
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
        logger.info(f"\n💾 Результаты сохранены: {output_file}")

    logger.info(f"\n✅ Обработано тендеров: {len(results)}")
    return results


def run_interactive():
    """
    Интерактивный режим: вставить текст тендера вручную.
    """
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
        description="TENDER-BOT: Анализ тендеров с ИИ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python main.py --parse-only --max-results 10     # Только поиск
  python main.py --analyze --max-pages 2            # Полный анализ
  python main.py --interactive                      # Ручной ввод
  python main.py --test                             # Тест калькулятора
        """,
    )

    parser.add_argument(
        "--parse-only",
        action="store_true",
        help="Только поиск и парсинг тендеров (без LLM)",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Полный анализ с LLM (поиск → анализ → расчёт)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Интерактивный режим (ручной ввод текста тендера)",
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

    # Если нет аргументов — показать справку
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
        run_analyze(max_pages=args.max_pages, max_results=args.max_results)

    elif args.interactive:
        run_interactive()


if __name__ == "__main__":
    main()
