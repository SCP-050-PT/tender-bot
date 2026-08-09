"""
core/analysis/analyzer.py
Главный анализатор тендера (оркестрация).
v6.7.1: Исправлены int_fields NameError, bool_fields внутри цикла, needs_manual_review прокидывание,
        classification передаётся в user_message, llm_confidence в EducationCalculator.
"""

from typing import Optional, Tuple
from loguru import logger

from core.calculation.calculator import TenderCalculator
from core.risk_rules import RiskAnalyzer
from core.param_extractor import TenderParamExtractor
from core.tender_type import get_type_detector
from core.analysis.llm_wrapper import LlmWrapper
from core.analysis.result_formatter import TenderAnalysis
from core.calculation.calculator import TenderCalculator, CalculationResult

class TenderAnalyzer:
    """Главный анализатор тендера. Оркестрирует извлечение, расчёт и валидацию."""

    def __init__(self, llm_client=None, calculator=None, risk_analyzer=None):
        self.llm_wrapper = LlmWrapper(llm_client)
        self.calc = calculator or TenderCalculator()
        self.risk = risk_analyzer or RiskAnalyzer()
        self.type_detector = get_type_detector()
        self.param_extractor = TenderParamExtractor()
        logger.info("TenderAnalyzer инициализирован (v6.7.1)")

    def analyze(
        self,
        tender_text: str,
        tender_id: str = None,
        nmck: float = None,
        region: str = None,
        procurement_method: str = None,
        etp: str = None,
        deadline_date: str = None,
        law_type: str = "44",
        tender_info: dict = None,
    ) -> TenderAnalysis:
        """Главный метод анализа тендера."""
        logger.info(f"Начинаю анализ тендера {tender_id or 'N/A'}")
        tender_info = tender_info or {}
        actual_nmck = nmck or 0

        # --- Шаг 1: Извлечение параметров из текста ---
        extracted = self._extract_params(tender_text)
        extracted_rm = getattr(extracted, "rm_total", 0) if extracted else 0
        extracted_type_hint = (
            getattr(extracted, "tender_type_hint", None) if extracted else None
        )

        # --- Шаг 2: Двухуровневый LLM-анализ ---
        llm_result, classification = self._call_llm(tender_text, tender_info, extracted)
        if not isinstance(llm_result, dict):
            llm_result = None
            llm_confidence = 0.0
        else:
            llm_confidence = llm_result.get("confidence", 0.0)

        # --- Шаг 3: Валидация LLM-результатов ---
        llm_needs_review = False
        validated_rm = extracted_rm

        if llm_result and "parse_error" not in llm_result:
            llm_needs_review, validated_rm = self.llm_wrapper.validate_rm(
                llm_result, extracted_rm
            )
            if llm_needs_review:
                llm_result["rm_total"] = validated_rm
                llm_result["needs_manual_review"] = True

            if extracted:
                merged = self.param_extractor.merge_with_llm_result(
                    extracted, llm_result, llm_confidence=llm_confidence
                )
                llm_result = merged.to_dict() if hasattr(merged, "to_dict") else merged

            tender_type = self._resolve_tender_type(
                llm_result, llm_confidence, extracted_type_hint, classification
            )
            details = self._normalize_llm_params(llm_result)
            quantity_source = self._detect_quantity_source(llm_result)
        else:
            details = self._fallback_params(tender_text)
            tender_type = details.get("tender_type", "соут")
            quantity_source = "fallback_text"

        # --- Шаг 4: Переопределение из tender_info (КТРУ/HTML-парсинг) ---
        details = self._merge_tender_info(details, tender_info)

        # v6.7.1: Проверка КТРУ-данных и повышение confidence
        ktru_fields = ["students_count", "rm_total", "points_count"]
        has_ktru = any(
            tender_info.get(f"{field}_source") == "ktru"
            and tender_info.get(field, 0) > 0
            for field in ktru_fields
        )
        if has_ktru:
            logger.info(
                f"[v6.7.1] КТРУ-данные найдены в tender_info, confidence повышен до 1.0"
            )
            llm_confidence = 1.0
            if quantity_source == "nmck_fallback":
                quantity_source = "ktru"
                logger.info(
                    f"[v6.7.1] КТРУ-данные подтверждены, ручная проверка не требуется"
                )

        # --- Шаг 5: Определение варианта СОУТ ---
        sout_variant = self._resolve_sout_variant(tender_type, tender_text, llm_result)
        details["variant"] = sout_variant

        # --- Шаг 6: Определение количества ---
        quantity, quantity_source, needs_manual_review = self._resolve_quantity(
            tender_type, details, actual_nmck, quantity_source, llm_confidence
        )
        details = self._set_quantity_in_details(tender_type, details, quantity)

        # --- Шаг 7: Расчёт ---
        calc_result = self._calculate(
            tender_type,
            details,
            tender_text,
            tender_info,
            needs_manual_review,
            actual_nmck,
            llm_confidence,  # v6.7.1: передаём llm_confidence
        )
        tender_type = details.get("tender_type", tender_type)

        # v6.7.1: Принимаем needs_manual_review из calc_result
        if getattr(calc_result, "needs_manual_review", False):
            needs_manual_review = True

        # --- Шаг 8: Guard'ы ---
        final_price, margin_percent, margin_rub, guard_flags = self._apply_guards(
            calc_result, actual_nmck, tender_type  # добавить tender_type
        )
        needs_manual_review = needs_manual_review or bool(guard_flags)

        # --- Шаг 9: Обеспечение ---
        guarantee_cost = self._calc_guarantee(actual_nmck)

        # --- Шаг 10: Риски ---
        risk_result = self._analyze_risks(
            tender_text,
            margin_percent,
            calc_result.cost_price,
            actual_nmck,
            details,
            tender_type,
            needs_manual_review,
            llm_confidence,
        )

        # --- Шаг 11: Формирование результата ---
        comment = self._build_comment(
            tender_type,
            calc_result,
            risk_result,
            details,
            needs_manual_review,
            quantity,
            guard_flags,
            sout_variant,
        )

        app_guarantee, contract_guarantee, guarantee_method = self._resolve_guarantee(
            tender_info, llm_result
        )

        return TenderAnalysis(
            tender_id=tender_id or "unknown",
            tender_type=tender_type,
            nmck=actual_nmck or 0,
            calculated_price=calc_result.recommended_price,
            recommended_price=final_price,
            margin_percent=margin_percent,
            margin_rub=margin_rub,
            cost_price=calc_result.cost_price,
            risk_level=risk_result.risk_level,
            decision=risk_result.decision,
            comment=comment,
            red_flags=list(risk_result.red_flags) + guard_flags,
            transport_cost=calc_result.transport_cost,
            subcontractor_cost=calc_result.subcontractor_cost,
            guarantee_cost=guarantee_cost,
            details={
                **details,
                "service_name": tender_type,
                "procurement_method": procurement_method or "",
                "etp": etp or "",
                "region": region or "",
                "deadline_date": deadline_date or "",
                "quantity": quantity or 1,
                "application_guarantee": app_guarantee,
                "contract_guarantee": contract_guarantee,
                "guarantee_method": guarantee_method,
            },
            raw_llm_response=llm_result,
            quantity_source=quantity_source,
            needs_manual_review=needs_manual_review,
            llm_confidence=llm_confidence,
        )

    # ============ ШАГИ АНАЛИЗА ============

    def _extract_params(self, tender_text: str):
        """Извлекает параметры из текста через regex."""
        if not tender_text or len(tender_text) < 100:
            return None
        try:
            extracted = self.param_extractor.extract(
                text=tender_text,
                tender_type_hint=self.type_detector.detect(tender_text).tender_type,
            )
            logger.info(f"TextExtractor: confidence={extracted.confidence:.2f}")
            return extracted
        except Exception as e:
            logger.warning(f"TextExtractor не сработал: {e}")
            return None

    def _call_llm(self, tender_text: str, tender_info: dict, extracted):
        if not self.llm_wrapper.llm or self.llm_wrapper.llm is False:
            return None, None

        classification = self._classify_tender(tender_text, tender_info, extracted)
        if classification:
            logger.info(
                f"[v6.7.1] Классификация: {classification.get('tender_type', 'unknown')}, "
                f"confidence={classification.get('confidence', 0)}"
            )

        try:
            result = self.llm_wrapper.analyze_tender(
                tender_text=tender_text,
                tender_info=tender_info,
                extracted_params=extracted,
                classification=classification,
            )
            if result:
                logger.info(f"LLM confidence: {result.get('confidence', 0.0):.2f}")
                return result, classification  # ← tuple
        except Exception as e:
            logger.warning(f"LLM-анализ не удался: {e}")

        return None, classification  # ← classification сохраняется

    def _classify_tender(
        self, tender_text: str, tender_info: dict, extracted
    ) -> Optional[dict]:
        text_preview = tender_text[:2000] if len(tender_text) > 2000 else tender_text
        name = tender_info.get("purchase_name", "")
        okpd2 = tender_info.get("okpd2", "")

        prompt = f"""Классифицируй тендер в 1-2 предложениях.

Название: {name}
ОКПД2: {okpd2}
Текст: {text_preview[:1000]}

Верни ТОЛЬКО JSON:
{{
"tender_type": "sout|education|plk|opr|combined",
"confidence": 0.0,
"reasoning": "краткое обоснование"
}}"""

        try:
            result = self.llm_wrapper.llm.send(
                system_prompt="Ты классификатор тендеров. Отвечай кратко, только JSON.",
                user_message=prompt,
                temperature=0.0,
                max_tokens=200,
            )
            parsed = self.llm_wrapper._parse_llm_response(result)
            if parsed and isinstance(parsed, dict):
                return parsed
        except Exception as e:
            logger.warning(
                f"Классификация не удалась: {e}"
            )  # v6.7.1: warning вместо debug

        return None

    def _resolve_tender_type(
        self, llm_result: dict, llm_confidence: float, extracted_type_hint: str,
        classification: dict = None  # ← новый параметр
    ) -> str:
        students = llm_result.get("students_count", 0) if llm_result else 0
        opr_positions = llm_result.get("opr_positions", 0) if llm_result else 0

        if students > 0 and opr_positions == 0:
            return "обучение"
        if students > 0 and opr_positions > 0:
            return "комбинированный"

        # v6.7.2-fix: Если извлечение упало, но классификация была — используем её
        if llm_confidence == 0 and classification:
            classified_type = classification.get("tender_type", "").lower().strip()
            if classified_type and classified_type != "unknown":
                logger.info(
                    f"[v6.7.2-fix] Извлечение упало, используем классификацию: {classified_type}"
                )
                return classified_type

        llm_type = llm_result.get("tender_type", "соут") if llm_result else "соут"
        # v6.7.2-fix: Если классификация сказала ОПР, а LLM сказал СОУТ — доверяем классификации
        if classification and llm_confidence >= 0.7:
            classified_type = classification.get("tender_type", "").lower().strip()
            if classified_type == "opr" and llm_type in ("соут", "sout"):
                logger.info(
                    f"[v6.7.2-fix] Классификация=opr, но LLM=соут → используем opr"
                )
                return "opr"
            if classified_type == "education" and llm_type in ("соут", "sout"):
                logger.info(
                    f"[v6.7.2-fix] Классификация=education, но LLM=соут → используем education"
                )
                return "education"

        if extracted_type_hint:
            return extracted_type_hint

        return llm_type

    def _detect_quantity_source(self, llm_result: dict) -> str:
        """Определяет источник количества."""
        if not llm_result:
            return "fallback_text"
        if llm_result.get("rm_total_source") == "extracted":
            return "extracted"
        if llm_result.get("points_count_source") == "extracted":
            return "extracted"
        if llm_result.get("students_count_source") == "extracted":
            return "extracted"
        return "llm"

    def _fallback_params(self, tender_text: str) -> dict:
        new_params = self.param_extractor.extract(tender_text)
        result = new_params.to_dict()
        if result.get("deadline_days") is None:
            result["deadline_days"] = 30
        result.update({
            "tender_type": self.type_detector.detect(tender_text).tender_type,
            "variant": self.type_detector.detect_variant(tender_text),
        })
        return result

    def _merge_tender_info(self, details: dict, tender_info: dict) -> dict:
        # v6.7.3-fix: ВСЕГДА мержим rm_total из КТРУ в details (независимо от типа)
        # Это нужно, потому что тип может быть переопределён позже классификацией
        if tender_info.get("rm_total") and tender_info.get("rm_total") > 0:
            details["rm_total"] = tender_info["rm_total"]
            logger.info(
                f"[v6.7.3-fix] КТРУ rm_total={tender_info['rm_total']} записан в details"
            )

        # v6.7.3-fix: Также безусловно мержим students_count и points_count из КТРУ
        for ktru_field in ["students_count", "points_count", "rm_total"]:
            if tender_info.get(ktru_field) and tender_info.get(ktru_field) > 0:
                details[ktru_field] = tender_info[ktru_field]
                logger.info(
                    f"[v6.7.3-fix] КТРУ {ktru_field}={tender_info[ktru_field]} записан в details"
                )

        tender_type = (details.get("tender_type", "") or "").lower()

        # v6.7.1: ВСЕГДА определяем int_fields ДО if/else
        int_fields = []
        if "опр" in tender_type:
            int_fields = ["opr_positions", "opr_persons"]
            if tender_info.get("opr_positions"):
                details["opr_positions"] = tender_info["opr_positions"]
            if tender_info.get("opr_persons"):
                details["opr_persons"] = tender_info["opr_persons"]
            # v6.7.2-fix: КТРУ даёт rm_total, но для ОПР это opr_positions
            if tender_info.get("rm_total") and details.get("opr_positions", 0) == 0:
                details["opr_positions"] = tender_info["rm_total"]
                logger.info(
                    f"[v6.7.2-fix] ОПР: opr_positions не найдено, используем КТРУ rm_total={tender_info['rm_total']}"
                )
            logger.info("[v6.7.1] Тип=ОПР, rm_total из КТРУ используется как fallback")
        else:
            int_fields = [
                "students_count",
                "rm_total",
                "points_count",
                "cities_count",
                "regions_count",
                "trip_days",
                "opr_positions",
                "opr_persons",
            ]

        # v6.7.1: int_fields теперь ВСЕГДА определена
        for field in int_fields:
            if tender_info.get(field) is not None and tender_info[field] > 0:
                details[field] = tender_info[field]

        # v6.7.1: bool_fields ВНЕ цикла int_fields
        bool_fields = ["has_full_time", "is_seasonal", "needs_subcontractor"]
        for field in bool_fields:
            if tender_info.get(field) is not None:
                details[field] = tender_info[field]

        for field in [
            "teacher_days",
            "accommodation_nights",
            "transport_km",
            "venue_rent_days",
            "manikin_days",
        ]:
            if tender_info.get(field) is not None and tender_info[field] > 0:
                details[field] = tender_info[field]

        if tender_info.get("has_full_time") is not None:
            details["is_distance"] = not tender_info["has_full_time"]

        return details

    def _resolve_sout_variant(
        self, tender_type: str, tender_text: str, llm_result: dict
    ) -> int:
        """Определяет вариант расчёта СОУТ."""
        if tender_type not in ("соут", "комбинированный"):
            return 1
        return self.type_detector.detect_variant(
            tender_text, llm_result.get("variant") if llm_result else None
        )

    def _resolve_quantity(
        self,
        tender_type: str,
        details: dict,
        nmck: float,
        quantity_source: str,
        llm_confidence: float,
    ) -> Tuple[int, str, bool]:
        """Определяет количество и флаг ручной проверки."""
        quantity = self._get_quantity(tender_type, details)
        needs_manual_review = False

        if quantity == 0 or quantity is None:
            quantity, quantity_source = self._estimate_from_nmck(
                tender_type, nmck, quantity_source
            )
            if quantity == 0:
                needs_manual_review = True

        if quantity_source == "nmck_fallback":
            if llm_confidence >= 0.7:
                needs_manual_review = False
                quantity_source = "llm_fallback"
                logger.info(
                    f"[v6.7.1] LLM confidence={llm_confidence:.2f} ≥ 0.7, ручная проверка не требуется"
                )
            else:
                needs_manual_review = True
        elif quantity > 200 and quantity_source == "llm" and llm_confidence < 0.3:
            needs_manual_review = True
        elif quantity > 200 and quantity_source == "nmck_fallback":
            needs_manual_review = True

        return quantity, quantity_source, needs_manual_review

    def _get_quantity(self, tender_type: str, details: dict) -> int:
        tt = tender_type.lower()
        if "соут" in tt or "опр" in tt or "комбинированный" in tt:
            return details.get("rm_total", 0) or 0
        elif "плк" in tt:
            return details.get("points_count", 0) or 0
        elif "обучение" in tt:
            return details.get("students_count", 0) or 0
        return 0

    def _estimate_from_nmck(
        self, tender_type: str, nmck: float, current_source: str
    ) -> Tuple[int, str]:
        if nmck <= 0:
            return 0, current_source
        rates = {
            "соут": 1500,
            "плк": 500,
            "обучение": 1500,
            "опр": 5000,
            "комбинированный": 1500,
        }
        rate = rates.get(tender_type.lower(), 1500)
        estimated = max(1, int(nmck / rate))
        logger.debug(
            f"Fallback по НМЦК: {tender_type} ~{estimated} ед. (rate={rate}₽/ед)"
        )
        return estimated, "nmck_fallback"

    def _set_quantity_in_details(
        self, tender_type: str, details: dict, quantity: int
    ) -> dict:
        """Устанавливает quantity в правильное поле details."""
        tt = tender_type.lower()
        if "соут" in tt or "комбинированный" in tt:
            details["rm_total"] = quantity
        elif "плк" in tt:
            details["points_count"] = quantity
        elif "обучение" in tt:
            details["students_count"] = quantity
        return details

    def _calculate(
        self,
        tender_type: str,
        details: dict,
        tender_text: str,
        tender_info: dict,
        manual_review: bool,
        actual_nmck: float = 0,
        llm_confidence: float = 0.0,  # v6.7.1: новый параметр
    ):
        """Вызывает калькулятор с нормализацией типа."""
        if manual_review:
            return self._create_manual_review_result(tender_type, actual_nmck)

        TYPE_ALIASES = {
            "education": "обучение",
            "sout": "соут",
            "plk": "плк",
            "opr": "опр",
            "combined": "комбинированный",
            "обучение": "обучение",
            "соут": "соут",
            "плк": "плк",
            "опр": "опр",
            "комбинированный": "комбинированный",
        }
        tt = TYPE_ALIASES.get(tender_type.lower().strip(), tender_type.lower().strip())
        logger.info(f"[v6.7.1] Нормализованный тип: '{tender_type}' → '{tt}'")

        # None → 0
        numeric_fields = [
            "rm_total",
            "rm_category_1",
            "rm_category_2",
            "iii_count",
            "points_count",
            "students_count",
            "factors_count",
            "delivery_count",
            "teacher_days",
            "accommodation_nights",
            "transport_km",
            "venue_rent_days",
            "manikin_days",
            "trip_days",
            "opr_positions",
            "opr_persons",
            "addresses_count",
            "cities_count",
            "regions_count",
            "deadline_days",
            "certificates",
            "diplomas",
            "worker_certs",
            "qual_certs",
            "protocols_count",
        ]
        for field in numeric_fields:
            if details.get(field) is None:
                details[field] = 0

        # Guard: students → обучение (только если не combined)
        students = details.get("students_count", 0)
        teacher_days = details.get("teacher_days", 0)
        if (
            "соут" in tt
            and (students > 0 or teacher_days > 0)
            and "комбинированный" not in tt
        ):
            logger.warning(
                f"[v6.7.1] Тип '{tt}' переопределён в 'обучение' "
                f"(students={students}, teacher_days={teacher_days})"
            )
            tt = "обучение"
            details["tender_type"] = tt

        regions_count = max(
            1,
            (
                int(details.get("regions_count", 1))
                if isinstance(details.get("regions_count"), (int, float))
                else 1
            ),
        )
        cities_count = details.get("cities_count", 1)
        if isinstance(cities_count, dict):
            cities_count = cities_count.get("total_cities", 1)

        needs_subcontractor = details.get("needs_subcontractor", False)
        if tender_info and tender_info.get("needs_subcontractor"):
            needs_subcontractor = True

        logger.info(
            f"[v6.7.1] regions_count={regions_count}, cities_count={cities_count}"
        )

        if "комбинированный" in tt:
            return self.calc.calculate_combined(
                rm_total=details.get("rm_total", 0),
                rm_category_1=details.get("rm_category_1", 0),
                rm_category_2=details.get("rm_category_2", 0),
                rm_with_iii=details.get("iii_count", 0),
                opr_positions=details.get("opr_positions", 0),
                opr_persons=details.get("opr_persons", 0),
                variant=details.get("variant", 1),
                delivery_count=details.get("delivery_count", 1),
                is_annual=details.get("is_annual", False),
                cities_count=cities_count,
                addresses_count=details.get("addresses_count", 1),
                trip_days=details.get("trip_days", 3),
                regions_count=regions_count,
                is_seasonal=details.get("is_seasonal", False),
            )
        elif "обучение" in tt:
            logger.info(
                f"[v6.7.1] Вызов calculate_education: "
                f"students={details.get('students_count', 0)}, "
                f"is_distance={details.get('is_distance', True)}"
            )
            calc_result = self.calc.calculate_education(
                students_count=details.get("students_count", 0),
                certificates=details.get("certificates", 0),
                diplomas=details.get("diplomas", 0),
                worker_certs=details.get("worker_certs", 0),
                qual_certs=details.get("qual_certs", 0),
                protocols_count=details.get("protocols_count", 0),
                is_distance=details.get("is_distance", True),
                delivery_count=details.get("delivery_count", 1),
                teacher_days=details.get("teacher_days", 0),
                accommodation_nights=details.get("accommodation_nights", 0),
                transport_km=details.get("transport_km", 0),
                venue_rent_days=details.get("venue_rent_days", 0),
                manikin_days=details.get("manikin_days", 0),
                tender_text=tender_text,
                llm_confidence=llm_confidence,  # v6.7.1: передаём confidence
                tender_type=tender_type,
            )
            # v6.7.1: needs_manual_review уже в calc_result, возвращаем как есть
            return calc_result
        elif "соут" in tt:
            return self.calc.calculate_sout(
                rm_total=details.get("rm_total", 0),
                rm_category_1=details.get("rm_category_1", 0),
                rm_category_2=details.get("rm_category_2", 0),
                rm_with_iii=details.get("iii_count", 0),
                variant=details.get("variant", 1),
                delivery_count=details.get("delivery_count", 1),
                is_annual=details.get("is_annual", False),
                needs_subcontractor=needs_subcontractor,
                cities_count=cities_count,
                addresses_count=details.get("addresses_count", 1),
                trip_days=details.get("trip_days", 3),
                regions_count=regions_count,
                is_seasonal=details.get("is_seasonal", False),
            )
        elif "плк" in tt:
            return self.calc.calculate_plk(
                points_count=details.get("points_count", 0),
                factors_count=details.get("factors_count", 0),
                delivery_count=details.get("delivery_count", 1),
                is_annual=details.get("is_annual", False),
                needs_subcontractor=needs_subcontractor,
            )
        elif "опр" in tt:
            opr_positions = details.get("opr_positions", 0) or 0
            opr_persons = details.get("opr_persons", 0) or 0

            # v6.7.3-fix: Усиленный каскадный fallback для opr_positions
            if opr_positions == 0:
                # 1. Проверяем details.rm_total (КТРУ уже тут, тип мог быть переопределён)
                if details.get("rm_total", 0) > 0:
                    opr_positions = details["rm_total"]
                    logger.info(
                        f"[v6.7.3-fix] ОПР: используем details rm_total={opr_positions}"
                    )
                # 2. Проверяем tender_info напрямую
                elif tender_info and tender_info.get("rm_total", 0) > 0:
                    opr_positions = tender_info["rm_total"]
                    logger.info(
                        f"[v6.7.3-fix] ОПР: используем tender_info rm_total={opr_positions}"
                    )
                # 3. Проверяем extracted_rm
                elif tender_info and tender_info.get("extracted_rm", 0) > 0:
                    opr_positions = tender_info["extracted_rm"]
                    logger.info(
                        f"[v6.7.3-fix] ОПР: используем extracted_rm={opr_positions}"
                    )

            # v6.7.2: Если opr_positions всё ещё 0 — последний fallback
            if opr_positions == 0:
                rm_total = details.get("rm_total", 0) or 0
                if rm_total > 0:
                    opr_positions = rm_total
                    logger.info(f"[v6.7.2] ОПР: opr_positions=0, используем rm_total={rm_total}")

            # v6.7.2: Если всё ещё 0 — ручная проверка
            if opr_positions == 0:
                logger.warning("[v6.7.2] ОПР: количество не определено, needs_manual_review=True")
                return CalculationResult(
                    cost_price=0,
                    recommended_price=0,
                    margin_percent=0,
                    margin_rub=0,
                    transport_cost=0,
                    subcontractor_cost=0,
                    details={"note": "ОПР: количество должностей не определено"},
                    needs_manual_review=True,
                    review_reason="ОПР: количество должностей/человек не определено из текста",
                )

            return self.calc.calculate_opr(
                rm_count=opr_positions,
                delivery_count=details.get("delivery_count", 1),
            )
        else:
            logger.warning(
                f"[v6.7.1] Неизвестный тип тендера: '{tt}' (оригинал: '{tender_type}')"
            )
            if details.get("students_count", 0) > 0:
                logger.info(
                    f"[v6.7.1] Fallback на обучение (students={details['students_count']})"
                )
                return self.calc.calculate_education(
                    students_count=details.get("students_count", 0),
                    certificates=details.get("certificates", 0),
                    is_distance=details.get("is_distance", True),
                    tender_text=tender_text,
                    llm_confidence=llm_confidence,  # v6.7.1
                )
            return self.calc.calculate_sout(
                rm_total=details.get("rm_total", 1),
                variant=details.get("variant", 1),
                cities_count=cities_count,
                addresses_count=details.get("addresses_count", 1),
                trip_days=details.get("trip_days", 3),
                regions_count=regions_count,
            )

    def _create_manual_review_result(self, tender_type: str, nmck: float):
        """Создаёт результат для ручной проверки."""
        from core.calculation.calculator import CalculationResult

        estimated_cost = nmck * 0.7 if nmck > 0 else 0
        recommended = nmck * 0.85 if nmck > 0 else 0
        return CalculationResult(
            cost_price=estimated_cost,
            recommended_price=recommended,
            margin_percent=10.0,
            margin_rub=recommended - estimated_cost,
            transport_cost=0,
            subcontractor_cost=0,
            details={"note": "Ручной анализ требуется"},
        )

    # ============ GUARD'Ы ============

    def _apply_guards(
        self, calc_result, nmck: float, tender_type: str = ""  # добавить tender_type
    ) -> Tuple[float, float, float, list]:
        """Применяет guard'ы к результату расчёта."""
        final_price = calc_result.recommended_price
        margin_rub = final_price - calc_result.cost_price

        # v6.7.1: защита от деления на 0
        if calc_result.cost_price > 0:
            margin_percent = margin_rub / calc_result.cost_price * 100
        else:
            margin_percent = 0 if margin_rub == 0 else float("inf")
            logger.warning(f"[v6.7.1] cost_price=0, margin_percent={margin_percent}")

        guard_flags = []

        if nmck > 0 and final_price > nmck:
            logger.error(
                f"🚨 GUARD: price={final_price:,.0f}₽ > НМЦК={nmck:,.0f}₽. "
                f"Скорректировано до 95% от НМЦК."
            )
            final_price = int(nmck * 0.95)
            margin_rub = final_price - calc_result.cost_price
            if calc_result.cost_price > 0:
                margin_percent = margin_rub / calc_result.cost_price * 100
            else:
                margin_percent = 0
            guard_flags.append(
                f"🚨 Рек. цена ({calc_result.recommended_price:,.0f}₽) превышала НМЦК ({nmck:,.0f}₽). "
                f"Скорректировано до {final_price:,.0f}₽."
            )

        # v6.7.3-fix: Для ОПР с малой себестоимостью НЕ добавляем guard-флаг
        is_opr = "опр" in tender_type.lower()
        if margin_percent > 200:
            if is_opr and calc_result.cost_price < 50000:
                logger.info(
                    f"[v6.7.3-fix] ОПР: пропускаем guard margin>{margin_percent:.1f}% "
                    f"(себестоимость {calc_result.cost_price:,.0f}₽ < 50к)"
                )
                # НЕ добавляем в guard_flags
            else:
                logger.error(
                    f"🚨 GUARD: margin={margin_percent:.1f}% > 200%. "
                    f"Аномально высокая маржа."
                )
                guard_flags.append(
                    f"🚨 Аномально высокая маржа: {margin_percent:.1f}%. Проверьте количество."
                )

        if getattr(calc_result, "needs_manual_review", False):
            guard_flags.append(f"⚠️ {calc_result.review_reason}")

        return final_price, margin_percent, margin_rub, guard_flags

    def _calc_guarantee(self, nmck: float) -> float:
        """Рассчитывает стоимость обеспечения (inline)."""
        if nmck <= 0:
            return 0
        try:
            return self.calc.calculate_guarantee(
                contract_sum=nmck, guarantee_type="application"
            )
        except Exception as e:
            logger.warning(f"Не удалось рассчитать обеспечение: {e}")
            return 0

    def _analyze_risks(
        self,
        tender_text: str,
        margin_percent: float,
        cost_price: float,
        nmck: float,
        details: dict,
        tender_type: str,
        needs_manual_review: bool,
        llm_confidence: float,
    ):
        """Анализирует риски тендера."""
        quantity = self._get_quantity(tender_type, details)
        return self.risk.analyze(
            tender_text=tender_text,
            margin_percent=margin_percent,
            cost_price=cost_price,
            nmck=nmck or 100000,
            deadline_days=details.get("deadline_days") or 30,
            volume_large=quantity > 50 if quantity else False,
            region_distance=0,
            venue_required=details.get("has_venue", False),
            addresses_count=details.get("addresses_count", 1),
            cities_count=details.get("cities_count", 1),
            tender_type=tender_type,
            needs_manual_review=needs_manual_review,
            llm_confidence=llm_confidence,
        )

    # ============ НОРМАЛИЗАЦИЯ ============

    def _normalize_llm_params(self, llm_result: dict) -> dict:
        """Нормализует параметры из LLM-ответа."""
        TYPE_ALIASES = {
            "education": "обучение",
            "sout": "соут",
            "plk": "плк",
            "opr": "опр",
            "combined": "комбинированный",
        }

        if not llm_result or not isinstance(llm_result, dict):
            logger.warning(
                f"[v6.7.1] LLM-результат не dict ({type(llm_result)}), возвращаем пустой dict"
            )
            return {}

        has_iii = llm_result.get("has_iii", False)
        iii_count = llm_result.get("iii_count", 0)
        rm_total = llm_result.get("rm_total") or llm_result.get("quantity_rm") or 0
        tender_type = llm_result.get("tender_type", "")
        tender_type = TYPE_ALIASES.get(
            tender_type.lower().strip(), tender_type.lower().strip()
        )

        if tender_type in ("education", "обучение"):
            rm_total = 0
            iii_count = 0
            logger.info("[v6.7.1] Тип=обучение, сбрасываем rm_total и iii_count")
        elif has_iii and iii_count == 0 and rm_total > 0:
            iii_count = max(1, int(rm_total * 0.12))
            logger.info(f"has_iii=true, аппроксимируем iii_count={iii_count}")

        return {
            "students_count": llm_result.get("students_count")
            or llm_result.get("quantity_students")
            or 0,
            "certificates": llm_result.get("certificates", 0),
            "is_distance": llm_result.get("is_distance", True),
            "delivery_count": llm_result.get("delivery_count", 1),
            "rm_total": rm_total,
            "rm_category_1": llm_result.get("rm_category_1", 0),
            "rm_category_2": llm_result.get("rm_category_2", 0),
            "iii_count": iii_count,
            "is_annual": llm_result.get("is_annual", False),
            "points_count": llm_result.get("points_count")
            or llm_result.get("quantity_points")
            or 0,
            "factors_count": llm_result.get("factors_count", 0),
            "needs_subcontractor": llm_result.get("needs_subcontractor", False),
            "needs_siz_norms": llm_result.get("needs_siz_norms", False),
            "needs_dsiz_norms": llm_result.get("needs_dsiz_norms", False),
            "needs_iot_norms": llm_result.get("needs_iot_norms", False),
            "deadline_days": llm_result.get("deadline_days", 30),
            "addresses_count": llm_result.get("addresses_count", 1),
            "has_venue": llm_result.get("has_venue", False),
            "variant": llm_result.get("variant", 1),
            "teacher_days": llm_result.get("teacher_days", 0),
            "accommodation_nights": llm_result.get("accommodation_nights", 0),
            "transport_km": llm_result.get("transport_km", 0),
            "venue_rent_days": llm_result.get("venue_rent_days", 0),
            "manikin_days": llm_result.get("manikin_days", 0),
            "trip_days": llm_result.get("trip_days", 3),
            "opr_positions": llm_result.get("opr_positions", 0),
            "opr_persons": (
                0
                if tender_type in ("education", "обучение")
                else llm_result.get("opr_persons", 0)
            ),
            "is_seasonal": llm_result.get("is_seasonal", False),
            "llm_confidence": llm_result.get("confidence", 0.0),
            "cities_count": llm_result.get("cities_count", 1),
            "regions_count": llm_result.get("regions_count", 1),
        }

    # ============ КОММЕНТАРИЙ И ГАРАНТИЯ ============

    def _build_comment(
        self,
        tender_type: str,
        calc_result,
        risk_result,
        details: dict,
        needs_manual_review: bool,
        quantity: int,
        guard_flags: list,
        sout_variant: int,
    ) -> str:
        """Строит итоговый комментарий."""
        lines = [
            f"Анализ тендера типа «{tender_type}»",
            "",
            f"Расчётная себестоимость: {calc_result.cost_price:,.0f} ₽",
            f"Рекомендуемая цена: {calc_result.recommended_price:,.0f} ₽",
            f"Маржа: {calc_result.margin_percent:.1f}%",
            "",
            f"Решение: {risk_result.decision}",
            f"Уровень риска: {risk_result.risk_level}",
        ]
        if risk_result.red_flags:
            lines.extend(["", "Выявленные риски:"])
            for flag in risk_result.red_flags:
                lines.append(f"• {flag}")

        if needs_manual_review:
            lines.append(
                f"⚠️ ВНИМАНИЕ: Количество не определено из текста. "
                f"Ориентировочная оценка по НМЦК: {quantity} ед. Требуется ручная проверка ТЗ."
            )

        if tender_type == "соут":
            variant_names = {
                1: "20% + аналогия 100₽/РМ",
                2: "1 карта + аналогия 200₽/РМ",
                3: "карты + 20% комплектов протоколов",
            }
            lines.append(
                f"📋 Вариант расчёта СОУТ: {sout_variant} ({variant_names.get(sout_variant, 'неизвестно')})"
            )

        if details.get("regions_count", 1) > 1:
            lines.append(
                f"📍 Регионов: {details['regions_count']}, "
                f"выездов: {details.get('trips', details['regions_count'])}"
            )

        # v6.7.1: review_reason уже в guard_flags через _apply_guards, не дублируем
        for flag in guard_flags:
            lines.append(f"{flag}")

        comment = "\n".join(lines)
        comment = comment.replace("\r\n", "\n").replace("\r", "\n")
        return comment

    def _resolve_guarantee(
        self, tender_info: dict, llm_result: dict
    ) -> Tuple[str, str, str]:
        """Определяет обеспечение (HTML-парсинг приоритетнее LLM)."""
        if tender_info:
            app = (tender_info.get("application_guarantee") or "").strip()
            contract = (tender_info.get("contract_guarantee") or "").strip()
            method = (tender_info.get("guarantee_method") or "").strip()
        else:
            app = contract = method = ""

        if llm_result:
            app = app or llm_result.get("application_guarantee", "")
            contract = contract or llm_result.get("contract_guarantee", "")
            method = method or llm_result.get("guarantee_method", "")

        return app, contract, method
