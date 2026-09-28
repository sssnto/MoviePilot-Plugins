"""Focused V3 plugin contract tests without a running MoviePilot host."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def module(name, **attributes):
    value = types.ModuleType(name)
    value.__dict__.update(attributes)
    return value


def load_plugin():
    class FakeScheduler:
        def __init__(self, **_):
            self.running = False
            self.jobs = []

        def add_job(self, *args, **kwargs):
            self.jobs.append((args, kwargs))

        def get_jobs(self):
            return self.jobs

        def start(self):
            self.running = True

        def remove_all_jobs(self):
            self.jobs.clear()

        def shutdown(self, wait=False):
            self.running = False

    class FakeTrigger:
        @staticmethod
        def from_crontab(*_, **__):
            return object()

    class FakeEvents:
        PluginAction = "PluginAction"

    class FakeNotifications:
        Plugin = "Plugin"

    class FakeLogger:
        def info(self, *_):
            pass

        def warning(self, *_):
            pass

        def error(self, *_):
            pass

    class FakeBase:
        def update_config(self, _):
            pass

        def post_message(self, **_):
            pass

    class FakeOper:
        histories = []

        def list_by_date(self, _):
            return self.histories

    def register(_):
        return lambda function: function

    stubs = {
        "apscheduler": module("apscheduler"),
        "apscheduler.schedulers": module("apscheduler.schedulers"),
        "apscheduler.schedulers.background": module("apscheduler.schedulers.background", BackgroundScheduler=FakeScheduler),
        "apscheduler.triggers": module("apscheduler.triggers"),
        "apscheduler.triggers.cron": module("apscheduler.triggers.cron", CronTrigger=FakeTrigger),
        "app": module("app"),
        "app.db": module("app.db"),
        "app.db.oper": module("app.db.oper"),
        "app.db.oper.transferhistory": module("app.db.oper.transferhistory", TransferHistoryOper=FakeOper),
        "app.plugins": module("app.plugins", _PluginBase=FakeBase),
        "app.schemas": module("app.schemas"),
        "app.schemas.types": module("app.schemas.types", EventType=FakeEvents, NotificationType=FakeNotifications),
        "app.sdk": module("app.sdk"),
        "app.sdk.config": module("app.sdk.config", settings=types.SimpleNamespace(TZ="Asia/Shanghai")),
        "app.sdk.events": module("app.sdk.events", Event=object, eventmanager=types.SimpleNamespace(register=register)),
        "app.sdk.logging": module("app.sdk.logging", logger=FakeLogger()),
        "app.sdk.network": module("app.sdk.network", RequestUtils=object),
    }
    path = Path(__file__).resolve().parents[3] / "plugins.v3/zspacemediafresh/__init__.py"
    spec = importlib.util.spec_from_file_location("zspacemediafresh_v3_test", path)
    plugin_module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(plugin_module)
    return plugin_module, FakeOper


PLUGIN, OPER = load_plugin()


class TestZspaceMediaFresh(unittest.TestCase):
    def setUp(self):
        self.plugin = PLUGIN.ZspaceMediaFresh()
        self.plugin.init_plugin({
            "startswith": "/cloud", "moivelib": "电影, 动漫",
            "tvlib": "电视剧，动漫", "zsphost": "nas:5055",
            "zspcookie": "zenithtoken=a; device_id=b; device=c; version=d; _l=e; nas_id=f",
        })

    def test_categories_match_same_history_type(self):
        def history(media_type, category, dest="/cloud/video", status=True):
            return types.SimpleNamespace(type=media_type, category=category, dest=dest, status=status)

        OPER.histories = [
            history("电影", "电影"), history("电影", "电视剧"),
            history("电视剧", "动漫"), history("电影", "动漫", dest="/other"),
            history("电视剧", "电视剧", status=False),
        ]
        self.assertEqual(self.plugin._selected_categories(), ["动漫", "电影"])

    def test_successful_refresh_and_failed_task_are_distinct(self):
        submitted = []
        responses = iter([
            {"code": "200", "data": [{"name": "电影", "id": 10}]},
            {"code": "200", "data": {"task_id": "task-1"}},
            {"code": "200", "data": {"task_status": 2}},
        ])
        def post(_path, data=None):
            if data and "classification_id" in data:
                submitted.append(data)
            return next(responses)

        self.plugin._post = post
        self.assertTrue(self.plugin._refresh_zspace(["电影"]))
        self.assertEqual(submitted[0]["token"], "a")

        responses = iter([
            {"code": "200", "data": [{"name": "电影", "id": 10}]},
            {"code": "200", "data": {"task_id": "task-2"}},
            {"code": "500", "data": {"task_status": 3}},
        ])
        self.plugin._post = lambda *_, **__: next(responses)
        self.assertFalse(self.plugin._refresh_zspace(["电影"]))

    def test_missing_category_is_failure(self):
        self.plugin._post = lambda *_, **__: {"code": "200", "data": []}
        self.assertFalse(self.plugin._refresh_zspace(["电影"]))

    def test_stop_cancels_running_poll(self):
        self.plugin.stop_service()
        with self.assertRaisesRegex(RuntimeError, "已停止"):
            self.plugin._wait_for_task("电影", "task-1", {}, self.plugin._stop_event)

    def test_cookie_encoding_preserves_existing_escapes(self):
        self.plugin._zspcookie = "zenithtoken=abc%2F中文; device_id=b"
        cookies = self.plugin._request_cookies()
        self.assertEqual(cookies["zenithtoken"], "abc%2F%E4%B8%AD%E6%96%87")

    def test_http_request_uses_encoded_cookies(self):
        captured = {}

        class FakeRequest:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def post_res(self, url, data=None, params=None):
                captured.update(url=url, data=data, params=params)
                return types.SimpleNamespace(status_code=200, json=lambda: {"code": "200"})

        self.plugin._zspcookie = "zenithtoken=abc%2F中文; device_id=b"
        with patch.object(PLUGIN, "RequestUtils", FakeRequest):
            self.plugin._post("/zvideo/classification/list")
        self.assertEqual(captured["cookies"]["zenithtoken"], "abc%2F%E4%B8%AD%E6%96%87")
        self.assertEqual(captured["url"], "http://nas:5055/zvideo/classification/list")

    def test_legacy_token_cookie_still_works(self):
        self.plugin._zspcookie = "token=old; device_id=b; device=c; version=d; _l=e; nas_id=f"
        submissions = []
        responses = iter([
            {"code": "200", "data": [{"name": "电影", "id": 10}]},
            {"code": "200", "data": {"task_id": "task-1"}},
            {"code": "200", "data": {"task_status": 2}},
        ])

        def post(_path, data=None):
            if data and "classification_id" in data:
                submissions.append(data)
            return next(responses)

        self.plugin._post = post
        self.assertTrue(self.plugin._refresh_zspace(["电影"]))
        self.assertEqual(submissions[0]["token"], "old")

    def test_form_groups_controls_without_losing_config_fields(self):
        form, defaults = self.plugin.get_form()

        def walk(node):
            yield node
            for child in node.get("content", []):
                yield from walk(child)

        nodes = list(walk(form[0]))
        models = [node.get("props", {}).get("model") for node in nodes
                  if node.get("props", {}).get("model")]
        self.assertEqual(set(models), set(defaults))
        self.assertEqual(len(models), len(defaults))
        self.assertEqual(sum(node["component"] == "VCard" for node in nodes), 4)
        self.assertTrue(any(node.get("props", {}).get("show") == "{{ !flushall }}"
                            for node in nodes))
        cookie_field = next(node for node in nodes
                            if node.get("props", {}).get("model") == "zspcookie")
        self.assertEqual(cookie_field["props"]["type"], "password")


if __name__ == "__main__":
    unittest.main()
