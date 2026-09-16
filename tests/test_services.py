"""供应商与渠道服务层单测（真实读写临时 config.yaml / .env）。"""
from __future__ import annotations

import pytest

from app.hermes import channels_service as cs
from app.hermes import providers_service as ps
from app.hermes.config_store import EnvStore, get_path, load_config


# ---------------------------------------------------------------------------
# 供应商
# ---------------------------------------------------------------------------

def test_create_custom_provider_writes_config(hermes_home):
    ps.create_provider(
        pid="my-relay", name="我的中转", kind="custom", preset_id=None,
        protocol="anthropic_messages", base_url="https://relay.example.com",
        env_key="", api_key="sk-abc", default_model="claude-opus-4-6", note="")
    config = load_config()
    node = get_path(config, "providers.my-relay")
    assert node["api_mode"] == "anthropic_messages"
    assert node["key_env"] == "HERMES_PROVIDER_MY_RELAY_API_KEY"
    env = EnvStore.load()
    assert env.get("HERMES_PROVIDER_MY_RELAY_API_KEY") == "sk-abc"


def test_create_preset_provider_uses_official_env_key(hermes_home):
    ps.create_provider(
        pid="x", name="", kind="preset", preset_id="openrouter",
        protocol="openai_chat", base_url="", env_key="",
        api_key="sk-or", default_model="openai/gpt-5.2", note="")
    meta = ps.list_providers()
    assert meta[0].env_key == "OPENROUTER_API_KEY"
    assert meta[0].base_url == "https://openrouter.ai/api/v1"
    assert EnvStore.load().get("OPENROUTER_API_KEY") == "sk-or"


def test_duplicate_id_rejected(hermes_home):
    ps.create_provider(pid="dup", name="d", kind="custom", preset_id=None,
                       protocol="openai_chat", base_url="https://x/v1",
                       env_key="K", api_key="", default_model="", note="")
    with pytest.raises(ps.ProviderError):
        ps.create_provider(pid="dup", name="d2", kind="custom", preset_id=None,
                           protocol="openai_chat", base_url="https://x/v1",
                           env_key="K", api_key="", default_model="", note="")


def test_set_main_model(hermes_home):
    ps.create_provider(pid="p1", name="P1", kind="custom", preset_id=None,
                       protocol="openai_chat", base_url="https://x/v1",
                       env_key="K1", api_key="", default_model="", note="")
    ps.set_main_model("p1", "gpt-5.2", context_length=128000)
    config = load_config()
    assert get_path(config, "model.provider") == "p1"
    assert get_path(config, "model.default") == "gpt-5.2"
    assert get_path(config, "model.context_length") == 128000
    assert get_path(config, "model.api_mode") == "chat_completions"


def test_publish_and_remove_alias(hermes_home):
    ps.create_provider(pid="p2", name="P2", kind="custom", preset_id=None,
                       protocol="openai_chat", base_url="https://y/v1",
                       env_key="K2", api_key="", default_model="", note="")
    ps.publish_alias("p2", "m1", "fast")
    aliases = ps.list_aliases()
    assert aliases["fast"]["model"] == "m1"
    ps.remove_alias("fast")
    assert "fast" not in ps.list_aliases()


def test_fallback_chain(hermes_home):
    entries = [ps.ChainEntry(provider="a", model="m1"),
               ps.ChainEntry(provider="b", model="m2", key_env="KB")]
    ps.set_fallback(entries)
    loaded = ps.get_fallback()
    assert [(e.provider, e.model, e.key_env) for e in loaded] == [
        ("a", "m1", ""), ("b", "m2", "KB")]
    ps.set_fallback([])
    assert ps.get_fallback() == []


def test_delete_provider_cleans_references(hermes_home):
    ps.create_provider(pid="gone", name="G", kind="custom", preset_id=None,
                       protocol="openai_chat", base_url="https://g/v1",
                       env_key="KG", api_key="sk-g", default_model="m", note="")
    ps.set_main_model("gone", "m")
    ps.set_fallback([ps.ChainEntry(provider="gone", model="m")])
    notes = ps.delete_provider("gone")
    assert any("主模型" in n for n in notes)
    assert ps.list_providers() == []
    config = load_config()
    assert "gone" not in (get_path(config, "providers", {}) or {})
    assert get_path(config, "model", {}).get("provider") is None


# ---------------------------------------------------------------------------
# 渠道
# ---------------------------------------------------------------------------

def test_channel_save_and_toggle(hermes_home):
    changes = cs.save_channel(
        "feishu", enabled=True,
        env_values={"FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "s1"},
        config_values={}, extra_values={"admins": "ou_9"})
    assert any("启用" in c for c in changes)
    config = load_config()
    assert get_path(config, "platforms.feishu.enabled") is True
    assert get_path(config, "platforms.feishu.extra.admins") == "ou_9"
    env = EnvStore.load()
    assert env.get("FEISHU_APP_ID") == "cli_x"

    cs.save_channel("feishu", enabled=False)
    assert get_path(load_config(), "platforms.feishu.enabled") is False


def test_channel_clear_marker_removes_env_key(hermes_home):
    cs.save_channel("feishu", env_values={"FEISHU_APP_ID": "to-remove"})
    cs.save_channel("feishu", env_values={"FEISHU_APP_ID": cs.CLEAR_MARKER})
    assert EnvStore.load().get("FEISHU_APP_ID") is None


def test_toolset_override(hermes_home):
    msg = cs.set_toolset("telegram", "hermes-telegram")
    assert "hermes-telegram" in msg
    assert get_path(load_config(), "platform_toolsets.telegram") == ["hermes-telegram"]
    cs.set_toolset("telegram", "")
    assert get_path(load_config(), "platform_toolsets", {}) .get("telegram") is None


def test_list_channels_merges_config(hermes_home):
    cs.save_channel("feishu", enabled=True)
    views = cs.list_channels()
    feishu = next(v for v in views if v.name == "feishu")
    assert feishu.enabled
    assert feishu.configured is False  # 未填必填凭证


def test_platform_fields_match_official_docs(hermes_home):
    """关键平台的凭证字段必须落在网关连接判定读取的位置。"""
    from app.hermes.schema import PLATFORMS

    def env_names(pid):
        return [f.name for f in PLATFORMS[pid].env_fields]

    def extra_names(pid):
        return [f.name for f in PLATFORMS[pid].extra_fields]

    # 网关 _PLATFORM_CONNECTED_CHECKERS：QQ 凭证读 extra.app_id / extra.client_secret
    assert "app_id" in extra_names("qqbot") and "client_secret" in extra_names("qqbot")
    # weixin 连接判定读 extra.account_id
    assert "account_id" in extra_names("weixin")
    # signal 连接判定读 extra.http_url
    assert "http_url" in extra_names("signal")
    # api_server 连接判定读 extra.key
    assert "key" in extra_names("api_server")
    # wecom 保留 env 双通道
    assert "WECOM_BOT_ID" in env_names("wecom") and "WECOM_SECRET" in env_names("wecom")
    assert "SLACK_APP_TOKEN" in env_names("slack") and "SLACK_BOT_TOKEN" in env_names("slack")
    assert "EMAIL_ADDRESS" in env_names("email") and "EMAIL_IMAP_HOST" in env_names("email")
    assert "DINGTALK_CLIENT_ID" in env_names("dingtalk")
    assert all(PLATFORMS[pid].verified for pid in
               ("telegram", "discord", "qqbot", "wecom", "dingtalk", "slack", "email"))


def test_qqbot_credentials_write_to_extra(hermes_home):
    """QQ 凭证必须写入 platforms.qqbot.extra（网关判定位置），而非 .env。"""
    cs.save_channel("qqbot",
                    extra_values={"app_id": "qq-app-1", "client_secret": "sec-1"})
    from app.hermes.config_store import get_path, load_config
    extra = get_path(load_config(), "platforms.qqbot.extra")
    assert extra["app_id"] == "qq-app-1" and extra["client_secret"] == "sec-1"


def test_channel_configured_requires_extra_credentials(hermes_home):
    cs.save_channel("qqbot", extra_values={"app_id": "a", "client_secret": "s"})
    view = next(v for v in cs.list_channels() if v.name == "qqbot")
    assert view.configured is True
    cs.save_channel("qqbot", extra_values={"app_id": "a", "client_secret": "__CLEAR__"})
    view = next(v for v in cs.list_channels() if v.name == "qqbot")
    assert view.configured is False


def test_channel_advanced_extra_json(hermes_home):
    import pytest

    from app.hermes.channels_service import ChannelError

    group_rules = {"group_rules": {"oc_1": {"policy": "allowlist", "allowlist": ["ou_9"]}}}
    cs.save_channel("feishu", extra_json='{"admins": ["ou_1"], "group_rules": {"oc_1": {"policy": "allowlist", "allowlist": ["ou_9"]}}}')
    from app.hermes.config_store import load_config, get_path
    extra = get_path(load_config(), "platforms.feishu.extra")
    assert extra["admins"] == ["ou_1"]
    assert extra["group_rules"]["oc_1"]["policy"] == "allowlist"

    with pytest.raises(ChannelError):
        cs.save_channel("feishu", extra_json="{not json}")
    with pytest.raises(ChannelError):
        cs.save_channel("feishu", extra_json='["array not object"]')


def test_env_keys_match_official_docs(hermes_home):
    """每个渠道的 env 字段必须出现在官方文档键名快照内（快照取自官方仓库文档）。"""
    import json
    import pathlib

    from app.hermes.schema import PLATFORMS

    snapshot = json.loads(
        (pathlib.Path(__file__).parent / "official_channel_keys.json").read_text())
    problems = []
    for pid, d in PLATFORMS.items():
        official = set(snapshot.get(pid, ()))
        for f in d.env_fields:
            if official and f.name not in official:
                problems.append(f"{pid}.{f.name} 不在官方文档键名中")
        required = [f.name for f in d.env_fields if f.required]
        for name in required:
            if official and name not in official:
                problems.append(f"{pid} 必填键 {name} 未在官方文档确认")
    assert not problems, problems


def test_discord_config_scope_top(hermes_home):
    """Discord 的行为键写入顶层 discord.*（官方约定），而非 platforms.discord.*。"""
    cs.save_channel("discord", config_values={"require_mention": "on", "auto_thread": "off"})
    from app.hermes.config_store import get_path, load_config
    config = load_config()
    assert get_path(config, "discord.require_mention") is True
    assert get_path(config, "discord.auto_thread") is False
    assert "require_mention" not in (get_path(config, "platforms.discord", {}) or {})


def test_discord_view_reads_top_level(hermes_home):
    cs.save_channel("discord", config_values={"require_mention": "on"})
    views = cs.list_channels()
    discord = next(v for v in views if v.name == "discord")
    assert discord.config_values.get("require_mention") == "true"
