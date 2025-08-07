from datetime import datetime, timedelta
from typing import Optional, Any, List, Dict, Tuple

import pytz
import re
import random
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.event import eventmanager, Event
from app.db.transferhistory_oper import TransferHistoryOper
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType
from app.utils.http import RequestUtils, cookie_parse


class ZspaceMediaFresh(_PluginBase):
    # 插件名称
    plugin_name = "fresh极影视"
    # 插件描述
    plugin_desc = "定时刷新极影视。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/sssnto/MoviePilot-Plugins/main/icons/Zspace_B.png"
    # 插件版本
    plugin_version = "3.0.0"
    # 插件作者
    plugin_author = "sssnto"
    # 作者主页
    author_url = "https://github.com/sssnto"
    # 插件配置项ID前缀
    plugin_config_prefix = "zspacemediafresh_"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    _onlyonce = False
    _cron = None
    _days = None
    _timescope =None 
    _waittime = None
    _zspcookie=None
    _zsphost=None
    _moivelib=None
    _tvlib=None
    _flushall = False
    _startswith = None
    _notify = False
    _notifyaggregation = False
    _unit=None
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            self._days = config.get("days") or 1
            self._timescope = config.get("timescope") or 1440
            self._waittime = config.get("waittime") or 60
            self._zspcookie=config.get("zspcookie")
            self._zsphost=config.get("zsphost")
            self._moivelib = config.get("moivelib")
            self._tvlib=config.get("tvlib")
            self._flushall=config.get("flushall")
            self._startswith=config.get("startswith")
            self._notify = config.get("notify")
            self._notifyaggregation =config.get("notifyaggregation")
            self._unit =config.get("unit") or "day"
            if self._zsphost:           
                if not self._zsphost.startswith("http"):
                    self._zsphost = "http://" + self._zsphost
                if  self._zsphost.endswith("/"):
                    self._zsphost = self._zsphost[:-1]
            # 加载模块
            if self._enabled or self._onlyonce:
                # 定时服务
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)

                # 立即运行一次
                if self._onlyonce:
                    logger.info(f"极影视刷新服务启动，立即运行一次")
                    self._scheduler.add_job(self.refresh, 'date',
                                            run_date=datetime.now(
                                                tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                            name="极影视刷新")
                    # 关闭一次性开关
                    self._onlyonce = False
                    # 保存配置
                    self.__update_config()
                # 周期运行
                if self._cron:
                    try:
                        self._scheduler.add_job(func=self.refresh,
                                                trigger=CronTrigger.from_crontab(self._cron),
                                                name="极影视刷新")
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
                "timescope":self._timescope,
                "waittime": self._waittime,
                "zspcookie": self._zspcookie,
                "zsphost": self._zsphost,
                "moivelib": self._moivelib,
                "tvlib": self._tvlib,
                "flushall": self._flushall,
                "startswith":self._startswith,
                "notify": self._notify,
                "notifyaggregation":self._notifyaggregation,
                "unit":self._unit
            }
        )

    def refresh(self):
        """
        刷新极影视
        """
        classify_list = []
        if not self._flushall:
            # 参数验证
            if not self._startswith:
                logger.error(f"网盘媒体库路径未设置")
                return           
            #获取days内入库的媒体
            current_date = datetime.now()
            if self._unit =="day":
                target_date = current_date - timedelta(days=int(self._timescope)) 
            elif  self._unit =="hour":    
                target_date = current_date - timedelta(hours=int(self._timescope)) 
            elif  self._unit =="minute":
                target_date = current_date - timedelta(minutes=int(self._timescope)) 
            else:
                 logger.info(f"时间范围单位未设置")
                 return
            transferhistorys = TransferHistoryOper().list_by_date(target_date.strftime('%Y-%m-%d %H:%M:%S'))
            if not transferhistorys:
                logger.info(f"{self._timescope} {self._unit}内没有媒体库入库记录")
                return
            #匹配指定路径的入库数据
            filtered_transferhistorys = [th for th in transferhistorys if th.status == 1 and th.dest is not None and th.dest.startswith(self._startswith) ]
            if  not filtered_transferhistorys :
                logger.info(f"{self._timescope} {self._unit}内没有网盘媒体库的记录")
                return
            # 提取顶级分类  电影或电视剧
            unique_types = set([th.type for th in filtered_transferhistorys])
            types_list =list(unique_types)
            if "电影" in types_list:
                moivelib_list = self._moivelib.replace("，", ",").split(",")
            else:
                moivelib_list = []
            if "电视剧" in types_list:
                tvlib_list = self._tvlib.replace("，", ",").split(",")
            else:
                tvlib_list = []
            classify_list = moivelib_list + tvlib_list
            logger.info(f"开始刷新极影视，最近{self._timescope} {self._unit}内网盘入库媒体：{len(filtered_transferhistorys)}个,需刷新媒体库：{classify_list}")
        # 刷新极影视
        self.__refresh_zspmedia(classify_list)
        logger.info(f"刷新极影视完成")

    @eventmanager.register(EventType.PluginAction)
    def remote_sync(self, event: Event):
        """
        远程刷新媒体库
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "zsp_media_refresh":
                return
            self.post_message(channel=event.event_data.get("channel"),
                              title="开始刷新极影视 ...",
                              userid=event.event_data.get("user"))
        self.refresh()
        if event:
            self.post_message(channel=event.event_data.get("channel"),
                              title="刷新极影视完成！", userid=event.event_data.get("user"))

    def __refresh_zspmedia(self,classify_list):
        """
        刷新极影视
        """

        # logger.info(f"_zsphost ：{self._zsphost}")
        # logger.info(f"_zspcookie ：{self._zspcookie}")

        if not self._zsphost or not self._zspcookie:
            logger.error("极空间主机地址或cookie未配置")
            return False

        logger.info(f"cookie parse start")

        try:
            cookie = cookie_parse(self._zspcookie)
            logger.info(f"cookie解析成功：{cookie}")
        except Exception as e:
            logger.error(f"cookie解析失败：{str(e)}")
            return False

        # 检查必要的cookie字段
        required_fields = ['token', 'device_id', 'device', 'version', '_l', 'nas_id']
        missing_fields = []
        for field in required_fields:
            if field not in cookie:
                missing_fields.append(field)
        
        if missing_fields:
            logger.error(f"cookie中缺少必要字段：{missing_fields}")
            return False

        token = cookie['token']
        logger.info(f"token ：{token}")
        device_id = cookie['device_id']
        logger.info(f"device_id ：{device_id}")
        device = cookie['device']
        logger.info(f"device ：{device}")
        version = cookie['version']
        logger.info(f"version ：{version}")
        _l = cookie['_l']
        logger.info(f"_l ：{_l}")
        nasid = cookie['nas_id']
        logger.info(f"nasid ：{nasid}")

        msgtext= None
        total_msgtext = ""

        # 获取分类列表
        list_url = "%s/zvideo/classification/list?&rnd=%s&webagent=v2" % (self._zsphost, self.generate_string() )
        logger.info(f"获取极影视分类URL ：{list_url}")

        try:
            logger.info(f"获取极影视分类cookies ：{self._zspcookie}")

            rsp_body=RequestUtils(cookies=self._zspcookie).post_res(list_url)
            logger.info(f"获取极影视分类rsp_body ：{rsp_body}")

            res = rsp_body.json()
            logger.debug(f"获取极影视分类 ：{res}")
            if res and res["code"] == "200":
                if res["data"] and isinstance(res["data"], list):
                    # 获取分类ID
                    name_id_dict = {item["name"]: item["id"] for item in res['data']}
                    # 是否全类型刷新
                    if self._flushall :
                            classify_list = [item["name"] for item in res['data']]
                    for classify in classify_list:
                        if classify not in name_id_dict:
                            logger.info(f"分类 {classify} 不存在于极影视分类列表中，跳过刷新")
                            continue
                        # 提交刷新请求
                        rescan_url = "%s/zvideo/classification/rescan?&rnd=%s&webagent=v2" % (self._zsphost, self.generate_string())
                        formdata = {"classification_id": name_id_dict[classify],"device_id":device_id,"token":token,"device":device,"plat":"web","_l":_l,"version":version,"nasid":nasid}
                        rescanres = RequestUtils(headers={"Content-Type": "application/x-www-form-urlencoded"},cookies=self._zspcookie).post_res(rescan_url,formdata)
                        rescanres_json = rescanres.json()
                        logger.debug(f"提交刷新请求--rescanres_json：{rescanres_json}")
                        start_time = time.time()# 记录开始时间
                        if rescanres_json["code"] =="200" and rescanres_json["data"]["task_id"]:
                            logger.info(f"分类：{classify}开始刷新，任务ID：{rescanres_json['data']['task_id']}")
                            # 查询刷新结果
                            result_url = "%s/zvideo/classification/rescan/result?&rnd=%s&webagent=v2" % (self._zsphost,self.generate_string())
                            formdata["task_id"] = rescanres_json['data']['task_id']
                            logger.debug(f"返回数据-----》：{formdata}")
                            while True:
                                #轮询状态
                                resultRep = RequestUtils(headers={"Content-Type": "application/x-www-form-urlencoded"},
                                                        cookies=self._zspcookie).post_res(result_url, formdata)
                                result_json = resultRep.json()
                                if result_json and result_json["code"] in ["200","N120024"] and result_json["data"]["task_status"] == 4:
                                    logger.info(f"分类：{classify} 刷新执行中,等待{self._waittime}秒，task_id：{rescanres_json['data']['task_id']}")
                                    time.sleep(int(self._waittime))  #任务状态进行中 等待
                                else:
                                    logger.info(f"分类：{classify} 刷新任务执行结束,task_id：{rescanres_json['data']['task_id']}，task_status:{result_json['data']['task_status']}")
                                    end_time = time.time()  # 记录结束时间
                                    msgtext =f"分类：{classify} 刷新成功\n"+f"开始时间： {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}\n"+f"用时： {int(end_time - start_time)} 秒\n"
                                    if not self._notifyaggregation and self._notify:
                                        self.post_message(
                                            mtype=NotificationType.Plugin,
                                            title="【刷新极影视】",
                                            text= msgtext)
                                    elif self._notifyaggregation and self._notify :
                                        total_msgtext += msgtext
                                    break
                        else:
                            logger.info(f"极影视获取分类列表出错：{rescanres_json}")
                    if  self._notifyaggregation and self._notify:
                        self.post_message(
                                mtype=NotificationType.Plugin,
                                title="【刷新极影视】",
                                text=total_msgtext)
            else:
                logger.info(f"极影视获取分类列表出错：{res}")
        except Exception as e:
            logger.error(f"极影视刷新出错：" + str(e))
            return False
        return False

    @staticmethod
    def generate_string():
        timestamp = str(time.time())  # 获取当前的时间戳
        four_digit_random = str(random.randint(1000,9999))  # 生成四位的随机数
        return f"{timestamp}_{four_digit_random}"  # 返回格式化后的字符串

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [{
            "cmd": "/zsp_media_refresh",
            "event": EventType.PluginAction,
            "desc": "极影视刷新",
            "category": "",
            "data": {
                "action": "zsp_media_refresh"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    # def get_service(self) -> List[Dict[str, Any]]:
    # """
    # 注册插件公共服务
    # [{
    #     "id": "服务ID",
    #     "name": "服务名称",
    #     "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
    #     "func": self.xxx,
    #     "kwargs": {} # 定时器参数
    # }]
    # """
    # if self._enabled and self._cron:
    #     return [
    #         {
    #             "id": "zspacemediafresh",
    #             "name": "刷新极空间媒体定时服务",
    #             "trigger": CronTrigger.from_crontab(self._cron),
    #             "func": self.refresh,
    #             "kwargs": {}
    #         }
    #     ]
    # else:
    #     return []


    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
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
                                    'md': 4
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
                                    'md': 4
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
                                            'model': 'flushall',
                                            'label': '刷新全部分类',
                                        }
                                    }
                                ]
                            },
                        ]
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
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '开启通知',
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
                                            'model': 'notifyaggregation',
                                            'label': '聚合通知',
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
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '5位cron表达式，留空不运行'
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
                                            'model': 'timescope',
                                            'label': '时间范围',
                                            'placeholder': '1',
                                            'hint': '指定之内有网盘资源入库才刷新'
                                        }
                                    }
                                ]
                            },
                            {

                                "component": "VCol",
                                "props": {"cols": 12,'md': 2},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "chips": True,
                                            "multiple": False,
                                            "model": "unit",
                                            "label": "单位",
                                            "items": [{"title": "天", "value": "day"},
                                                        {"title": "小时", "value": "hour"},
                                                        {"title": "分钟", "value": "minute"}
                                                        ],
                                            'hint': '时间控制单位'
                                        },
                                    }
                                ],
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
                                            'model': 'waittime',
                                            'label': '等待时间(秒)',
                                            'placeholder': '60',
                                            'hint': '获取刷新状态的频率'
                                        }
                                    }
                                ]
                            }
                        ],
                    },{
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
                                            'model': 'startswith',
                                            'label': '网盘媒体库路径'
                                        }
                                    }
                                ]
                            },{
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'zsphost',
                                            'label': '极空间webip+端口',
                                            'placeholder': 'http://127.0.0.1:5055'
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
                                            'model': 'moivelib',
                                            'label': '电影分类名',
                                            'placeholder': '多个逗号分割',
                                            'rows': 6,
                                            'hint': '包含电影的极影视分类名，多个逗号分割'
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
                                            'model': 'tvlib',
                                            'label': '电视剧分类名',
                                            'placeholder': '多个逗号分割',
                                            'rows': 6,
                                            'hint': '包含电视剧、综艺、纪录片、动漫的极影视分类名，多个逗号分割'
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
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'zspcookie',
                                            'label': 'cookie',
                                            'rows': 5
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
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '- 网盘媒体库路径：MP网盘媒体库路径,可在历史记录或媒体库配置处查看 ' + '\n' +
                                                    '- cookie：极空间web端cookie' + '\n' +
                                                    '- 详细说明：https://raw.githubusercontent.com/gxterry/MoviePilot-Plugins/main/docs/zspacemediafresh.md',
                                            'style': 'white-space: pre-line;'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ],
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "cron": "5 1 * * *",
            "timescope": 1,
            "waittime":60,
            "unit":"day"
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