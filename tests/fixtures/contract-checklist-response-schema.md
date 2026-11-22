# Contract Checklist — test fixture

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["isTemplate", "contractTypes", "counterpartyType", "briefDescription", "findings"],
  "properties": {
    "isTemplate": {"type": "boolean"},
    "contractTypes": {
      "type": "array",
      "items": {"type": "string", "enum": ["legal_entity_resident", "individual_resident", "non_resident", "software_license", "pharmacy", "termination", "unknown"]}
    },
    "counterpartyType": {"type": "string", "enum": ["legal_entity", "individual", "unknown"]},
    "briefDescription": {"type": "string", "minLength": 1},
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["checkId", "checkName", "severity", "status", "description", "issue", "recommendation"],
        "properties": {
          "checkId": {"type": "string", "enum": ["T1"]},
          "checkName": {"type": "string"},
          "severity": {"type": "string", "enum": ["critical", "advisory"]},
          "status": {"type": "string", "enum": ["ok", "issue", "not_applicable", "template_skip", "needs_review"]},
          "description": {"type": "string"},
          "issue": {"type": "string"},
          "recommendation": {"type": "string"}
        }
      }
    }
  }
}
```
