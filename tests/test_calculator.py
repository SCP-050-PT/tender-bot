"""
tests/test_calculator.py
Тесты для калькулятора тендеров.
"""

import sys
from pathlib import Path

# Добавляем корень проекта в путь (только если нужно)
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.calculator import TenderCalculator


def test_sout_variant_1():
    """Тест СОУТ Вариант 1: 20% основных + аналогия полностью."""
    calc = TenderCalculator()

    result = calc.calculate_sout(
        rm_total=24, rm_category_1=7, rm_category_2=17, variant=1, delivery_count=1
    )

    print(f"\n=== СОУТ Вариант 1 (24 РМ) ===")
    print(f"Себестоимость: {result.cost_price:,.0f} ₽")
    print(f"Рекомендуемая цена: {result.recommended_price:,.0f} ₽")
    print(f"Маржа: {result.margin_percent:.1f}%")

    assert result.recommended_price >= 20000, "Минимум для СОУТ — 20 000₽"
    assert result.margin_percent >= 10, "Маржа должна быть ≥ 10%"


def test_sout_variant_3():
    """Тест СОУТ Вариант 3: карты + комплекты протоколов (20%)."""
    calc = TenderCalculator()

    result = calc.calculate_sout(
        rm_total=24, rm_category_1=7, rm_category_2=3, variant=3, delivery_count=1
    )

    print(f"\n=== СОУТ Вариант 3 (24 РМ) ===")
    print(f"Себестоимость: {result.cost_price:,.0f} ₽")
    print(f"Рекомендуемая цена: {result.recommended_price:,.0f} ₽")
    print(f"Маржа: {result.margin_percent:.1f}%")

    assert result.recommended_price >= 15000


def test_sout_with_iii():
    """Тест СОУТ с ИИИ (субподряд)."""
    calc = TenderCalculator()

    result = calc.calculate_sout(
        rm_total=24, rm_with_iii=8, variant=1, delivery_count=1
    )

    print(f"\n=== СОУТ с ИИИ (8 РМ) ===")
    print(f"Субподряд: {result.subcontractor_cost:,.0f} ₽")
    print(f"Рекомендуемая цена: {result.recommended_price:,.0f} ₽")

    assert result.subcontractor_cost == 5000, "Субподряд для 8 РМ = 5000₽"


def test_sout_with_travel():
    """Тест СОУТ с командировкой."""
    calc = TenderCalculator()

    result = calc.calculate_sout(
        rm_total=24,
        variant=1,
        delivery_count=1,
        transport_cost=30000,
        accommodation_nights=2,
        expert_days=2,
    )

    print(f"\n=== СОУТ с выездом ===")
    print(f"Транспортные: {result.transport_cost:,.0f} ₽")
    print(f"Итого: {result.recommended_price:,.0f} ₽")

    assert result.transport_cost == 30000


def test_education_distance():
    """Тест дистанционного обучения."""
    calc = TenderCalculator()

    result = calc.calculate_education(
        students_count=30, certificates=30, is_distance=True, delivery_count=1
    )

    print(f"\n=== Обучение дистанционное (30 чел) ===")
    print(f"Себестоимость: {result.cost_price:,.0f} ₽")
    print(f"Рекомендуемая цена: {result.recommended_price:,.0f} ₽")

    assert result.recommended_price >= 10000, "Минимум для обучения — 10 000₽"


def test_education_full_time():
    """Тест очного обучения с выездом."""
    calc = TenderCalculator()

    result = calc.calculate_education(
        students_count=20,
        certificates=20,
        is_distance=False,
        days_full_time=2,
        transport_km=500,
        accommodation_nights=3,
        teacher_days=2,
        teacher_rate=4000,
        manikin_days=2,
        delivery_count=1,
    )

    print(f"\n=== Обучение очное с выездом (20 чел, 500 км) ===")
    print(f"Транспортные: {result.transport_cost:,.0f} ₽")
    print(f"Себестоимость: {result.cost_price:,.0f} ₽")
    print(f"Рекомендуемая цена: {result.recommended_price:,.0f} ₽")

    assert result.transport_cost > 0


def test_plk():
    """Тест ПЛК."""
    calc = TenderCalculator()

    result = calc.calculate_plk(points_count=50, factors_count=10, delivery_count=1)

    print(f"\n=== ПЛК (50 точек, 10 факторов) ===")
    print(f"Себестоимость: {result.cost_price:,.0f} ₽")
    print(f"Рекомендуемая цена: {result.recommended_price:,.0f} ₽")

    assert result.recommended_price >= 15000, "Минимум для ПЛК — 15 000₽"


def test_opr():
    """Тест ОПР."""
    calc = TenderCalculator()

    result = calc.calculate_opr(rm_count=50, delivery_count=1, needs_siz_norms=True)

    print(f"\n=== ОПР (50 РМ, нормы СИЗ) ===")
    print(f"Себестоимость: {result.cost_price:,.0f} ₽")
    print(f"Рекомендуемая цена: {result.recommended_price:,.0f} ₽")
    print(f"Маржа: {result.margin_percent:.1f}%")

    assert result.margin_percent == 30, "Маржа для ОПР — 30%"


def test_guarantee():
    """Тест расчёта банковской гарантии."""
    calc = TenderCalculator()

    # Тест 1: 30 000₽ → 1000₽
    bg_cost = calc.calculate_guarantee(30000, "contract")
    print(f"\n=== БГ для контракта 30 000₽ ===")
    print(f"Стоимость БГ: {bg_cost:,.0f} ₽")
    assert bg_cost == 1000, f"Ожидалось 1000, получено {bg_cost}"

    # Тест 2: 100 000₽ → 1200₽
    bg_cost = calc.calculate_guarantee(100000, "contract")
    print(f"\n=== БГ для контракта 100 000₽ ===")
    print(f"Стоимость БГ: {bg_cost:,.0f} ₽")
    assert bg_cost == 1200, f"Ожидалось 1200, получено {bg_cost}"

    # Тест 3: 500 000₽ → 2000₽
    bg_cost = calc.calculate_guarantee(500000, "contract")
    print(f"\n=== БГ для контракта 500 000₽ ===")
    print(f"Стоимость БГ: {bg_cost:,.0f} ₽")
    assert bg_cost == 2000, f"Ожидалось 2000, получено {bg_cost}"


def test_transport():
    """Тест расчёта транспортных расходов."""
    calc = TenderCalculator()

    transport = calc.calculate_transport(
        distance_km=1000, accommodation_nights=3, expert_days=2
    )

    print(f"\n=== Транспорт (1000 км, 3 ночи) ===")
    print(f"Бензин: {transport['fuel_cost']:,.0f} ₽")
    print(f"Проживание: {transport['accommodation_cost']:,.0f} ₽")
    print(f"Суточные: {transport['daily_allowance']:,.0f} ₽")
    print(f"Итого: {transport['total']:,.0f} ₽")

    assert transport["fuel_cost"] > 6000


def run_all_tests():
    """Запускает все тесты."""
    print("=" * 60)
    print("ЗАПУСК ТЕСТОВ КАЛЬКУЛЯТОРА")
    print("=" * 60)

    tests = [
        test_sout_variant_1,
        test_sout_variant_3,
        test_sout_with_iii,
        test_sout_with_travel,
        test_education_distance,
        test_education_full_time,
        test_plk,
        test_opr,
        test_guarantee,
        test_transport,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
            print(f"  ✅ {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {test.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ❌ {test.__name__}: ОШИБКА: {e}")

    print("\n" + "=" * 60)
    print(f"РЕЗУЛЬТАТ: {passed} пройдено, {failed} не пройдено")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
