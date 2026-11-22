# Contract Review — JSON Response Schema (тестовая копия)

Для автотестов. Схема совпадает по структуре со схемой из `legal-copilot/references/`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["summary", "overallRecommendation", "findings", "businessQuestions", "lawyerNote"],
  "properties": {
    "summary": {"type": "string", "minLength": 1, "maxLength": 3000},
    "overallRecommendation": {
      "type": "string",
      "enum": ["подписать", "подписать после правок", "не подписывать без существенных правок", "требуется решение юриста"]
    },
    "findings": {
      "type": "array",
      "maxItems": 100,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "clauseReference", "sourceText", "clauseType", "riskLevel", "issue", "recommendation", "proposedWording", "counterpartyComment", "lawyerNote"],
        "properties": {
          "id": {"type": "string", "pattern": "^R[1-9][0-9]*$", "maxLength": 10},
          "clauseReference": {"type": "string", "maxLength": 100},
          "sourceText": {"type": "string", "maxLength": 250},
          "clauseType": {"type": "string", "minLength": 1, "maxLength": 100},
          "riskLevel": {"type": "string", "enum": ["блокирующий", "высокий", "средний", "низкий", "вопрос-бизнесу"]},
          "issue": {"type": "string", "minLength": 1, "maxLength": 1000},
          "recommendation": {"type": "string", "minLength": 1, "maxLength": 1000},
          "proposedWording": {"type": "string", "maxLength": 5000},
          "counterpartyComment": {"type": "string", "maxLength": 1500},
          "lawyerNote": {"type": "string", "maxLength": 1500}
        }
      }
    },
    "businessQuestions": {"type": "array", "maxItems": 30, "items": {"type": "string", "minLength": 1, "maxLength": 500}},
    "lawyerNote": {"type": "string", "maxLength": 3000}
  }
}
```
