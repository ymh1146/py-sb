#!/usr/bin/env python3
"""
Sing-box Argo 启动脚本（精简版）
仅保留 Argo + VLESS WS，并输出单条订阅。

需要在容器环境变量中至少配置：
1. ARGO_TOKEN: Cloudflare Tunnel Token
2. ARGO_DOMAIN: 绑定到该 Tunnel 的域名

可选环境变量：
1. UUID: 自定义 UUID
2. ARGO_PORT: 本地 VLESS 监听端口，默认 8080
3. CF_DOMAIN: 优选域名，默认随机
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


class Config:
    DEFAULT_UUID: str = "c9425af1-73fc-4b42-a473-b7d90f47591e"
    CF_DOMAINS: List[str] = [
        "cf.090227.xyz",
        "cf.877774.xyz",
        "cf.130519.xyz",
        "cf.008500.xyz",
        "store.ubi.com",
        "saas.sin.fan",
    ]
    DOWNLOAD_RETRY: int = 3
    DOWNLOAD_TIMEOUT: int = 120


class Utils:
    @staticmethod
    def get_env(name: str, default: str = "") -> str:
        value = os.environ.get(name, default).strip()
        return value

    @staticmethod
    def get_arch() -> Tuple[str, str]:
        import platform

        arch = platform.machine()
        if arch == "aarch64":
            return "arm64", "arm64"
        return "amd64", "amd64"

    @staticmethod
    def download_file(url: str, output: Path, timeout: int = 60, retry: int = 3) -> bool:
        import urllib.request

        for attempt in range(retry):
            try:
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'curl/7.88.1')
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    output.write_bytes(resp.read())
                try:
                    os.chmod(output, 0o755)
                except Exception:
                    pass
                logger.info(f"[下载] {output.name} 完成")
                return True
            except Exception as exc:
                logger.warning(f"[下载] {output.name} 失败 (尝试 {attempt + 1}/{retry}): {exc}")
                if attempt < retry - 1:
                    time.sleep(2)
        logger.error(f"[下载] {output.name} 多次失败")
        return False

    @staticmethod
    def select_cf_domain(domains: List[str]) -> str:
        import random

        return random.choice(domains)


class ArgoTunnel:
    def __init__(self, argo_binary: Path, local_port: int, argo_token: str):
        self.argo_binary = argo_binary
        self.local_port = local_port
        self.argo_token = argo_token
        self.process: Optional[subprocess.Popen] = None
        self.log_fd = None

    def start(self, log_file: Path) -> None:
        cmd = [
            str(self.argo_binary),
            "tunnel",
            "--no-autoupdate",
            "run",
            "--token",
            self.argo_token,
        ]
        self.log_fd = open(log_file, 'w', encoding='utf-8')
        self.process = subprocess.Popen(
            cmd,
            stdout=self.log_fd,
            stderr=subprocess.STDOUT,
            text=True,
        )
        logger.info(f"[Argo] 隧道进程已启动 PID: {self.process.pid}")

    def stop(self) -> None:
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        if self.log_fd:
            try:
                self.log_fd.close()
            except Exception:
                pass


class SingBox:
    def __init__(self, sb_binary: Path, config_path: Path):
        self.sb_binary = sb_binary
        self.config_path = config_path
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> bool:
        cmd = [str(self.sb_binary), "run", "-c", str(self.config_path)]
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(3)

        if self.process.poll() is not None:
            stdout, stderr = self.process.communicate(timeout=5)
            if stdout:
                logger.error(f"[SING-BOX] stdout: {stdout.strip()}")
            if stderr:
                logger.error(f"[SING-BOX] stderr: {stderr.strip()}")
            logger.error("[SING-BOX] 启动失败")
            return False

        logger.info(f"[SING-BOX] 已启动 PID: {self.process.pid}")
        return True

    def stop(self) -> None:
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class ConfigGenerator:
    def __init__(self, base_dir: Path, uuid_str: str, argo_port: int):
        self.base_dir = base_dir
        self.uuid = uuid_str
        self.argo_port = argo_port

    def generate(self) -> dict:
        return {
            "log": {"level": "warn"},
            "inbounds": [
                {
                    "type": "vless",
                    "tag": "vless-argo-in",
                    "listen": "127.0.0.1",
                    "listen_port": self.argo_port,
                    "users": [{"uuid": self.uuid}],
                    "transport": {
                        "type": "ws",
                        "path": f"/{self.uuid}-vless",
                    },
                }
            ],
            "outbounds": [{"type": "direct", "tag": "direct"}],
        }

    def save(self, path: Path) -> bool:
        try:
            path.write_text(json.dumps(self.generate(), indent=4, ensure_ascii=False), encoding='utf-8')
            return True
        except Exception as exc:
            logger.error(f"[CONFIG] 保存配置失败: {exc}")
            return False


class SingboxNode:
    def __init__(self):
        self.script_dir = Path(__file__).parent.absolute()
        self.work_dir = self.script_dir / ".npm"
        self.config = Config()
        self._shutdown_event = False

        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.uuid = Utils.get_env("UUID") or self.config.DEFAULT_UUID
        self.argo_token = Utils.get_env("ARGO_TOKEN") or Utils.get_env("CF_TOKEN")
        self.argo_domain = Utils.get_env("ARGO_DOMAIN") or Utils.get_env("TUNNEL_DOMAIN")
        self.argo_port = int(Utils.get_env("ARGO_PORT", "8080") or "8080")
        self.cf_domain = Utils.get_env("CF_DOMAIN") or Utils.select_cf_domain(self.config.CF_DOMAINS)

        self.sb_binary: Optional[Path] = None
        self.argo_binary: Optional[Path] = None
        self.singbox: Optional[SingBox] = None
        self.argo_tunnel: Optional[ArgoTunnel] = None

        if os.name != 'nt':
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info(f"[信号] 收到信号 {signum}，准备关闭...")
        self._shutdown_event = True
        self.cleanup()
        sys.exit(0)

    def validate_env(self) -> None:
        if not self.argo_token:
            logger.error("[环境变量] 缺少 ARGO_TOKEN（或 CF_TOKEN）")
            logger.error("[环境变量] 请在容器后台添加固定 Tunnel Token")
            sys.exit(1)

        if not self.argo_domain:
            logger.error("[环境变量] 缺少 ARGO_DOMAIN（或 TUNNEL_DOMAIN）")
            logger.error("[环境变量] 请填写绑定到该 Tunnel 的域名")
            sys.exit(1)

    def build_subscription(self) -> str:
        path = f"/{self.uuid}-vless"
        return (
            f"vless://{self.uuid}@{self.cf_domain}:443"
            f"?encryption=none&security=tls&sni={self.argo_domain}"
            f"&type=ws&host={self.argo_domain}&path=%2F{self.uuid}-vless"
            "#Argo-Node"
        )

    def run(self) -> None:
        logger.info("[初始化] Sing-box Argo 启动中...")
        self.validate_env()

        logger.info(f"[UUID] {self.uuid}")
        logger.info(f"[Argo] 域名: {self.argo_domain}")
        logger.info(f"[CF优选] {self.cf_domain}")
        logger.info(f"[本地端口] {self.argo_port}")

        sb_arch, argo_arch = Utils.get_arch()
        sb_base_url = f"https://{sb_arch}.ssss.nyc.mn"
        self.sb_binary = self.work_dir / "sb"
        self.argo_binary = self.work_dir / "cloudflared"

        logger.info(f"[下载] sing-box ({sb_arch})...")
        if not Utils.download_file(
            f"{sb_base_url}/sb",
            self.sb_binary,
            timeout=self.config.DOWNLOAD_TIMEOUT,
            retry=self.config.DOWNLOAD_RETRY,
        ):
            sys.exit(1)

        logger.info(f"[下载] cloudflared ({argo_arch})...")
        argo_url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{argo_arch}"
        if not Utils.download_file(
            argo_url,
            self.argo_binary,
            timeout=self.config.DOWNLOAD_TIMEOUT,
            retry=self.config.DOWNLOAD_RETRY,
        ):
            sys.exit(1)

        logger.info("[CONFIG] 生成配置...")
        config_path = self.work_dir / "config.json"
        config_gen = ConfigGenerator(self.work_dir, self.uuid, self.argo_port)
        if not config_gen.save(config_path):
            sys.exit(1)
        logger.info("[CONFIG] 配置已生成")

        logger.info("[SING-BOX] 启动中...")
        self.singbox = SingBox(self.sb_binary, config_path)
        if not self.singbox.start():
            sys.exit(1)

        logger.info("[Argo] 启动固定隧道...")
        argo_log = self.work_dir / "argo.log"
        self.argo_tunnel = ArgoTunnel(self.argo_binary, self.argo_port, self.argo_token)
        self.argo_tunnel.start(argo_log)

        sub = self.build_subscription()
        (self.work_dir / "sub.txt").write_text(sub + "\n", encoding='utf-8')

        logger.info("")
        logger.info("=" * 60)
        logger.info("Argo 订阅:")
        logger.info(sub)
        logger.info("=" * 60)
        logger.info("")
        logger.info("[提示] 请确认 Cloudflare Tunnel 的 Public Hostname 已指向 http://127.0.0.1:%s", self.argo_port)
        logger.info("[运行中] 按 Ctrl+C 停止")

        try:
            while not self._shutdown_event:
                time.sleep(1)
                if self.singbox and not self.singbox.is_running():
                    logger.warning("[警告] sing-box 进程已退出")
                    break
        except KeyboardInterrupt:
            logger.info("[停止] 正在关闭服务...")
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        if self.argo_tunnel:
            self.argo_tunnel.stop()
        if self.singbox:
            self.singbox.stop()
        logger.info("[停止] 服务已关闭")


if __name__ == "__main__":
    app = SingboxNode()
    app.run()
