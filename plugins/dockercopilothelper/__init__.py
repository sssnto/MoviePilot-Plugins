from datetime import datetime, timedelta
from typing import Optional, Any, List, Dict, Tuple

import pytz
import re
import random
import time

from Cython.Includes.numpy import data
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.event import eventmanager, Event
from app.db.transferhistory_oper import TransferHistoryOper
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType
from app.utils.http import RequestUtils


class DockerCopilotHelper(_PluginBase):
    # 插件名称
    plugin_name = "DC坚叔助手"
    # 插件描述
    plugin_desc = "助手坚叔 配合DcokerCopilot-帮你自动更新docker"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/gxterry/MoviePilot-Plugins/main/icons/Docker_Copilot.png"
    # 插件版本
    plugin_version = "0.1"
    # 插件作者
    plugin_author = "gxterry"
    # 作者主页
    author_url = "https://github.com/gxterry"
    # 插件配置项ID前缀
    plugin_config_prefix = "dcokercopilothelper_"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    _onlyonce = False
    # 可用更新
    _update_cron = None
    _updatable_list = []
    _updatable_notify = False
    # 自动更新
    _auto_update_cron = None
    _auto_update_list = []
    _auto_update_notify = False
    _delete_images = False
    # 备份
    _backup_cron = None
    _backup = False
    _host = None
    _backups_cycle = None

    _secretKey = None
    _jwt =None
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):

        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._update_cron = config.get("updatecron")
            self._updatable_list = config.get("updatablelist")
            self._updatable_notify = config.get("updatablenotify")
            self._auto_update_cron = config.get("autoupdatecron")
            self._auto_update_list = config.get("autoupdatelist")
            self._auto_update_notify = config.get("autoupdatenotify")
            self._delete_images = config.get("deleteimages")
            self._backup_cron = config.get("backupcron")
            self._backup = config.get("backup")
            self._host = config.get("host")
            self._backups_cycle = config.get("backupscycle") or 7
            self._secretKey = config.get("secretKey")
            self._jwt = config.get("jwt")

            # 获取DC列表数据
            if self._secretKey and self._host:
                logger.error(f"DC助手服务结束 secretKey，host 未填写")
                return False

            # 加载模块
            if self._enabled or self._onlyonce:
                # 定时服务
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)

                # 立即运行一次
                if self._onlyonce:
                    logger.info(f"DC助手服务启动，立即运行一次")
                    self._scheduler.add_job(self.backup, 'date',
                                            run_date=datetime.now(
                                                tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                            name="DC助手")
                    self._scheduler.add_job(self.auto_update, 'date',
                                            run_date=datetime.now(
                                                tz=pytz.timezone(settings.TZ)) + timedelta(seconds=6),
                                            name="DC助手")

                    self._scheduler.add_job(self.updatable, 'date',
                                            run_date=datetime.now(
                                                tz=pytz.timezone(settings.TZ)) + timedelta(seconds=10),
                                            name="DC助手")

                    # 关闭一次性开关
                    self._onlyonce = False
                    # 保存配置
                    self.__update_config()
                # 周期运行
                if self._backup_cron:
                    try:
                        self._scheduler.add_job(func=self.backup,
                                                trigger=CronTrigger.from_crontab(self._backup_cron),
                                                name="DC助手-备份")
                    except Exception as err:
                        logger.error(f"定时任务配置错误：{str(err)}")
                        # 推送实时消息
                        self.systemmessage.put(f"执行周期配置错误：{err}")
                if self._update_cron:
                    try:
                        self._scheduler.add_job(func=self.auto_update,
                                                trigger=CronTrigger.from_crontab(self._update_cron),
                                                name="DC助手-更新通知")
                    except Exception as err:
                        logger.error(f"定时任务配置错误：{str(err)}")
                        # 推送实时消息
                        self.systemmessage.put(f"执行周期配置错误：{err}")
                if self._auto_update_cron:
                    try:
                        self._scheduler.add_job(func=self.auto_update,
                                                trigger=CronTrigger.from_crontab(self._auto_update_cron),
                                                name="DC助手-自动更新")
                    except Exception as err:
                        logger.error(f"定时任务配置错误：{str(err)}")
                        # 推送实时消息
                        self.systemmessage.put(f"执行周期配置错误：{err}")
                # 启动任务
                if self._scheduler.get_jobs():
                    self._scheduler.print_jobs()
                    self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    def __update_config(self):
        self.update_config(
            {
                "onlyonce": self._onlyonce,
                "cron": self._cron,
                "enabled": self._enabled,
                "days": self._days,
                "waittime": self._waittime,
                "zspcookie": self._zspcookie,
                "zsphost": self._zsphost,
                "moivelib": self._moivelib,
                "tvlib": self._tvlib,
                "flushall": self._flushall,
                "startswith":self._startswith,
                "notify": self._notify,
                "notifyaggregation":self._notifyaggregation,
                "jwt":self._jwt
            }
        )

    def auto_update(self):
        """
        自动更新
        """
    def updatable(self):
        """
        更新通知
        """

    def backup(self):
        """
        备份
        """

    @eventmanager.register(EventType.PluginAction)
    def remote_sync(self, event: Event):
        pass
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass


    def get_auth(self) -> str:
        """
        获取授权
        """
        auth_url = "%s/api/auth" % (self._host)
        rescanres = (RequestUtils(headers={"Content-Type": "application/x-www-form-urlencoded"})
                     .post_res(auth_url, {"secretKey":self._secretKey}))
        data = rescanres.json()
        if data["code"] == 201:
            jwt = data["data"]["jwt"]
            return jwt
        else:
            logger.error(f"DC-获取凭证异常 Error code: {data['code']}, message: {data['msg']}")
            return ""

    def get_docker_list(self,jwt: str) ->List[Dict[str, Any]] :
        """
        容器列表
        """
        docker_url = "%s/api/containers" % (self._host)
        result = (RequestUtils(headers={"Authorization": jwt})
                     .get_res(docker_url))
        data = result.json()
        if data["code"] == 0:
            return data["data"]
        else:
            logger.error(f"DC-获取凭证异常 Error code: {data['code']}, message: {data['msg']}")
            return []


    # if data["code"] == 0:
    #     for item in data["data"]:
    #         auto_update_list.append({"title": item["name"], "value": item["id"]})
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        updatable_list = []
        auto_update_list= []
        jwt = self.get_auth()
        data=self.get_docker_list(jwt)
        for item in data:
            updatable_list.append({"title": item["name"], "value": item["id"]})
            auto_update_list.append({"title": item["name"], "value": item["id"]})
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [                         
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'host',
                                            'label': '服务器地址',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'secretKey',
                                            'label': 'secretKey',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'updatecron',
                                            'label': '更新通知周期',
                                            'placeholder': '0 6 0 ? *'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'multiple': True,
                                            'model': 'updatablelist',
                                            'label': '更新通知容器',
                                            'items': updatable_list
                                        }
                                    }
                                ]
                            }
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'autoupdatecron',
                                            'label': '自动更新周期',
                                            'placeholder': '0 6 0 ? *'
                                        }
                                    }
                               ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'autoupdatenotify',
                                            'label': '自动更新通知'
                                        }
                                    }
                               ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'deleteimages',
                                            'label': '删除旧镜像'
                                        }
                                    }
                               ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'multiple': True,
                                            'model': 'autoupdatelist',
                                            'label': '自动更新容器',
                                            'items': auto_update_list
                                        }
                                    }
                                ]
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'backupcron',
                                            'label': '自动备份',
                                            'placeholder': '0 6 0 ? *'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'backupscycle',
                                            'label': '备份周期'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                     {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'backup',
                                            'label': '备份通知'
                                        }
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "updatablenotify": False,
            "autoupdatenotify": False,
            "deleteimages": False,
            "backup": False,
            "backupscycle": 7
        }

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))