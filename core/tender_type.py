"""
core/tender_type.py
Единое определение типа тендера.
ИСПРАВЛЕНО (27.07.2026 v6.3):
  - Консолидирована логика из text_extractor.py, analyzer.py, detailed_parser.py
  - Приоритет: эксклюзивные ключевые слова обучения > СОУТ > ПЛК > ОПР
  - combined: СОУТ + ОПР (НЕ education + СОУТ — это education)
  - Валидация: education с rm_total>0 и students_count=0 → подозрительно
"""

import re
from typing import Optional, Tuple
from dataclasses import dataclass
from loguru import logger


@dataclass
class TypeDetectionResult:
    tender_type: str  # "соут", "обучение", "плк", "опр", "комбинированный"
    confidence: float  # 0.0–1.0
    is_combined: bool
    primary_type: str  # Для combined — основной тип
    secondary_type: str  # Для combined — вторичный тип
    matched_keywords: list  # Какие ключевые слова сработали
    reason: str  # Почему выбран этот тип


class TenderTypeDetector:
    """
    Единый детектор типа тендера.
    Заменяет: _detect_type() в text_extractor.py, _normalize_tender_type() в analyzer.py,
    TYPE_PATTERNS в detailed_parser.py, _extract_params_from_text() в analyzer.py.
    """

    # === АЛИАСЫ ТИПОВ ===
    TYPE_ALIASES = {
        "sout": "соут",
        "education": "обучение",
        "plk": "плк",
        "opr": "опр",
        "combined": "комбинированный",
        "unknown": "соут",
        "соут": "соут",
        "обучение": "обучение",
        "плк": "плк",
        "опр": "опр",
        "комбинированный": "комбинированный",
        "неизвестно": "соут",
        "oplk": "плк",
        "sout_education": "комбинированный",
        "education_sout": "комбинированный",
        "plk_opr": "комбинированный",
        "opr_plk": "комбинированный",
        "sout_opr": "комбинированный",
        "opr_sout": "комбинированный",
    }

    # === КЛЮЧЕВЫЕ СЛОВА С ВЕСАМИ ===
    # Вес определяет "силу" сигнала. 10 = эксклюзивное (гарантированно этот тип)
    TYPE_KEYWORDS = {
        "соут": [
            ("специальная оценка условий труда", 10),
            ("специальная оценка труда", 9),
            ("оценка условий труда", 8),
            ("оценка рабочих мест", 8),
            ("специальная оценка", 7),
            ("соут", 10),
            ("сои", 8),  # Сводная оценка условий
        ],
        "обучение": [
            # ЭКСКЛЮЗИВНЫЕ — приоритет 100, не перехватываются СОУТ
            ("обучение охране труда", 100),
            ("обучение по охране труда", 100),
            ("обучению охране труда", 100),
            ("обучению по охране труда", 100),
            ("оказание услуг по обучению", 100),
            ("услуги по обучению", 100),
            ("обучение работников", 100),
            ("обучение работодателей", 100),
            # Обычные
            ("обучение", 5),
            ("повышение квалификации", 8),
            ("переподготовка", 8),
            ("профессиональная подготовка", 7),
            ("профессиональное образование", 7),
            ("дополнительное образование", 7),
            ("переквалификация", 7),
            ("обучение безработных", 7),
            ("проверка знаний", 6),
            ("аттестация", 6),
            ("инструктаж", 5),
            ("тренинг", 5),
            # Тематические (тоже обучение)
            ("пожарная безопасность", 6),
            ("промышленная безопасность", 6),
            ("электробезопасность", 6),
            ("работы на высоте", 6),
            ("газоопасные работы", 6),
            ("первая помощь", 6),
            ("технологические карты", 6),
            ("озп", 6),  # Ограниченные пространства
            ("ппр", 6),  # Проект производства работ
            ("техносферная безопасность", 6),
        ],
        "плк": [
            ("производственный лабораторный контроль", 10),
            ("лабораторный контроль", 9),
            ("лабораторные исследования", 8),
            ("замеры вредных факторов", 8),
            ("плк", 10),
            ("санитарно-защитная зона", 7),
            ("сзз", 7),
            ("гигиеническая оценка", 7),
            ("факторы среды", 6),
        ],
        "опр": [
            ("оценка профессиональных рисков", 10),
            ("опр", 10),
            ("профессиональные риски", 8),
            ("управление профессиональными рисками", 8),
        ],
    }

    # === ВАРИАНТЫ СОУТ ===
    VARIANT_KEYWORDS = {
        2: ["карта", "карты", "карт", "индивидуальная карта", "оценочная карта"],
        3: ["протокол", "протоколы", "комплект", "комплекты", "протоколов"],
    }

    def __init__(self):
        logger.info("TenderTypeDetector инициализирован (v6.3)")

    def detect(
        self, text: str, llm_type_hint: Optional[str] = None
    ) -> TypeDetectionResult:
        """
        Определяет тип тендера по тексту.

        Алгоритм:
        1. Нормализуем LLM-hint если есть
        2. Считаем взвешенные скоры по ключевым словам
        3. Проверяем эксклюзивные паттерны (вес 100)
        4. Определяем combined (только СОУТ + ОПР)
        5. Возвращаем результат с confidence
        """
        if not text:
            return self._result("соут", 0.0, [], "пустой текст")

        text_lower = text.lower()

        # === Шаг 1: Нормализация LLM-hint ===
        if llm_type_hint:
            normalized_hint = self._normalize_alias(llm_type_hint)
            # Если LLM дал конкретный тип с высоким confidence — доверяем
            # Но проверим на противоречия
            pass  # Используем как дополнительный сигнал ниже

        # === Шаг 2: Подсчёт взвешенных скоров ===
        scores = {}
        matched_keywords = []

        for ttype, keywords in self.TYPE_KEYWORDS.items():
            score = 0
            for keyword, weight in keywords:
                # Ищем как подстроку (регистронезависимо)
                count = text_lower.count(keyword.lower())
                if count > 0:
                    score += count * weight
                    matched_keywords.append((ttype, keyword, weight, count))
            scores[ttype] = score

        # === Шаг 3: Проверка эксклюзивных паттернов (вес 100) ===
        exclusive_education = False
        for ttype, keywords in self.TYPE_KEYWORDS.items():
            for keyword, weight in keywords:
                if weight >= 100 and keyword.lower() in text_lower:
                    if ttype == "обучение":
                        exclusive_education = True
                        logger.info(
                            f"[tender_type] Эксклюзивное ключевое слово обучения: '{keyword}'"
                        )
                        break

        # === Шаг 4: Определение combined ===
        has_sout = scores.get("соут", 0) > 0
        has_opr = scores.get("опр", 0) > 0
        has_education = scores.get("обучение", 0) > 0
        has_plk = scores.get("плк", 0) > 0

        # combined ТОЛЬКО если СОУТ + ОПР (и нет эксклюзивного обучения)
        is_combined = has_sout and has_opr and not exclusive_education

        # Если есть эксклюзивное обучение — это обучение, даже если есть СОУТ
        if exclusive_education:
            final_type = "обучение"
            confidence = min(1.0, 0.5 + scores.get("обучение", 0) / 100)
            reason = f"эксклюзивное ключевое слово обучения (score={scores.get('обучение', 0)})"

        elif is_combined:
            final_type = "комбинированный"
            confidence = min(
                1.0, 0.6 + (scores.get("соут", 0) + scores.get("опр", 0)) / 50
            )
            reason = (
                f"combined: СОУТ({scores.get('соут', 0)}) + ОПР({scores.get('опр', 0)})"
            )

        else:
            # Выбираем тип с максимальным скором
            if not scores or max(scores.values()) == 0:
                final_type = "соут"  # fallback
                confidence = 0.1
                reason = "нет ключевых слов → fallback 'соут'"
            else:
                # Исключаем combined из выбора
                candidate_scores = {
                    k: v for k, v in scores.items() if k != "комбинированный"
                }
                best_type = max(candidate_scores, key=candidate_scores.get)
                best_score = candidate_scores[best_type]

                # Проверяем, не равны ли скоры обучения и СОУТ
                if (
                    has_education
                    and has_sout
                    and scores.get("обучение", 0) == scores.get("соут", 0)
                ):
                    # При равенстве — приоритет обучению (если есть слово "обучение" в тексте)
                    if "обучение" in text_lower:
                        final_type = "обучение"
                        reason = "равные скоры, приоритет 'обучение' (по правилу)"
                    else:
                        final_type = best_type
                        reason = f"максимальный скор: {best_type}={best_score}"
                else:
                    final_type = best_type
                    reason = f"максимальный скор: {best_type}={best_score}"

                # Расчёт confidence
                total_score = sum(candidate_scores.values())
                if total_score > 0:
                    confidence = min(1.0, best_score / total_score * 2)  # Нормализация
                else:
                    confidence = 0.3

        # === Шаг 5: Дополнительная валидация ===
        # Если тип "обучение", но есть rm_total и нет students_count — подозрительно
        # Эта проверка делается в analyzer, но логируем здесь
        if (
            final_type == "обучение"
            and "рабочих мест" in text_lower
            and "слушател" not in text_lower
        ):
            logger.warning(
                f"[tender_type] Тип 'обучение', но найдено 'рабочих мест' без 'слушателей' — "
                f"возможно неверное определение"
            )
            confidence *= 0.7  # Понижаем confidence

        # === Шаг 6: Учёт LLM-hint ===
        if llm_type_hint:
            normalized_hint = self._normalize_alias(llm_type_hint)
            if normalized_hint != final_type and confidence < 0.5:
                # Низкий confidence + расхождение с LLM — используем LLM
                logger.info(
                    f"[tender_type] Низкий confidence={confidence:.2f}, "
                    f"LLM говорит '{normalized_hint}' → используем LLM"
                )
                final_type = normalized_hint
                confidence = 0.5  # Средний confidence при использовании LLM
                reason += f" (скорректировано по LLM: {normalized_hint})"

        return TypeDetectionResult(
            tender_type=final_type,
            confidence=round(confidence, 2),
            is_combined=is_combined,
            primary_type="соут" if is_combined else final_type,
            secondary_type="опр" if is_combined else "",
            matched_keywords=[
                f"{t}:{k}({w}×{c})" for t, k, w, c in matched_keywords if w < 100
            ],
            reason=reason,
        )

    def detect_variant(self, text: str, llm_variant: Optional[int] = None) -> int:
        """Определяет вариант СОУТ (1, 2, 3)."""
        if llm_variant in (1, 2, 3):
            logger.info(f"Вариант СОУТ из LLM: {llm_variant}")
            return llm_variant

        text_lower = text.lower()

        # Проверяем вариант 3 (протоколы)
        for kw in self.VARIANT_KEYWORDS[3]:
            if kw in text_lower:
                logger.info(f"Вариант СОУТ 3 (по keywords '{kw}'): протоколы/комплекты")
                return 3

        # Проверяем вариант 2 (карты)
        for kw in self.VARIANT_KEYWORDS[2]:
            if kw in text_lower:
                logger.info(f"Вариант СОУТ 2 (по keywords '{kw}'): карты")
                return 2

        logger.info("Вариант СОУТ 1 (по умолчанию): 20% основных + аналогия")
        return 1

    def _normalize_alias(self, raw_type: str) -> str:
        """Нормализует строковый тип в стандартное название."""
        if not raw_type:
            return "соут"
        raw_lower = raw_type.lower().strip()
        if raw_lower in self.TYPE_ALIASES:
            return self.TYPE_ALIASES[raw_lower]
        # Поиск по частичному совпадению
        for alias, normalized in self.TYPE_ALIASES.items():
            if alias in raw_lower or raw_lower in alias:
                return normalized
        return "соут"  # fallback

    def _result(
        self, ttype: str, confidence: float, matched: list, reason: str
    ) -> TypeDetectionResult:
        return TypeDetectionResult(
            tender_type=ttype,
            confidence=confidence,
            is_combined=(ttype == "комбинированный"),
            primary_type=ttype,
            secondary_type="",
            matched_keywords=matched,
            reason=reason,
        )

    def validate_education_has_students(
        self, text: str, students_count: int
    ) -> Tuple[bool, str]:
        """
        Проверяет, что тендер типа "обучение" имеет слушателей.
        Возвращает (is_valid, warning_message).
        """
        if students_count > 0:
            return True, ""

        text_lower = text.lower()
        has_students_keyword = any(
            kw in text_lower
            for kw in ["слушател", "человек", "участник", "сотрудник", "групп"]
        )

        if not has_students_keyword:
            return False, "Тип 'обучение', но количество слушателей не найдено в тексте"

        return True, ""

    def validate_sout_has_rm(self, text: str, rm_total: int) -> Tuple[bool, str]:
        """
        Проверяет, что тендер типа "соут" имеет рабочие места.
        """
        if rm_total > 0:
            return True, ""

        text_lower = text.lower()
        has_rm_keyword = any(
            kw in text_lower
            for kw in ["рабочих мест", "оценке рабочих", "специальная оценка"]
        )

        if not has_rm_keyword:
            return False, "Тип 'соут', но количество РМ не найдено в тексте"

        return True, ""
