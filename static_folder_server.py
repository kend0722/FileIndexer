# -*- coding: utf-8 -*-
"""
@Time    : 2024/07/16 下午9:39
@Author  : Kend
@FileName: static_folder_server.py
@Software: PyCharm
@modifier:

FileIndexer 项目的行为：
    文件夹链接：
        当用户点击文件夹链接时，服务器会正确识别这是一个文件夹，并返回文件夹内容的 HTML 页面，列出该文件夹中的所有文件和子文件夹。
    文件链接：
        对于图片和视频文件，默认情况下会触发下载，但用户可以通过点击 [查看] 链接在浏览器中查看或播放文件
    其他文件类型（如 .txt, .pt, .pdf, .zip 等）：
        点击链接后，文件会直接下载，不会在浏览器中显示。
注意：
    serve_file()方法决定是否是下载还是内联显示;
    对于所有文件类型，默认设置 Content-Disposition: attachment 以强制下载
    当用户点击“查看”链接时，服务器会检查 URL 中是否有 ?view=true 参数。
    如果有，则设置 Content-Disposition: inline，允许浏览器内联显示；
    否则，继续强制下载。
    “查看”链接：
    在生成的 HTML 页面中，为图片和视频文件添加了一个 [查看] 链接。
    用户可以通过点击这个链接来查看文件，而不是直接下载。
    对于其他文件类型，只提供下载链接。
    如果不想使用显示的功能，需要设置 Content-Disposition: attachment
    下载还是内联显示主要功能看 serve_file 方法 和 render_directory 方法
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


# # 配置日志
# logging.basicConfig(level=logging.INFO)


class StaticFileServer:
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
        """启动 FastAPI 服务器"""
        app = self.create_app()  # 获取 FastAPI 应用程序实例
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="info")
        server = uvicorn.Server(config)
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



# 示例：如何在自己的主流程中使用 StaticFileServer
if __name__ == "__main__":
    # 创建静态文件服务器实例
    static_server = StaticFileServer(folder_path=r"D:\kend\tests", host="127.0.0.1", port=8000)

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
