# -*- coding: utf-8 -*-
"""
@Time    : 2024/07/16 下午11:03
@Author  : Kend
@FileName: static_folder_server.py
@Software: PyCharm
@modifier:
"""

"""
功能：
    长期稳定：确保服务能够长时间稳定运行。
    将清理过期文件和更新索引分成两个独立的任务，避免任务耦合。
    定时任务的时间错开，清理过期文件在每天 0 点 0 分执行，更新索引在 0 点 5 分执行，避免任务冲突。
    服务重启时读取并更新覆盖索引：服务启动时会重新读取和更新索引。
    增量更新与全量更新参数：用户可以选择扫描所有文件（全量更新）或仅更新新增和修改过的文件（增量更新）。
    FileService 类封装：所有逻辑（文件服务、索引更新、定时任务）都封装在 FileService 类中。
    单独进程运行：通过 multiprocessing.Process 启动服务，确保它与主流程独立运行。
    start() 方法启动服务：该方法启动 FastAPI 服务并处理所有的文件和目录访问, 不再单独写文件的路由函数。
实现方案
    定时任务：使用 APScheduler 进行定时任务管理。
    增量和全量更新索引：通过参数 full_scan 来控制是否执行全量扫描。
    索引更新逻辑：包括检查六个月前的文件，删除并更新索引文件。
"""


# TODO  显示的问题
"""
待优化点：
1、健康检查接口：/health 接口返回一个简单的 JSON 响应 {"status": "OK"}，表示服务正常运行。你可以通过定期轮询这个接口来监控服务的健康状态。
2：为了确保操作系统对文件句柄的数量没有过低的限制，特别是在高并发场景下，你需要检查并调整系统的文件句柄限制。
3、异步文件操作：
使用 aiofiles 替换了同步的文件读写操作，确保文件操作不会阻塞主线程。
load_index、save_index、serve_file 和 range_file_response 方法都改为异步方法，使用 await 关键字来等待 I/O 操作完成。
异步索引更新：
update_index 和 clean_old_files 方法也改为异步方法，确保定时任务不会阻塞主线程。
异步目录渲染：
render_directory 方法改为异步方法，确保目录渲染不会阻塞主线程。
4：	确认：对于定期定期清理定期清理不再需要的索引文件，每天凌晨不是都会更新索引文件
添加文件系统监控：
在 FileService 类的构造函数中，我们初始化了一个 Observer 实例，并注册了一个 FileChangeHandler 来处理文件系统事件。
FileChangeHandler 类继承自 FileSystemEventHandler，并重写了 on_modified、on_created 和 on_deleted 方法。每当文件被修改、创建或删除时，这些方法会被调用，并触发 update_index 方法进行增量更新。
启动和停止文件系统监控：
start_file_system_monitor 方法用于启动文件系统监控。
stop_file_system_monitor 方法用于在服务关闭时停止文件系统监控。添加文件系统监控：
在 FileService 类的构造函数中，我们初始化了一个 Observer 实例，并注册了一个 FileChangeHandler 来处理文件系统事件。
FileChangeHandler 类继承自 FileSystemEventHandler，并重写了 on_modified、on_created 和 on_deleted 方法。每当文件被修改、创建或删除时，这些方法会被调用，并触发 update_index 方法进行增量更新。
启动和停止文件系统监控：
start_file_system_monitor 方法用于启动文件系统监控。
stop_file_system_monitor 方法用于在服务关闭时停止文件系统监控。添加文件系统监控：
在 FileService 类的构造函数中，我们初始化了一个 Observer 实例，并注册了一个 FileChangeHandler 来处理文件系统事件。
FileChangeHandler 类继承自 FileSystemEventHandler，并重写了 on_modified、on_created 和 on_deleted 方法。每当文件被修改、创建或删除时，这些方法会被调用，并触发 update_index 方法进行增量更新。
启动和停止文件系统监控：
start_file_system_monitor 方法用于启动文件系统监控。
stop_file_system_monitor 方法用于在服务关闭时停止文件系统监控。添加文件系统监控：
在 FileService 类的构造函数中，我们初始化了一个 Observer 实例，并注册了一个 FileChangeHandler 来处理文件系统事件。
FileChangeHandler 类继承自 FileSystemEventHandler，并重写了 on_modified、on_created 和 on_deleted 方法。每当文件被修改、创建或删除时，这些方法会被调用，并触发 update_index 方法进行增量更新。
启动和停止文件系统监控：
start_file_system_monitor 方法用于启动文件系统监控。
stop_file_system_monitor 方法用于在服务关闭时停止文件系统监控。

结合增量更新和文件系统监控：
将增量更新和文件系统监控结合起来，既能保证在定时任务中定期检查是否有遗漏的文件变化，又能通过实时监控快速响应文件系统的变动。
"""



import os
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import time
from multiprocessing import Process
import mimetypes
import io
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import json
from datetime import datetime, timedelta


# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FileService:
    def __init__(self, folder_path: str, host: str = "0.0.0.0", port: int = 8000):
        """
        初始化静态文件服务器。

        :param folder_path: 要服务的文件夹路径。
        :param host: 服务器绑定的主机地址，默认为 "0.0.0.0"。
        :param port: 服务器监听的端口号，默认为 8000。
        """
        self.folder_path = os.path.abspath(folder_path)
        if not os.path.isdir(self.folder_path):
            raise ValueError(f"The path '{folder_path}' is not a valid directory.")

        self.host = host
        self.port = port
        self.process = None
        self.logger = logging.getLogger(__name__)

        # 初始化索引
        self.index = self.load_index()
        self.update_index(full_scan=True)  # 服务启动时进行全量更新

    def load_index(self) -> dict:
        """加载或初始化索引"""
        index_file = os.path.join(self.folder_path, "index.json")
        if os.path.exists(index_file):
            with open(index_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {}

    def save_index(self):
        """保存索引到文件"""
        index_file = os.path.join(self.folder_path, "index.json")
        with open(index_file, 'w', encoding='utf-8') as f:
            json.dump(self.index, f, ensure_ascii=False, indent=4)

    def update_index(self, full_scan: bool = False):
        """更新索引，支持增量更新和全量更新"""
        self.logger.info("Starting index update")
        if full_scan:
            self.logger.info("Performing full index scan")
            self.index = {}
            for root, _, files in os.walk(self.folder_path):
                for file in files:
                    file_path = os.path.relpath(os.path.join(root, file), self.folder_path)
                    self.index[file_path] = {
                        "last_modified": os.path.getmtime(os.path.join(root, file)),
                        "size": os.path.getsize(os.path.join(root, file))
                    }
        else:
            self.logger.info("Performing incremental index update")
            for root, _, files in os.walk(self.folder_path):
                for file in files:
                    file_path = os.path.relpath(os.path.join(root, file), self.folder_path)
                    if file_path not in self.index or os.path.getmtime(os.path.join(root, file)) > self.index[file_path]["last_modified"]:
                        self.index[file_path] = {
                            "last_modified": os.path.getmtime(os.path.join(root, file)),
                            "size": os.path.getsize(os.path.join(root, file))
                        }

        self.save_index()

    def clean_old_files(self):
        """清理过期文件"""
        threshold_date = datetime.now() - timedelta(days=180)
        for root, _, files in os.walk(self.folder_path):
            for file in files:
                full_path = os.path.join(root, file)
                if os.path.getmtime(full_path) < threshold_date.timestamp():
                    try:
                        os.remove(full_path)
                        self.logger.info(f"Deleted old file: {full_path}")
                    except Exception as e:
                        self.logger.error(f"Error deleting file {full_path}: {e}")

    def create_app(self) -> FastAPI:
        """
        创建并配置 FastAPI 应用程序。

        :return: 配置好的 FastAPI 应用程序实例。
        """
        app = FastAPI()

        # 启用 Gzip 压缩
        app.add_middleware(GZipMiddleware, minimum_size=1000)

        # 启用 CORS 支持
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # 允许所有来源，实际使用时应限制为特定域名
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # 相对路径路由
        @app.get("/{file_path:path}")
        async def serve_path(file_path: str, request: Request):
            """
            提供对文件和文件夹的访问：
            - 如果是文件，则返回文件内容。
            - 如果是文件夹，则返回文件夹内文件的 HTML 列表。
            """
            self.logger.info(f"Handling request for path: {file_path}")

            # 拼接文件或文件夹的完整路径
            full_path = os.path.abspath(os.path.join(self.folder_path, file_path))
            self.logger.info(f"Full path: {full_path}")

            # 确保路径在指定文件夹范围内
            if not os.path.commonpath([full_path, self.folder_path]) == self.folder_path:
                self.logger.warning(f"Access denied for path: {full_path}")
                raise HTTPException(status_code=403, detail="Access denied")

            # 路径不存在
            if not os.path.exists(full_path):
                self.logger.warning(f"Path not found: {full_path}")
                raise HTTPException(status_code=404, detail="File or directory not found")

            # 如果路径是文件夹，返回 HTML 格式的文件列表
            if os.path.isdir(full_path):
                self.logger.info(f"Rendering directory: {full_path}")
                return await self.render_directory(full_path, file_path)

            # 如果路径是文件，返回文件内容
            if os.path.isfile(full_path):
                self.logger.info(f"Serving file: {full_path}")
                return await self.serve_file(full_path, request)

            # 如果既不是文件也不是文件夹，返回 404 错误
            self.logger.error(f"Invalid path: {full_path}")
            raise HTTPException(status_code=404, detail="Invalid path")

        return app  # 返回 FastAPI 应用程序实例

    async def serve_file(self, full_path: str, request: Request) -> FileResponse:
        """
        处理文件请求，返回文件内容。
        :param full_path: 文件的完整路径。
        :param request: 请求对象。
        :return: FileResponse 或 StreamingResponse。
        """
        try:
            mime_type, _ = mimetypes.guess_type(full_path)
            if mime_type is None:
                if full_path.lower().endswith('.mp4'):
                    mime_type = 'video/mp4'
                else:
                    mime_type = 'application/octet-stream'

            # 判断是否是图片或视频文件
            is_image_or_video = mime_type and (mime_type.startswith("image/") or mime_type == 'video/mp4')

            # 检查 URL 参数，判断是否需要内联显示
            view_param = request.query_params.get('view', '').lower() == 'true'

            if is_image_or_video and view_param:
                # 如果是图片或视频文件，并且用户请求查看，则设置 Content-Disposition 为 inline
                content_disposition = "inline"
            else:
                # 默认情况下，所有文件都强制下载
                content_disposition = "attachment"

            range_header = request.headers.get('Range', None)
            if range_header and mime_type == 'video/mp4':
                return await self.range_file_response(full_path, range_header)
            else:
                headers = {
                    "Cache-Control": "public, max-age=86400",  # 缓存一天
                    "Content-Type": mime_type,  # 确保正确的 MIME 类型
                    "Content-Disposition": f"{content_disposition}; filename={os.path.basename(full_path)}",
                }

                return FileResponse(
                    full_path,
                    headers=headers
                )
        except Exception as e:
            self.logger.error(f"Error serving file: {e}")
            raise HTTPException(status_code=500, detail="Error serving file")

    async def range_file_response(self, full_path: str, range_header: str) -> StreamingResponse:
        """处理 MP4 文件的范围请求"""
        file_size = os.path.getsize(full_path)
        start, end = self.parse_range_header(range_header, file_size)
        length = end - start + 1

        headers = {
            'Content-Type': 'video/mp4',
            'Accept-Ranges': 'bytes',
            'Content-Range': f'bytes {start}-{end}/{file_size}',
            'Content-Length': str(length),
        }

        with open(full_path, 'rb') as file:
            file.seek(start)
            content = file.read(length)

        return StreamingResponse(io.BytesIO(content), status_code=206, headers=headers)

    def parse_range_header(self, range_header: str, file_size: int) -> (int, int):
        """解析 Range 请求头"""
        _, byte_range = range_header.split('=')
        start, end = byte_range.split('-')
        start = int(start) if start else 0
        end = int(end) if end else file_size - 1
        end = min(end, file_size - 1)
        return start, end

    async def render_directory(self, full_path: str, file_path: str) -> HTMLResponse:
        """渲染文件夹内容为 HTML 页面"""
        self.logger.info(f"Rendering directory: {full_path}")

        files = sorted(os.listdir(full_path))
        files_html = "\n".join([
            f'<li><a href="/{os.path.join(file_path, f)}" download="{f}">{f}</a> '
            f'<a href="/{os.path.join(file_path, f)}?view=true">[查看]</a></li>'
            if os.path.isfile(os.path.join(full_path, f)) and (f.lower().endswith(('.jpg', '.png', '.mp4'))) else
            f'<li><a href="/{os.path.join(file_path, f)}">{f}</a></li>'
            for f in files
        ])

        # 添加返回上一级目录的链接（如果不是根目录）
        if file_path:
            parent_dir = os.path.dirname(file_path)
            files_html = f'<li><a href="/{parent_dir if parent_dir != "." else ""}">../</a></li>\n' + files_html

        return HTMLResponse(
            content=f"""
            <html>
            <head><title>Index of /{file_path}</title></head>
            <body>
                <h1>Index of /{file_path}</h1>
                <ul>
                    {files_html}
                </ul>
            </body>
            </html>
            """,
            status_code=200,
        )

    def start_server(self):
        """启动 FastAPI 服务器并初始化定时任务"""
        app = self.create_app()  # 获取 FastAPI 应用程序实例
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="info")
        server = uvicorn.Server(config)

        # 在子进程中初始化并启动调度器
        scheduler = BackgroundScheduler()
        scheduler.add_job(self.clean_old_files, CronTrigger(hour=0, minute=0))
        scheduler.add_job(self.update_index, CronTrigger(hour=0, minute=5), kwargs={"full_scan": True})
        scheduler.start()

        server.run()

    def run_in_process(self):
        """在独立进程中启动服务器"""
        self.process = Process(target=self.start_server)
        self.process.start()
        self.logger.info("Static file server process started.")

    def stop_server(self):
        """停止服务器"""
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.process.join()
            self.logger.info("Static file server process stopped.")
        else:
            self.logger.warning("Static file server process is not running.")

    def is_running(self) -> bool:
        """检查服务器是否正在运行"""
        return self.process and self.process.is_alive()


"""
移除 BackgroundScheduler 作为类属性：不再将 BackgroundScheduler 作为 FileService 类的属性，而是将其初始化和启动逻辑移到 start_server 方法内部。
这样可以确保每个子进程都有自己独立的 BackgroundScheduler 实例，而不会尝试序列化它。
确保 start_server 方法独立：start_server 方法现在完全独立于 FileService 类的其他部分，只依赖于 self.folder_path 和 self.logger，
这些都是可以安全序列化的属性。
避免传递不可序列化的对象：通过这种方式，我们避免了在 multiprocessing 中传递 BackgroundScheduler 实例，从而解决了 TypeError 问题。

所有组件都按照预期工作。以下是关键点的总结：
索引更新：索引更新过程成功启动，并进行了全量扫描。
子进程启动：FastAPI 服务器成功在一个独立的子进程中启动。
定时任务：两个定时任务 (clean_old_files 和 update_index) 成功添加到调度器，并在调度器启动后被安排执行。
Uvicorn 服务器：Uvicorn 服务器成功启动，并在指定的地址和端口上监听 HTTP 请求。

全量更新确实是在服务启动时进行的，它会遍历整个文件夹并构建或更新索引。
但是，对于新增的文件，我们需要一种机制来及时发现和处理这些变化，而不需要每次都进行全量扫描，因为全量扫描可能会消耗较多的资源，尤其是在文件数量较多的情况下。

"""


if __name__ == "__main__":
    # 创建静态文件服务器实例
    static_server = FileService(folder_path=r"D:\kend\tests", host="127.0.0.1", port=8000)

    try:
        # 启动静态文件服务器
        static_server.run_in_process()

        # 主流程继续运行其他任务
        while True:
            # 定期检查静态文件服务器的状态
            if not static_server.is_running():
                static_server.run_in_process()  # 重新启动服务器
            time.sleep(10)  # 每10秒检查一次

    except KeyboardInterrupt:
        # 捕获 Ctrl+C
        static_server.stop_server()



"""
代码功能说明：
    load_index：在服务启动时调用，加载之前保存的 file_index.json 索引文件。
    save_index：将当前的索引保存到 file_index.json 文件中。
    update_index：更新索引文件，支持全量更新（full_scan=True）和增量更新（full_scan=False）。增量更新时，检查文件的修改时间，并移除过期文件。
    cleanup_old_files：每天0点清理六个月前的文件，并从索引中移除这些文件。
    serve_path：提供文件夹和文件的访问，支持分页显示文件夹中的内容，如果是文件则直接返回该文件。
"""




