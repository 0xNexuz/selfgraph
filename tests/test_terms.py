from selfgraph import Schema
from selfgraph.terms import expand_tokens, schema_forms


def _tokens(text):
    return set(text.lower().split())


def _shared_schema(certified=None):
    certified = certified or {}
    return Schema.model_validate(
        {
            "entities": [
                {
                    "name": "Employee",
                    "definitions": [
                        {
                            "name": "status",
                            "expression": "active = true",
                            "synonyms": ["employment state"],
                            "certified": certified.get("Employee", False),
                        }
                    ],
                },
                {
                    "name": "Project",
                    "definitions": [
                        {
                            "name": "status",
                            "expression": "phase != completed",
                            "synonyms": ["project state"],
                            "certified": certified.get("Project", False),
                        }
                    ],
                },
            ]
        }
    )


def test_expand_tokens_maps_a_definition_synonym_to_its_declaring_entity():
    expanded = expand_tokens(_tokens("what is the employment state"), _shared_schema(), _tokens)
    assert "employee" in expanded
    assert "project" not in expanded


def test_expand_tokens_maps_a_shared_definition_name_to_every_owner():
    expanded = expand_tokens(_tokens("what is the status"), _shared_schema(), _tokens)
    assert "employee" in expanded
    assert "project" in expanded


def test_schema_forms_marks_a_shared_definition_certified_only_when_all_owners_are():
    mixed = schema_forms(_shared_schema({"Employee": False, "Project": True}))["definition"]
    both = schema_forms(_shared_schema({"Employee": True, "Project": True}))["definition"]
    assert mixed["status"] == ("status", False)
    assert both["status"] == ("status", True)


def test_schema_forms_certification_is_independent_of_entity_declaration_order():
    schema = _shared_schema({"Employee": False, "Project": True})
    reversed_schema = Schema.model_validate({"entities": list(reversed(schema.model_dump()["entities"]))})
    assert schema_forms(schema)["definition"]["status"] == ("status", False)
    assert schema_forms(reversed_schema)["definition"]["status"] == ("status", False)
