# -*- coding: utf-8 -*-
"""
NeXAS Switch Toolkit - Lightweight Local Web GUI Console
Zero third-party heavy dependencies. Built on Python's native http.server.
Provides a modern dark-mode browser interface at http://127.0.0.1:8899
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socketserver
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.parse
import webbrowser

from .patch.emulator_sync import EmulatorSync
from .patch.romfs_builder import RomFsBuilder
from .pipeline import PipelineConfig, PipelineRunner

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8899

# Global state for background pipeline execution
class WebServerState:
    is_running: bool = False
    progress: float = 0.0
    current_stage: str = "Ready"
    logs: List[Dict[str, str]] = []
    generated_zip: Optional[str] = None
    last_result: Optional[Dict[str, Any]] = None
    runner_thread: Optional[threading.Thread] = None

    @classmethod
    def reset(cls):
        cls.is_running = True
        cls.progress = 0.0
        cls.current_stage = "Initializing..."
        cls.logs = []
        cls.generated_zip = None
        cls.last_result = None

    @classmethod
    def add_log(cls, msg: str, level: str = "INFO"):
        cls.logs.append({"timestamp": time.strftime("%H:%M:%S"), "message": msg, "level": level})


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NeXAS Switch Toolkit - 自动化移植工作台</title>
    <style>
        :root {
            --bg-primary: #0f111a;
            --bg-card: #181b28;
            --border-color: #272c42;
            --text-primary: #e6edf3;
            --text-muted: #8b949e;
            --accent-blue: #00d2ff;
            --accent-red: #ff3366;
            --accent-green: #00f2a9;
            --accent-yellow: #f1e05a;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: var(--bg-primary); color: var(--text-primary); padding: 24px; }
        .container { max-width: 1100px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border-color); }
        .logo-title { display: flex; align-items: center; gap: 12px; }
        .logo-badge { background: linear-gradient(135deg, var(--accent-red), var(--accent-blue)); color: #fff; padding: 6px 12px; border-radius: 6px; font-weight: bold; font-size: 14px; letter-spacing: 1px; }
        h1 { font-size: 22px; font-weight: 700; }
        .subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
        .card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 10px; padding: 20px; }
        .card-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; color: var(--accent-blue); }
        .form-group { margin-bottom: 16px; }
        label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; color: var(--text-muted); }
        input[type="text"], select { width: 100%; background: #0b0d14; border: 1px solid var(--border-color); color: #fff; padding: 10px 12px; border-radius: 6px; font-size: 13px; outline: none; transition: border-color 0.2s; }
        input[type="text"]:focus, select:focus { border-color: var(--accent-blue); }
        .checkbox-group { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 8px; }
        .checkbox-label { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-primary); cursor: pointer; }
        input[type="checkbox"] { width: 16px; height: 16px; accent-color: var(--accent-blue); }
        .btn-bar { display: flex; gap: 12px; margin-bottom: 24px; }
        button { cursor: pointer; padding: 12px 24px; border-radius: 8px; font-size: 14px; font-weight: 600; border: none; transition: all 0.2s; display: inline-flex; align-items: center; gap: 8px; }
        .btn-primary { background: linear-gradient(135deg, #0072ff, #00d2ff); color: #fff; }
        .btn-primary:hover:not(:disabled) { box-shadow: 0 0 15px rgba(0, 210, 255, 0.4); transform: translateY(-1px); }
        .btn-secondary { background: #272c42; color: #fff; }
        .btn-secondary:hover:not(:disabled) { background: #343b59; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        /* Progress & Console */
        .progress-box { margin-bottom: 16px; }
        .progress-header { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 8px; }
        .progress-bar-bg { width: 100%; height: 10px; background: #0b0d14; border-radius: 5px; overflow: hidden; border: 1px solid var(--border-color); }
        .progress-bar-fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--accent-blue), var(--accent-green)); transition: width 0.3s ease; }
        .console-window { background: #090a0f; border: 1px solid var(--border-color); border-radius: 8px; padding: 14px; height: 320px; overflow-y: auto; font-family: "Cascadia Code", Consolas, monospace; font-size: 12px; line-height: 1.6; }
        .log-entry { margin-bottom: 4px; word-break: break-all; }
        .log-time { color: var(--text-muted); margin-right: 8px; }
        .log-INFO { color: #a5d6ff; }
        .log-SUCCESS { color: var(--accent-green); font-weight: bold; }
        .log-WARN { color: var(--accent-yellow); }
        .log-ERROR { color: var(--accent-red); font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-title">
                <div class="logo-badge">SWITCH NeXAS</div>
                <div>
                    <h1>NeXAS Engine PC ➔ Switch 跨平台移植系统</h1>
                    <div class="subtitle">Entergram 官方母体引擎资产自动化编译与 RomFS 打包管线</div>
                </div>
            </div>
            <div id="statusBadge" style="font-size: 13px; color: var(--accent-green);">● 就绪 / Ready</div>
        </header>

        <div class="grid">
            <!-- Project & Platform Settings -->
            <div class="card">
                <div class="card-title">🎮 游戏与目标配置</div>
                <div class="form-group">
                    <label>目标游戏预设 / Game Preset</label>
                    <select id="presetSelect" onchange="applyPreset()">
                        <option value="aquarium">《Aquarium / 水族馆》凑あくあ (0100D11018A7E000)</option>
                        <option value="dc4fd">《D.C.4 Fortunate Departures》 (0100412014A86000)</option>
                        <option value="aikiss">《Aikiss / 艾奇斯》 (010065900EE78000)</option>
                        <option value="aikiss2">《Aikiss 2》 (0100F83013854000)</option>
                        <option value="custom">-- 自定义 Title ID --</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Nintendo Switch Title ID (16位Hex)</label>
                    <input type="text" id="titleIdInput" value="0100D11018A7E000">
                </div>
                <div class="form-group">
                    <label>项目/补丁显示名称</label>
                    <input type="text" id="projectNameInput" value="Aquarium_Switch_CHS_Patch">
                </div>
                <div class="form-group">
                    <label>分辨率视口转换 (PC ➔ Switch)</label>
                    <div style="display: flex; gap: 10px;">
                        <select id="resSrcSelect" style="flex: 1;">
                            <option value="800x600">源: 800x600 (4:3)</option>
                            <option value="1024x768">源: 1024x768 (4:3)</option>
                            <option value="1280x720">源: 1280x720 (16:9)</option>
                        </select>
                        <select id="resDstSelect" style="flex: 1;">
                            <option value="1280x720">目标: 1280x720 (掌机)</option>
                            <option value="1920x1080">目标: 1920x1080 (底座)</option>
                        </select>
                        <select id="scaleModeSelect" style="flex: 1;">
                            <option value="letterbox">Letterbox等比居中</option>
                            <option value="stretch">Stretch全屏拉伸</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Path & Pipeline Options -->
            <div class="card">
                <div class="card-title">⚙️ 资产路径与编译选项</div>
                <div class="form-group">
                    <label>PC 原始游戏目录 / 待转换资源路径</label>
                    <input type="text" id="inputDir" placeholder="例如: D:/Games/Aquarium_PC 或 ./sample_assets">
                </div>
                <div class="form-group">
                    <label>Switch RomFS 补丁输出目录</label>
                    <input type="text" id="outputDir" value="./Switch_Output_Patch">
                </div>
                <div class="form-group">
                    <label>自动转码与增强特性</label>
                    <div class="checkbox-group">
                        <label class="checkbox-label"><input type="checkbox" id="chkSmartWrap" checked> 剧本智能断行 (避头避尾)</label>
                        <label class="checkbox-label"><input type="checkbox" id="chkImageAlign" checked> Tegra X1 16B 纹理对齐</label>
                        <label class="checkbox-label"><input type="checkbox" id="chkPremultiplyAlpha" checked> Alpha预乘通道处理</label>
                        <label class="checkbox-label"><input type="checkbox" id="chkAudioResample" checked> 48000 Hz 音频重采样</label>
                        <label class="checkbox-label"><input type="checkbox" id="chkRepackPac" checked> 重新编译 Switch PAC</label>
                        <label class="checkbox-label"><input type="checkbox" id="chkCreateZip" checked> 生成可刷机 Zip 包</label>
                        <label class="checkbox-label"><input type="checkbox" id="chkSyncEmu"> 自动同步至本地模拟器</label>
                        <label class="checkbox-label"><input type="checkbox" id="chkCreateNsp" onchange="toggleNspInput()"> 📦 制作独立可安装 NSP 封包 (.nsp)</label>
                        <div id="nspFields" style="display: none; margin-top: 8px;">
                            <label style="font-size: 11px; color: var(--accent-blue);">母体 main (NSO 可执行文件) 路径:</label>
                            <input type="text" id="mainNsoInput" placeholder="例如: F:/移植/output/nsp_build/exefs/main" style="margin-top: 4px;">
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Buttons -->
        <div class="btn-bar">
            <button class="btn-primary" id="btnStart" onclick="startPipeline()">
                <span>🚀</span> 启动一键全自动移植流水线
            </button>
            <button class="btn-secondary" id="btnDownload" onclick="downloadZip()" disabled>
                <span>📦</span> 下载 Atmosphère 补丁 Zip
            </button>
            <button class="btn-secondary" id="btnOpenFolder" onclick="openOutputFolder()">
                <span>📂</span> 打开输出目录
            </button>
        </div>

        <!-- Progress and Console Output -->
        <div class="card">
            <div class="progress-box">
                <div class="progress-header">
                    <span id="stageLabel">就绪等待任务...</span>
                    <span id="percentLabel">0%</span>
                </div>
                <div class="progress-bar-bg">
                    <div class="progress-bar-fill" id="progressBar"></div>
                </div>
            </div>

            <div class="console-window" id="consoleBox">
                <div class="log-entry"><span class="log-time">[System]</span><span class="log-INFO">NeXAS Switch Toolkit 本地控制台就绪。请配置路径后点击“启动一键全自动移植流水线”。</span></div>
            </div>
        </div>
    </div>

    <script>
        const PRESETS = {
            "aquarium": "0100D11018A7E000",
            "dc4fd": "0100412014A86000",
            "aikiss": "010065900EE78000",
            "aikiss2": "0100F83013854000"
        };

        function applyPreset() {
            const val = document.getElementById("presetSelect").value;
            if (PRESETS[val]) {
                document.getElementById("titleIdInput").value = PRESETS[val];
            }
        }

        function toggleNspInput() {
            const chk = document.getElementById("chkCreateNsp").checked;
            document.getElementById("nspFields").style.display = chk ? "block" : "none";
        }

        let isPolling = false;

        async function startPipeline() {
            const payload = {
                project_name: document.getElementById("projectNameInput").value.trim(),
                title_id: document.getElementById("titleIdInput").value.trim(),
                input_dir: document.getElementById("inputDir").value.trim(),
                output_dir: document.getElementById("outputDir").value.trim(),
                resolution_src: document.getElementById("resSrcSelect").value.split("x").map(Number),
                resolution_dst: document.getElementById("resDstSelect").value.split("x").map(Number),
                scaling_mode: document.getElementById("scaleModeSelect").value,
                enable_smart_wrap: document.getElementById("chkSmartWrap").checked,
                image_alignment: document.getElementById("chkImageAlign").checked ? 16 : 1,
                premultiply_alpha: document.getElementById("chkPremultiplyAlpha").checked,
                audio_sample_rate: document.getElementById("chkAudioResample").checked ? 48000 : 0,
                repack_pac: document.getElementById("chkRepackPac").checked,
                create_zip: document.getElementById("chkCreateZip").checked,
                sync_emulator: document.getElementById("chkSyncEmu").checked,
                create_nsp: document.getElementById("chkCreateNsp").checked,
                main_nso_path: document.getElementById("mainNsoInput") ? document.getElementById("mainNsoInput").value.trim() : ""
            };

            if (!payload.input_dir) {
                alert("请先输入有效的 PC 游戏或资源目录！");
                return;
            }

            document.getElementById("btnStart").disabled = true;
            document.getElementById("statusBadge").innerText = "● 正在执行中...";
            document.getElementById("statusBadge").style.color = "var(--accent-blue)";

            try {
                const res = await fetch("/api/start", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.status === "started") {
                    if (!isPolling) {
                        isPolling = true;
                        pollStatus();
                    }
                } else {
                    alert("启动失败: " + data.message);
                    document.getElementById("btnStart").disabled = false;
                }
            } catch (e) {
                alert("网络错误: " + e.message);
                document.getElementById("btnStart").disabled = false;
            }
        }

        async function pollStatus() {
            try {
                const res = await fetch("/api/status");
                const data = await res.json();

                // Update Progress
                const pct = Math.round(data.progress * 100);
                document.getElementById("progressBar").style.width = pct + "%";
                document.getElementById("percentLabel").innerText = pct + "%";
                document.getElementById("stageLabel").innerText = data.current_stage;

                // Update Logs
                const box = document.getElementById("consoleBox");
                box.innerHTML = "";
                data.logs.forEach(l => {
                    const row = document.createElement("div");
                    row.className = "log-entry";
                    row.innerHTML = `<span class="log-time">[${l.timestamp}]</span><span class="log-${l.level}">${escapeHtml(l.message)}</span>`;
                    box.appendChild(row);
                });
                box.scrollTop = box.scrollHeight;

                if (data.generated_zip) {
                    document.getElementById("btnDownload").disabled = false;
                }

                if (data.is_running) {
                    setTimeout(pollStatus, 800);
                } else {
                    isPolling = false;
                    document.getElementById("btnStart").disabled = false;
                    if (data.last_result && data.last_result.status === "success") {
                        document.getElementById("statusBadge").innerText = "● 移植完成 / Success";
                        document.getElementById("statusBadge").style.color = "var(--accent-green)";
                    } else if (data.last_result && data.last_result.status === "error") {
                        document.getElementById("statusBadge").innerText = "● 发生异常 / Error";
                        document.getElementById("statusBadge").style.color = "var(--accent-red)";
                    }
                }
            } catch (e) {
                setTimeout(pollStatus, 2000);
            }
        }

        function downloadZip() {
            window.location.href = "/api/download_zip";
        }

        function openOutputFolder() {
            const out = document.getElementById("outputDir").value;
            fetch("/api/open_folder?path=" + encodeURIComponent(out));
        }

        function escapeHtml(text) {
            return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }
    </script>
</body>
</html>
"""


class WebGuiRequestHandler(BaseHTTPRequestHandler):
    """
    HTTP Request Handler for the embedded NeXAS web console.
    """

    def log_message(self, format, *args):
        # Silence default terminal request spam
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(INDEX_HTML.encode("utf-8"))

        elif path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            payload = {
                "is_running": WebServerState.is_running,
                "progress": WebServerState.progress,
                "current_stage": WebServerState.current_stage,
                "logs": WebServerState.logs,
                "generated_zip": WebServerState.generated_zip,
                "last_result": WebServerState.last_result,
            }
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        elif path == "/api/presets":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            presets = {
                "aquarium": {
                    "name": "《Aquarium / 水族馆》凑あくあ",
                    "title_id": "0100D11018A7E000",
                    "resolution_src": [800, 600],
                    "resolution_dst": [1280, 720],
                },
                "dc4fd": {
                    "name": "《D.C.4 Fortunate Departures》",
                    "title_id": "0100412014A86000",
                    "resolution_src": [1280, 720],
                    "resolution_dst": [1280, 720],
                },
                "aikiss": {
                    "name": "《Aikiss / 艾奇斯》",
                    "title_id": "010065900EE78000",
                    "resolution_src": [800, 600],
                    "resolution_dst": [1280, 720],
                },
            }
            self.wfile.write(json.dumps(presets, ensure_ascii=False).encode("utf-8"))

        elif path == "/api/download_zip":
            if WebServerState.generated_zip and os.path.exists(WebServerState.generated_zip):
                zip_path = Path(WebServerState.generated_zip)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header(
                    "Content-Disposition", f"attachment; filename=\"{zip_path.name}\""
                )
                self.send_header("Content-Length", str(zip_path.stat().st_size))
                self.end_headers()
                with open(zip_path, "rb") as zf:
                    shutil.copyfileobj(zf, self.wfile)
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"No generated ZIP file available.")

        elif path == "/api/open_folder":
            query = urllib.parse.parse_qs(parsed.query)
            target_path = query.get("path", [""])[0]
            if target_path and os.path.exists(target_path):
                if sys.platform == "win32":
                    os.startfile(os.path.abspath(target_path))
                elif sys.platform == "darwin":
                    os.system(f"open '{target_path}'")
                else:
                    os.system(f"xdg-open '{target_path}'")
            self.send_response(200)
            self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/start":
            if WebServerState.is_running:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Pipeline already running"}).encode("utf-8"))
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))

            cfg = PipelineConfig(
                project_name=data.get("project_name", "NeXAS_Switch_Port"),
                title_id=data.get("title_id", "0100D11018A7E000"),
                input_dir=data.get("input_dir", ""),
                output_dir=data.get("output_dir", "./Switch_Output_Patch"),
                resolution_src=tuple(data.get("resolution_src", [800, 600])),
                resolution_dst=tuple(data.get("resolution_dst", [1280, 720])),
                scaling_mode=data.get("scaling_mode", "letterbox"),
                enable_smart_wrap=data.get("enable_smart_wrap", True),
                image_alignment=data.get("image_alignment", 16),
                premultiply_alpha=data.get("premultiply_alpha", True),
                audio_sample_rate=data.get("audio_sample_rate", 48000),
                repack_pac=data.get("repack_pac", True),
                create_zip=data.get("create_zip", True),
                sync_emulator=data.get("sync_emulator", False),
            )

            WebServerState.reset()

            def run_worker():
                def on_log(msg, lvl):
                    WebServerState.add_log(msg, lvl)

                def on_prog(pct, desc):
                    WebServerState.progress = pct
                    WebServerState.current_stage = desc

                runner = PipelineRunner(cfg, log_callback=on_log, progress_callback=on_prog)
                try:
                    res = runner.run()
                    WebServerState.last_result = res
                    if "patch_results" in res and "zip" in res["patch_results"]:
                        WebServerState.generated_zip = res["patch_results"]["zip"]
                except Exception as e:
                    WebServerState.add_log(f"Fatal error in pipeline execution: {e}", "ERROR")
                    WebServerState.last_result = {"status": "error", "message": str(e)}
                finally:
                    WebServerState.is_running = False

            t = threading.Thread(target=run_worker, daemon=True)
            WebServerState.runner_thread = t
            t.start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started"}).encode("utf-8"))


def start_gui_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    """
    Launch the embedded HTTP Web Console.
    """
    server_address = (host, port)
    httpd = HTTPServer(server_address, WebGuiRequestHandler)
    url = f"http://{host}:{port}"
    print(f"[*] NeXAS Switch Toolkit Web Console running at: {url}")
    print("[*] Press Ctrl+C to terminate the local server.")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down Web Console.")
        httpd.server_close()
