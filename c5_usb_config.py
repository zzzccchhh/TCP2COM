"""C5 USB configuration client over the existing single-owner log connection."""

from c5_config import ConfigError, SaveOutcomeUnknown, public_config


class C5USBClient:
    def __init__(self, monitor, timeout=4):
        self.monitor = monitor
        self.timeout = timeout
        self.identity = monitor.config_identity()

    def _request(self, operation, patch=None):
        frame = self.monitor.request(operation, patch, boot=self.identity[3],
                                     generation=self.identity[2], session=self.identity[4], timeout=self.timeout)
        if frame["ok"] is not True:
            code = frame.get("code")
            if code == "save_unknown" or (operation == "save" and code not in ("invalid", "busy", "stale")):
                raise SaveOutcomeUnknown("保存结果待确认：未收到有效保存确认，请重连后读取核对")
            messages = {"busy": "C5 正在重启，请恢复后重新进入配网并读取",
                        "stale": "C5 已重启或配网会话已改变，当前编辑依据失效，请重新读取",
                        "invalid": "C5 拒绝配置：参数、字段或协议版本不正确",
                        "memory": "C5 内存不足，请稍后重新读取"}
            raise ConfigError(messages.get(code if isinstance(code, str) else "", "C5 拒绝 USB 配置请求，请检查固件版本"))
        result = frame.get("result")
        if not isinstance(result, dict):
            if operation == "save":
                raise SaveOutcomeUnknown("保存结果待确认：USB 回复缺少结果，请重新读取核对")
            raise ConfigError("USB 配置回复格式不正确")
        if operation in ("read", "validate", "save") and (frame["boot"], frame["session"]) != self.identity[3:]:
            if operation == "save":
                raise SaveOutcomeUnknown("保存结果待确认：设备已重启，请重新读取核对")
            raise ConfigError("设备已重启或配网会话改变，请重新读取配置")
        return frame, result

    def read(self):
        hello, info = self._request("hello")
        if info.get("device") != "ESP32-C5" or type(info.get("protocol")) is not int or info["protocol"] != 2:
            raise ConfigError("该 COM 不是支持 USB 配置的 C5，请检查端口和固件版本")
        self.identity = (*self.identity[:3], hello["boot"], hello["session"])
        frame, document = self._request("read")
        if frame["boot"] != hello["boot"]:
            raise ConfigError("读取期间 C5 已重启，请重新读取")
        self.identity = (*self.identity[:3], frame["boot"], frame["session"])
        result = public_config(document)
        result["config_source"] = str(document.get("config_source", "unknown"))
        return result

    def save(self, patch):
        if not self.identity[3]:
            raise ConfigError("请先通过当前 USB 连接读取 C5")
        _, validation = self._request("validate", patch)
        if validation.get("valid") is not True:
            raise ConfigError("C5 未确认配置校验通过，未发送保存命令")
        _, result = self._request("save", patch)
        if result.get("saved") is not True or result.get("restart") is not True:
            raise SaveOutcomeUnknown("保存结果待确认：未收到 USB 保存及重启确认，请重新读取核对")
        return result
