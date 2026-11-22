# Counterparty Check test schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["identifier", "source", "lookupStatus", "company", "summary", "lawyerNote"],
  "properties": {
    "identifier": { "type": "string", "pattern": "^(?:[0-9]{10}|[0-9]{12}|[0-9]{13}|[0-9]{15})$" },
    "source": { "type": "string", "enum": ["DaData"] },
    "lookupStatus": { "type": "string", "enum": ["найдено", "не найдено", "недоступно"] },
    "company": {
      "type": "object",
      "additionalProperties": false,
      "required": ["fullName", "shortName", "inn", "kpp", "ogrn", "status", "legalAddress", "management"],
      "properties": {
        "fullName": { "type": "string" },
        "shortName": { "type": "string" },
        "inn": { "type": "string" },
        "kpp": { "type": "string" },
        "ogrn": { "type": "string" },
        "status": { "type": "string" },
        "legalAddress": { "type": "string" },
        "management": { "type": "string" }
      }
    },
    "summary": { "type": "string" },
    "lawyerNote": { "type": "string" }
  }
}
```
