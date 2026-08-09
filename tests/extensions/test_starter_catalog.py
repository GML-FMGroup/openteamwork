"""Tests for the bundled, non-sensitive Extension starter catalog."""

from __future__ import annotations

import pytest

from openppx.extensions import ExtensionStarterCatalog, default_extension_starter_catalog
from openppx.extensions.app_models import AppDefinition


def test_default_catalog_has_expected_domain_coverage() -> None:
    catalog = default_extension_starter_catalog()

    assert len(catalog.list()) == 145
    assert len(catalog.list(kind="app")) == 44
    assert len(catalog.list(kind="mcp")) == 4
    assert len(catalog.list(kind="skill")) == 97
    assert len(catalog.list(kind="plugin")) == 0
    assert catalog.get("app-granola").display_name == "Granola"
    assert catalog.get("app-granola").runtime_kind == "mcp"
    assert catalog.get("app-granola").install_mode == "direct_mcp"
    assert catalog.get("app-granola").template["transport"]["auth"] == "oauth"
    assert catalog.get("mcp-microsoft-learn").availability == "ready"
    assert catalog.get("app-jira").runtime_kind == "mcp"
    assert catalog.get("app-monday").install_mode == "direct_mcp"
    assert catalog.get("app-parallel-search").template["transport"]["url"] == (
        "https://search.parallel.ai/mcp"
    )


def test_catalog_search_is_case_insensitive_and_projects_copies() -> None:
    catalog = default_extension_starter_catalog()

    matches = catalog.list(kind="app", query="MEETING")
    payload = matches[0].to_payload()
    payload["template"]["mutated"] = True

    assert any(item.starter_id == "app-granola" for item in matches)
    assert "mutated" not in catalog.get(matches[0].starter_id).template


def test_catalog_payloads_never_contain_credential_values() -> None:
    payload = [item.to_payload() for item in default_extension_starter_catalog().list()]
    keys: set[str] = set()

    def collect_keys(value: object) -> None:
        if isinstance(value, dict):
            keys.update(str(key) for key in value)
            for item in value.values():
                collect_keys(item)
        elif isinstance(value, list):
            for item in value:
                collect_keys(item)

    collect_keys(payload)

    assert {"secretValues", "credentialValues", "accessToken", "refreshToken", "password"}.isdisjoint(keys)


def test_app_and_mcp_starters_publish_explicit_local_icon_identity() -> None:
    catalog = default_extension_starter_catalog()

    branded = [*catalog.list(kind="app"), *catalog.list(kind="mcp")]

    assert all(item.presentation.icon not in {"app", "mcp"} for item in branded)
    assert catalog.get("app-telegram").to_payload()["presentation"] == {
        "icon": "telegram",
        "brandColor": "#229ed9",
    }
    assert catalog.get("mcp-context7").presentation.icon == "context7"


def test_every_direct_mcp_starter_has_a_complete_one_click_template() -> None:
    for starter in default_extension_starter_catalog().list():
        if starter.install_mode != "direct_mcp":
            continue
        assert {"serverId", "displayName", "risk", "transport"}.issubset(starter.template), starter.starter_id


def test_every_direct_app_starter_has_a_valid_definition() -> None:
    catalog = default_extension_starter_catalog()
    expected = {
        "app-telegram": "telegram-bot-api",
        "app-slack": "slack-web-api",
        "app-gmail": "gmail-api",
        "app-google-calendar": "google-calendar-api",
        "app-outlook": "microsoft-graph",
        "app-email": "imap-readonly",
        "app-notion": "notion-api",
    }

    for starter_id, adapter_id in expected.items():
        starter = default_extension_starter_catalog().get(starter_id)
        definition = AppDefinition.model_validate(starter.template["definition"])

        assert starter.install_mode == "direct_app"
        assert starter.availability == "needs_auth"
        assert definition.spec.implementation.type == "native"
        assert definition.spec.implementation.adapter == adapter_id
        assert definition.spec.tools

    wps = catalog.get("app-wps-cloud-docs")
    definition = AppDefinition.model_validate(wps.template["definition"])

    assert wps.install_mode == "direct_app"
    assert wps.availability == "needs_auth"
    assert definition.spec.implementation.type == "mcp"
    assert {tool.access for tool in definition.spec.tools} == {"read"}
    assert {tool.name for tool in definition.spec.tools} == {
        "kso_yundoc_extract_yundoc_comment",
        "kso_yundoc_extract_yundoc_content",
        "kso_yundoc_get_file_meta",
        "kso_yundoc_search_yundoc",
    }
    email = AppDefinition.model_validate(catalog.get("app-email").template["definition"])
    assert email.spec.auth.credentials[0].input_type == "email"

    feishu = catalog.get("app-feishu-docs")
    definition = AppDefinition.model_validate(feishu.template["definition"])

    assert feishu.install_mode == "direct_app"
    assert feishu.availability == "needs_auth"
    assert definition.spec.implementation.type == "mcp"
    assert definition.spec.implementation.transport.url == "https://mcp.feishu.cn/mcp"
    assert [credential.name for credential in definition.spec.auth.credentials] == [
        "user-access-token"
    ]
    assert {tool.access for tool in definition.spec.tools} == {"read"}
    assert {tool.name for tool in definition.spec.tools} == {
        "fetch-doc",
        "get-comments",
        "list-docs",
        "search-doc",
    }


def test_catalog_rejects_duplicate_ids() -> None:
    catalog = default_extension_starter_catalog()
    item = catalog.get("app-granola")

    with pytest.raises(ValueError, match="duplicate Extension starter id"):
        ExtensionStarterCatalog((item, item))
