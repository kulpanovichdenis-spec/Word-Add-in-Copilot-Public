# Clause Review — JSON Response Schema (тестовая копия)

Для автотестов. Схема совпадает по структуре со схемой из `legal-copilot/references/`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["clauseType", "riskLevel", "issue", "recommendation", "proposedWording",
               "counterpartyComment", "needMoreContext", "contextNeeded", "lawyerNote"],
  "properties": {
    "clauseType": {"type": "string", "enum": ["предмет", "цена", "сроки", "ответственность", "иное"]},
    "riskLevel": {"type": "string", "enum": ["блокирующий", "высокий", "средний", "низкий", "вопрос-бизнесу"]},
    "issue": {"type": "string", "minLength": 1, "maxLength": 500},
    "recommendation": {"type": "string", "minLength": 1, "maxLength": 500},
    "proposedWording": {"type": "string", "maxLength": 3000},
    "counterpartyComment": {"type": "string", "maxLength": 1000},
    "needMoreContext": {"type": "boolean"},
    "contextNeeded": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 200}, "maxItems": 10},
    "lawyerNote": {"type": "string", "maxLength": 1000}
  }
}
```

## Пример (не схема)

```json
{"clauseType": "цена", "riskLevel": "средний"}
```
