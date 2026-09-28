"""MoviePilot V3 plugin for refreshing ZSpace media classifications."""

from datetime import datetime, timedelta
from threading import Event as ThreadEvent, Lock
from time import monotonic, time
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.oper.transferhistory import TransferHistoryOper
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType
from app.sdk.config import settings
from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger
from app.sdk.network import RequestUtils


class ZspaceMediaFresh(_PluginBase):
    plugin_name = "fresh极影视"
    plugin_desc = "按 MoviePilot 整理历史定时刷新极影视分类"
    plugin_icon = "https://raw.githubusercontent.com/sssnto/MoviePilot-Plugins/main/icons/Zspace_B.png"
    plugin_version = "3.1.2"
    plugin_author = "sssnto"
    author_url = "https://github.com/sssnto"
    plugin_config_prefix = "zspacemediafresh_"
    plugin_order = 15
    auth_level = 1
    _enabled = False
    _scheduler = None

    def init_plugin(self, config: dict | None = None) -> None:
        self.stop_service()
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._onlyonce = bool(config.get("onlyonce"))
        self._cron = str(config.get("cron") or "").strip()
        self._timescope = config.get("timescope") or 1
        self._waittime = config.get("waittime") or 60
        self._unit = config.get("unit") or "day"
        self._zspcookie = str(config.get("zspcookie") or "").strip()
        self._zsphost = str(config.get("zsphost") or "").strip().rstrip("/")
        if self._zsphost and not self._zsphost.startswith(("http://", "https://")):
            self._zsphost = "http://" + self._zsphost
        self._moivelib = str(config.get("moivelib") or "")
        self._tvlib = str(config.get("tvlib") or "")
        self._flushall = bool(config.get("flushall"))
        self._startswith = str(config.get("startswith") or "").strip()
        self._notify = bool(config.get("notify"))
        self._notifyaggregation = bool(config.get("notifyaggregation"))
        self._refresh_lock = getattr(self, "_refresh_lock", Lock())
        self._stop_event = ThreadEvent()
        self._scheduler = None

        if not (self._enabled or self._onlyonce):
            return
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        if self._onlyonce:
            self._scheduler.add_job(
                self.refresh, "date",
                run_date=datetime.now(ZoneInfo(settings.TZ)) + timedelta(seconds=3),
                name="极影视立即刷新", max_instances=1,
            )
            self._onlyonce = False
            self._save_config()
        if self._enabled and self._cron:
            try:
                self._scheduler.add_job(
                    self.refresh, CronTrigger.from_crontab(self._cron, timezone=settings.TZ),
                    name="极影视定时刷新", max_instances=1,
                )
            except ValueError as exc:
                logger.error(f"极影视定时任务配置错误：{exc}")
                self.systemmessage.put(f"极影视定时任务配置错误：{exc}")
        if self._scheduler.get_jobs():
            self._scheduler.start()

    def _save_config(self) -> None:
        self.update_config({
            "enabled": self._enabled, "onlyonce": self._onlyonce,
            "cron": self._cron, "timescope": self._timescope,
            "waittime": self._waittime, "unit": self._unit,
            "zspcookie": self._zspcookie, "zsphost": self._zsphost,
            "moivelib": self._moivelib, "tvlib": self._tvlib,
            "flushall": self._flushall, "startswith": self._startswith,
            "notify": self._notify, "notifyaggregation": self._notifyaggregation,
        })

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def _categories(value: str) -> set[str]:
        return {name.strip() for name in value.replace("，", ",").split(",") if name.strip()}

    def _selected_categories(self) -> list[str]:
        if not self._startswith:
            raise ValueError("网盘媒体库路径未设置")
        try:
            amount = int(self._timescope)
        except (TypeError, ValueError) as exc:
            raise ValueError("时间范围必须是正整数") from exc
        if amount <= 0 or self._unit not in ("day", "hour", "minute"):
            raise ValueError("时间范围必须是正整数，单位需为天、小时或分钟")
        target = datetime.now() - timedelta(**{self._unit + "s": amount})
        histories = TransferHistoryOper().list_by_date(target.strftime("%Y-%m-%d %H:%M:%S")) or []
        movie = self._categories(self._moivelib)
        tv = self._categories(self._tvlib)
        selected = set()
        matched = 0
        for history in histories:
            dest = getattr(history, "dest", None)
            if not getattr(history, "status", False) or not dest or not dest.startswith(self._startswith):
                continue
            matched += 1
            category = getattr(history, "category", None)
            if history.type == "电影" and category in movie:
                selected.add(category)
            elif history.type == "电视剧" and category in tv:
                selected.add(category)
        logger.info(f"最近 {amount} {self._unit} 网盘入库 {matched} 条，待刷新分类：{sorted(selected)}")
        return sorted(selected)

    def refresh(self) -> bool:
        """Run a refresh and report whether every selected classification succeeded."""
        if not self._refresh_lock.acquire(blocking=False):
            logger.warning("极影视刷新任务已在运行，跳过本次请求")
            return False
        stop_event = self._stop_event
        try:
            if not self._zsphost or not self._zspcookie:
                logger.error("极空间主机地址或 Cookie 未配置")
                return False
            categories = [] if self._flushall else self._selected_categories()
            if not self._flushall and not categories:
                logger.info("没有匹配的极影视分类，本次不刷新")
                return False
            return self._refresh_zspace(categories, stop_event)
        except Exception as exc:
            logger.error(f"极影视刷新失败：{exc}")
            return False
        finally:
            self._refresh_lock.release()

    @eventmanager.register(EventType.PluginAction)
    def remote_sync(self, event: Event) -> None:
        data = event.event_data if event else None
        if not data or data.get("action") != "zsp_media_refresh":
            return
        self.post_message(channel=data.get("channel"), title="开始刷新极影视 ...", userid=data.get("user"))
        success = self.refresh()
        self.post_message(
            channel=data.get("channel"),
            title="刷新极影视完成！" if success else "刷新极影视未完成，请查看插件日志",
            userid=data.get("user"),
        )

    @staticmethod
    def _parse_cookie(cookie: str) -> dict[str, str]:
        result = {}
        for item in cookie.split(";"):
            key, separator, value = item.strip().partition("=")
            if separator and key:
                result[key] = value
        return result

    def _request_cookies(self) -> dict[str, str]:
        """Encode unsafe characters while preserving browser percent escapes."""
        safe = "!#$%&'()*+-./:<=>?@[]^_`{|}~"
        return {key: quote(value, safe=safe)
                for key, value in self._parse_cookie(self._zspcookie).items()}

    def _post(self, path: str, data: dict | None = None) -> dict:
        url = f"{self._zsphost}{path}"
        response = RequestUtils(
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            cookies=self._request_cookies(), timeout=20,
        ).post_res(url, data=data, params={"rnd": str(time()), "webagent": "v2"})
        if response is None or response.status_code != 200:
            raise RuntimeError(f"极空间请求失败：{path}，HTTP {getattr(response, 'status_code', '无响应')}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"极空间返回无效数据：{path}")
        return payload

    def _refresh_zspace(self, categories: list[str], stop_event: ThreadEvent | None = None) -> bool:
        stop_event = stop_event or self._stop_event
        cookie = self._parse_cookie(self._zspcookie)
        required = {"device_id", "device", "version", "_l", "nas_id"}
        missing = required - cookie.keys()
        token = cookie.get("zenithtoken") or cookie.get("token")
        if not token:
            missing.add("zenithtoken/token")
        if missing:
            raise ValueError(f"Cookie 缺少必要字段：{', '.join(sorted(missing))}")
        response = self._post("/zvideo/classification/list")
        if str(response.get("code")) != "200" or not isinstance(response.get("data"), list):
            raise RuntimeError(f"获取极影视分类失败，错误码：{response.get('code')}")
        available = {item["name"]: item["id"] for item in response["data"] if "name" in item and "id" in item}
        requested = sorted(available) if self._flushall else categories
        if not requested:
            logger.info("极影视没有需要刷新的分类")
            return False
        form = {
            "device_id": cookie["device_id"], "token": token,
            "device": cookie["device"], "plat": "web", "_l": cookie["_l"],
            "version": cookie["version"], "nasid": cookie["nas_id"],
        }
        messages = []
        all_succeeded = True
        for name in requested:
            if stop_event.is_set():
                logger.info("极影视刷新任务已停止")
                return False
            if name not in available:
                logger.warning(f"极影视分类不存在：{name}")
                all_succeeded = False
                continue
            started = monotonic()
            try:
                result = self._post("/zvideo/classification/rescan", {
                    **form, "classification_id": available[name],
                })
                result_data = result.get("data")
                task_id = result_data.get("task_id") if isinstance(result_data, dict) else None
                if str(result.get("code")) != "200" or not task_id:
                    raise RuntimeError(f"极影视分类 {name} 提交刷新失败，错误码：{result.get('code')}")
                self._wait_for_task(name, task_id, form, stop_event)
            except RuntimeError as exc:
                logger.error(str(exc))
                all_succeeded = False
                continue
            message = f"分类：{name} 刷新成功；用时：{int(monotonic() - started)} 秒"
            logger.info(message)
            if self._notify and not self._notifyaggregation:
                self.post_message(mtype=NotificationType.Plugin, title="【刷新极影视】", text=message)
            messages.append(message)
        if self._notify and self._notifyaggregation and messages:
            self.post_message(mtype=NotificationType.Plugin, title="【刷新极影视】", text="\n".join(messages))
        return all_succeeded and len(messages) == len(requested)

    def _wait_for_task(self, name: str, task_id: Any, form: dict,
                       stop_event: ThreadEvent) -> None:
        try:
            interval = int(self._waittime)
        except (TypeError, ValueError) as exc:
            raise ValueError("等待时间必须是正整数") from exc
        if interval <= 0:
            raise ValueError("等待时间必须是正整数")
        deadline = monotonic() + 7200
        while monotonic() < deadline:
            if stop_event.is_set():
                raise RuntimeError(f"极影视分类 {name} 刷新任务已停止")
            result = self._post("/zvideo/classification/rescan/result", {**form, "task_id": task_id})
            data = result.get("data") or {}
            status = data.get("task_status") if isinstance(data, dict) else None
            if str(result.get("code")) not in ("200", "N120024"):
                raise RuntimeError(f"极影视分类 {name} 刷新失败，错误码：{result.get('code')}")
            if str(status) == "2":
                return
            if str(status) not in ("0", "1"):
                raise RuntimeError(f"极影视分类 {name} 状态异常：{status}")
            stop_event.wait(min(interval, max(0, deadline - monotonic())))
        raise RuntimeError(f"极影视分类 {name} 刷新超时")

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        return [{
            "cmd": "/zsp_media_refresh", "event": EventType.PluginAction,
            "desc": "极影视刷新", "category": "",
            "data": {"action": "zsp_media_refresh"},
        }]

    def get_api(self) -> list[dict[str, Any]]:
        return []

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        """Build a compact V3 configuration form without changing stored keys."""
        def control(component: str, model: str, label: str, **props: Any) -> dict:
            base = {"model": model, "label": label, "density": "comfortable"}
            if component == "VSwitch":
                base.update({"color": "primary", "inset": True, "hide-details": True})
            else:
                base.update({"variant": "outlined", "hide-details": "auto"})
            base.update(props)
            return {"component": component, "props": base}

        def col(item: dict, md: int = 12, **props: Any) -> dict:
            return {"component": "VCol",
                    "props": {"cols": 12, "md": md, **props},
                    "content": [item]}

        def row(*items: dict) -> dict:
            return {"component": "VRow", "props": {"dense": True},
                    "content": list(items)}

        def section(title: str, description: str, *rows: dict,
                    show: str | None = None) -> dict:
            props = {"variant": "outlined", "class": "mb-4 rounded-lg"}
            if show:
                props["show"] = show
            return {
                "component": "VCard", "props": props,
                "content": [
                    {"component": "VCardTitle", "text": title},
                    {"component": "VCardSubtitle", "text": description},
                    {"component": "VCardText", "content": list(rows)},
                ],
            }

        content = [
            section(
                "运行方式", "设置自动刷新与手动触发",
                row(
                    col(control("VSwitch", "enabled", "启用定时刷新"), md=4),
                    col(control("VSwitch", "onlyonce", "保存后运行一次"), md=4),
                    col(control("VSwitch", "flushall", "刷新全部分类"), md=4),
                ),
                row(
                    col(control("VTextField", "cron", "执行周期",
                                placeholder="5 1 * * *", hint="5 位 Cron 表达式，留空则不定时运行"), md=8),
                    col(control("VTextField", "waittime", "状态查询间隔（秒）",
                                type="number", min=1), md=4),
                ),
            ),
            section(
                "按入库记录刷新", "仅刷新回溯范围内入库的网盘媒体分类",
                row(
                    col(control("VTextField", "startswith", "网盘媒体库路径",
                                placeholder="/medias/links"), md=6),
                    col(control("VTextField", "timescope", "回溯范围",
                                type="number", min=1), md=3),
                    col(control("VSelect", "unit", "时间单位", items=[
                        {"title": "天", "value": "day"},
                        {"title": "小时", "value": "hour"},
                        {"title": "分钟", "value": "minute"},
                    ]), md=3),
                ),
                row(
                    col(control("VTextField", "moivelib", "电影分类",
                                placeholder="电影、华语电影", hint="多个分类用逗号分隔"), md=6),
                    col(control("VTextField", "tvlib", "电视剧分类",
                                placeholder="电视剧、动漫", hint="多个分类用逗号分隔"), md=6),
                ),
                show="{{ !flushall }}",
            ),
            section(
                "极空间连接", "填写极空间网页地址及当前登录的 Cookie",
                row(
                    col(control("VTextField", "zsphost", "极空间地址",
                                placeholder="http://192.168.1.10:5055"), md=5),
                    col(control("VTextField", "zspcookie", "网页 Cookie",
                                type="password", autocomplete="new-password"), md=7),
                ),
            ),
            section(
                "消息通知", "刷新完成后通过 MoviePilot 发送消息",
                row(
                    col(control("VSwitch", "notify", "开启通知"), md=6),
                    col(control("VSwitch", "notifyaggregation", "合并分类通知"),
                        md=6, show="{{ notify }}"),
                ),
            ),
        ]
        return [{"component": "VForm", "content": content}], {
            "enabled": False, "onlyonce": False, "flushall": False,
            "notify": False, "notifyaggregation": False,
            "cron": "5 1 * * *", "timescope": 1, "unit": "day", "waittime": 60,
            "startswith": "", "zsphost": "", "moivelib": "", "tvlib": "", "zspcookie": "",
        }

    def get_page(self) -> list[dict]:
        return []

    def stop_service(self) -> None:
        self._enabled = False
        stop_event = getattr(self, "_stop_event", None)
        if stop_event:
            stop_event.set()
        scheduler = getattr(self, "_scheduler", None)
        self._scheduler = None
        if scheduler:
            scheduler.remove_all_jobs()
            if scheduler.running:
                scheduler.shutdown(wait=False)
