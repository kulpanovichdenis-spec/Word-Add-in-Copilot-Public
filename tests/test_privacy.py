import json
import ssl

import httpx
import pytest

from host.dadata import API_URL, DaDataClient, system_trust_ssl_context
from host.errors import ActionFailure
from host.privacy import PrivacySession, valid_identifier
from host.privacy.anonymizer import extract_identifiers, split_document
from host.privacy.workspace import isolated_project


DOCUMENT = """ДОГОВОР № 15/2026
г. Москва 13 октября 2026 г.

ООО «Ромашка», именуемое в дальнейшем «Поставщик», в лице Иванова Ивана Ивановича,
действующего на основании Устава, и АО «Покупатель», именуемое в дальнейшем «Покупатель»,
заключили настоящий договор.

1. Предмет договора
ООО «Ромашка» передаёт товар Покупателю. Цена составляет 1 000 000 рублей.
Оплата производится до 20.10.2026. Для связи: contract@example.test.

12. Реквизиты и подписи сторон
ООО «Ромашка»
ИНН 0001234562 / КПП 000001001, ОГРН 1990001234562
Юридический адрес: 123456, г. Москва, ул. Ленина, д. 1
Иванов Иван Иванович
Тел. +7 (495) 123-45-67
"""


def test_whole_document_strips_header_and_requisites_and_masks_body():
    session = PrivacySession()
    result = session.anonymize(DOCUMENT, whole_document=True)

    assert result.preview.header_removed
    assert result.preview.requisites_removed
    assert result.preview.identifiers == ("0001234562", "1990001234562")
    assert result.preview.safe_to_send
    assert "ДОГОВОР № 15/2026" not in result.text
    assert "Реквизиты и подписи" not in result.text
    assert "Ромашка" not in result.text
    assert "contract@example.test" not in result.text
    assert "[СТОРОНА_" in result.text
    assert "[EMAIL_" in result.text
    assert "1 000 000" in result.text
    assert "20.10.2026" in result.text


def test_selection_is_redacted_without_section_stripping_and_restored_locally():
    session = PrivacySession()
    text = "ООО «Ромашка» направляет письмо Иванову Ивану Ивановичу на legal@example.test"
    result = session.anonymize(text, whole_document=False)
    assert result.preview.header_removed is False
    assert result.preview.requisites_removed is False
    assert "Ромашка" not in result.text and "Иванову" not in result.text and "legal@example.test" not in result.text
    restored = session.restore({"sourceText": result.text, "items": [result.text]})
    assert restored["sourceText"] == text
    assert restored["items"] == [text]


def test_full_legal_form_and_short_document_number_are_masked():
    text = 'Общество с ограниченной ответственностью «Ромашка» исполняет договор № 1.'
    result = PrivacySession().anonymize(text, whole_document=False)
    assert "Ромашка" not in result.text
    assert "№ 1" not in result.text
    assert "[КОМПАНИЯ_" in result.text and "[НОМЕР_ДОК_" in result.text


def test_uncertain_requisites_boundary_requires_explicit_user_attention():
    text = "1. Предмет\nУсловия договора.\n" + ("текст " * 20) + "\nИНН 0001234562"
    result = PrivacySession().anonymize(text, whole_document=True)
    assert not result.preview.requisites_removed
    assert not result.preview.safe_to_send
    assert "Не найден" in result.preview.warning
    assert "0001234562" not in result.text


@pytest.mark.parametrize(
    ("separator", "heading"),
    [
        ("\r", "12. ЮРИДИЧЕСКИЕ АДРЕСА, БАНКОВСКИЕ РЕКВИЗИТЫ И ПОДПИСИ СТОРОН"),
        ("\n", "XIII. ПОДПИСИ И РЕКВИЗИТЫ СТОРОН"),
        ("\r\n", "РАЗДЕЛ 14. МЕСТОНАХОЖДЕНИЕ, БАНКОВСКИЕ РЕКВИЗИТЫ И ПОДПИСИ СТОРОН"),
        ("\r", "РЕКВИЗИТЫ ЗАКАЗЧИКА"),
        ("\r", "16. ЮРИДИЧЕСКИЕ АДРЕСА И ПРОЧИЕ РЕКВИЗИТЫ СТОРОН"),
        ("\n", "**16. Адреса и банковские реквизиты сторон**"),
        ("\n", "### **16.** **Реквизиты и подписи сторон**"),
    ],
)
def test_whole_document_recognizes_common_requisites_headings(separator, heading):
    body = separator.join(["ДОГОВОР", "1. ПРЕДМЕТ", "Условия договора. " * 30])
    text = separator.join([body, heading, "ООО «Контрагент»", "ИНН 0001234562"])
    result = PrivacySession().anonymize(text, whole_document=True)
    assert result.preview.safe_to_send
    assert result.preview.requisites_removed
    assert result.preview.identifiers == ("0001234562",)
    assert "0001234562" not in result.text


def test_whole_document_does_not_treat_sentence_about_requisites_as_heading():
    text = (
        "ДОГОВОР\r1. ПРЕДМЕТ\r" + "Условия договора. " * 30
        + "\rПросим сообщить реквизиты в течение пяти дней.\rИНН 0001234562"
    )
    result = PrivacySession().anonymize(text, whole_document=True)
    assert not result.preview.safe_to_send


def test_general_requisites_heading_precedes_party_subheadings():
    text = (
        "1. Предмет договора\n" + "Условия договора. " * 30
        + "\n**12. Адреса и банковские реквизиты сторон**\nРеквизиты Заказчика\n"
        + "ИНН 0001234562\nРеквизиты Исполнителя\nОГРН 1990001234562"
    )
    result = PrivacySession().anonymize(text, whole_document=True)
    assert result.preview.safe_to_send
    assert result.preview.identifiers == ("0001234562", "1990001234562")
    assert "Реквизиты Заказчика" not in result.text


def test_whole_document_never_extracts_identifier_outside_requisites_section():
    text = "ИНН 0001234562 указан в предмете договора, но отдельного раздела реквизитов нет."
    result = PrivacySession().anonymize(text, whole_document=True)
    assert result.preview.identifiers == ()


def test_split_preserves_preamble_but_removes_title_and_final_block():
    header, body, requisites = split_document(DOCUMENT)
    assert "ДОГОВОР №" in header
    assert "именуемое в дальнейшем" in body
    assert "1. Предмет договора" in body
    assert "Реквизиты и подписи" in requisites


def test_only_valid_labeled_identifiers_are_extracted():
    assert valid_identifier("0001234562")
    assert valid_identifier("1990001234562")
    assert not valid_identifier("7700000000")
    assert extract_identifiers("ИНН 7700000000; ИНН 0001234562; ОГРН 1990001234562") == [
        "0001234562",
        "1990001234562",
    ]


def test_counterparty_context_is_reduced_to_status_flags():
    raw = json.dumps({
        "identifier": "0001234562",
        "lookupStatus": "найдено",
        "company": {"shortName": "ООО Ромашка", "legalAddress": "секретный адрес"},
    }, ensure_ascii=False)
    safe = PrivacySession.safe_counterparty_context(raw)
    assert safe == "DaData: найдено"
    assert "Ромашка" not in safe and "000001001" not in safe and "0001234562" not in safe


def test_document_names_are_hidden_from_model_and_restored_in_response():
    session = PrivacySession()
    placeholder = session.anonymize_name("ДС с Контрагентом.docx", 2)
    assert placeholder == "[ДОКУМЕНТ_2].docx"
    assert session.restore(f"См. {placeholder}, пункт 3") == "См. ДС с Контрагентом.docx, пункт 3"


@pytest.mark.parametrize(
    "name",
    [
        "Иванов Иван Иванович",
        "Иванова Ивана Ивановича",
        "Петровым Петром Петровичем",
        "Морозова Марина Владимировна",
        "Морозовой Марины Владимировны",
        "Сидоровой Анне Сергеевне",
        "Кузнецову Ольгу Ильиничну",
        "Орловой Еленой Андреевной",
        "И.И. Иванова",
        "И. И. Иванова",
        "Иванова И.И.",
        "Иванова И. И.",
    ],
)
def test_russian_names_in_declensions_and_initial_formats_are_masked(name):
    result = PrivacySession().anonymize(f"в лице {name}, действующей на основании Устава", whole_document=False)
    assert name not in result.text
    assert "[ФИО_1]" in result.text


def test_capitalized_organization_words_are_not_mistaken_for_person_name():
    text = "Российский Научный Конгресс проводится в Москве"
    result = PrivacySession().anonymize(text, whole_document=False)
    assert result.text == text


def test_dadata_request_contains_only_identifier_and_builds_local_card():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"suggestions": [{
            "value": "ООО Ромашка",
            "data": {
                "name": {"full_with_opf": "ООО Ромашка", "short_with_opf": "ООО Ромашка"},
                "inn": "0001234562",
                "kpp": "000001001",
                "ogrn": "1990001234562",
                "state": {"status": "ACTIVE"},
                "address": {"unrestricted_value": "123456, г. Москва, ул. Ленина, д. 1"},
                "management": {"name": "Иванов Иван Иванович", "post": "директор"},
            },
        }]})

    client = DaDataClient(lambda: "api-secret", lambda: "secret-secret", transport=httpx.MockTransport(handler))
    card = client.check("0001234562")

    assert seen == {
        "url": API_URL,
        "body": {"query": "0001234562", "count": 1},
        "authorization": "Token api-secret",
    }
    assert card["lookupStatus"] == "найдено"
    assert card["company"]["inn"] == "0001234562"
    assert "comparisons" not in card
    assert client.raw_response["suggestions"][0]["data"]["management"]["name"] == "Иванов Иван Иванович"


def test_dadata_rejects_bad_checksum_before_network_call():
    client = DaDataClient(lambda: "api", lambda: "secret", transport=httpx.MockTransport(lambda request: pytest.fail(str(request.url))))
    with pytest.raises(ActionFailure) as info:
        client.lookup("7700000000")
    assert info.value.code == "invalid_counterparty_id"


def test_dadata_tls_context_keeps_certificate_verification_enabled():
    context = system_trust_ssl_context()
    assert context.verify_mode.name == "CERT_REQUIRED"
    assert context.check_hostname is True
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        assert not context.verify_flags & ssl.VERIFY_X509_STRICT


def test_isolated_project_contains_only_capability_granted_guidance(tmp_path):
    source = tmp_path / "legal-copilot"
    (source / "skills" / "contract-review").mkdir(parents=True)
    (source / "references").mkdir()
    (source / "workflow").mkdir()
    (source / "CLAUDE.md").write_text("instructions", "utf-8")
    (source / "skills" / "contract-review" / "SKILL.md").write_text("skill", "utf-8")
    (source / "references" / "risk-scale.md").write_text("reference", "utf-8")
    cards = source / "profile" / "knowledge" / "clauses" / "cards"
    cards.mkdir(parents=True)
    (cards / "approved.md").write_text("approved clause", "utf-8")
    (source / ".env").write_text("SECRET=value", "utf-8")
    (source / "workflow" / "contract.md").write_text("confidential", "utf-8")

    with isolated_project(
        source,
        "contract-review",
        ("legal-methodology-read", "clause-cards-read"),
    ) as safe:
        assert (safe / "CLAUDE.md").read_text("utf-8") == "instructions"
        assert (safe / "skills" / "contract-review" / "SKILL.md").is_file()
        assert (safe / "references" / "risk-scale.md").is_file()
        assert (safe / "profile" / "knowledge" / "clauses" / "cards" / "approved.md").is_file()
        manifest = json.loads((safe / "references" / "word-addin-runtime.json").read_text("utf-8"))
        assert manifest["contractVersion"] == "word-review.v1"
        assert manifest["inputProtection"] == "host-verified-anonymized"
        assert manifest["capabilities"] == ["clause-cards-read", "legal-methodology-read"]
        assert "profile/knowledge/clauses/cards/approved.md" in manifest["clauseCardFiles"]
        assert not (safe / ".env").exists()
        assert list((safe / "workflow").iterdir()) == []


def test_isolated_project_does_not_copy_ungranted_references(tmp_path):
    source = tmp_path / "legal-copilot"
    (source / "skills" / "word-clause-review").mkdir(parents=True)
    (source / "skills" / "word-clause-review" / "SKILL.md").write_text("skill", "utf-8")
    (source / "references").mkdir()
    (source / "references" / "risk-scale.md").write_text("reference", "utf-8")

    with isolated_project(source, "word-clause-review", ()) as safe:
        assert not (safe / "references" / "risk-scale.md").exists()
        assert (safe / "references" / "word-addin-runtime.json").is_file()


def test_isolated_project_copies_only_checklist_guidance_for_checklist_capability(tmp_path):
    source = tmp_path / "legal-copilot"
    (source / "skills" / "word-contract-checklist").mkdir(parents=True)
    (source / "skills" / "word-contract-checklist" / "SKILL.md").write_text("skill", "utf-8")
    (source / "references").mkdir()
    checklist_dir = source / "profile/knowledge/contract-checklist"
    checklist_dir.mkdir(parents=True)
    (checklist_dir / "word-contract-checklist.md").write_text("checklist", "utf-8")
    (checklist_dir / "contract-checklist-response-schema.md").write_text("schema", "utf-8")
    (checklist_dir / "private-notes.md").write_text("must not be copied", "utf-8")
    (source / "references" / "contract-playbook.md").write_text("must not be copied", "utf-8")

    with isolated_project(source, "word-contract-checklist", ("contract-checklist-read",)) as safe:
        assert (safe / "profile/knowledge/contract-checklist" / "word-contract-checklist.md").is_file()
        assert (safe / "profile/knowledge/contract-checklist" / "contract-checklist-response-schema.md").is_file()
        assert not (safe / "profile/knowledge/contract-checklist" / "private-notes.md").exists()
        assert not (safe / "references" / "word-contract-checklist.md").exists()
        assert not (safe / "references" / "contract-playbook.md").exists()
        manifest = json.loads((safe / "references/word-addin-runtime.json").read_text("utf-8"))
        assert manifest["referenceFiles"] == [
            "profile/knowledge/contract-checklist/contract-checklist-response-schema.md",
            "profile/knowledge/contract-checklist/word-contract-checklist.md",
        ]
