VALID_CARD = {
    "clauseType": "ответственность",
    "riskLevel": "высокий",
    "issue": "Ответственность поставщика ограничена символической суммой.",
    "recommendation": "Предложить соразмерную неустойку и cap на уровне стоимости договора.",
    "proposedWording": "Поставщик уплачивает неустойку в размере 0,1% за каждый день просрочки.",
    "counterpartyComment": "Предлагаем сбалансировать условие об ответственности.",
    "needMoreContext": True,
    "contextNeeded": ["раздел о расторжении"],
    "lawyerNote": "[СТРАТЕГИЧЕСКОЕ РЕШЕНИЕ ЮРИСТА] Размер cap — переговорная переменная.",
}

VALID_REVIEW = {
    "summary": "Договор требует правок по ответственности и срокам.",
    "overallRecommendation": "подписать после правок",
    "findings": [
        {
            "id": "R1",
            "clauseReference": "7.3",
            "sourceText": "Ответственность поставщика ограничена 1 000 рублями.",
            "clauseType": "ответственность",
            "riskLevel": "высокий",
            "issue": "Лимит ответственности несоразмерен возможным убыткам.",
            "recommendation": "Установить лимит на уровне цены договора.",
            "proposedWording": "Ответственность поставщика ограничена ценой договора.",
            "counterpartyComment": "Предлагаем установить соразмерный лимит ответственности.",
            "lawyerNote": "Размер лимита требует переговорного решения.",
        }
    ],
    "businessQuestions": ["Какой лимит ответственности приемлем для бизнеса?"],
    "lawyerNote": "Проверить приложения и техническое задание.",
}

VALID_COUNTERPARTY = {
    "identifier": "0001234562",
    "source": "DaData",
    "lookupStatus": "найдено",
    "company": {
        "fullName": "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «КОНТРАГЕНТ»",
        "shortName": "ООО «Контрагент»",
        "inn": "0001234562",
        "kpp": "770001001",
        "ogrn": "1990001234562",
        "status": "ACTIVE",
        "legalAddress": "г. Москва",
        "management": "Генеральный директор",
    },
    "summary": "Контрагент найден в DaData.",
    "lawyerNote": "Результат получен напрямую из DaData. Сверка с договором не выполнялась.",
}

VALID_CHECKLIST = {
    "isTemplate": False,
    "contractTypes": ["legal_entity_resident"],
    "counterpartyType": "legal_entity",
    "briefDescription": "Заполненный договор с российским юридическим лицом.",
    "findings": [
        {
            "checkId": "T1",
            "checkName": "Условия оплаты при постоплате",
            "severity": "advisory",
            "status": "ok",
            "description": "Оплата привязана к подписанию УПД.",
            "issue": "",
            "recommendation": "",
        }
    ],
}
