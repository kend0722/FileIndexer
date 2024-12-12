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
import os
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import multiprocessing
import logging
from fastapi import FastAPI, HTTPException, Query, Request
from typing import Optional


# 配置日志记录
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class FileService:
    def __init__(self, folder_path: str, retention_time: timedelta = timedelta(days=180), full_scan_on_startup=True):
        self.folder_path = folder_path
        self.index_file = 'file_index.json'
        self.index = {}
        self.FILE_RETENTION_TIME = retention_time
        self.load_index()

        # 启动时是否执行全量扫描
        if full_scan_on_startup:
            self.full_scan_update_index()

    def load_index(self):
        """加载索引文件"""
        if os.path.exists(self.index_file):
            with open(self.index_file, 'r') as f:
                self.index = json.load(f)
                logger.info("Index loaded from file.")
        else:
            logger.info("No index file found. Starting with an empty index.")

    def save_index(self):
        """保存索引文件"""
        with open(self.index_file, 'w') as f:
            json.dump(self.index, f, indent=4)
            logger.info("Index saved to file.")

    def full_scan_update_index(self):
        """全量扫描并更新索引"""
        now = datetime.now()
        logger.info("Starting full scan update index...")
        self.index = {}

        # 确保根目录存在于索引中
        self.index[""] = {
            "is_dir": True,
            "size": 0,
            "modified_time": os.path.getmtime(self.folder_path),
        }

        for root, dirs, files in os.walk(self.folder_path):
            for name in dirs + files:
                full_path = os.path.join(root, name)
                relative_path = os.path.relpath(full_path, self.folder_path)
                relative_path = os.path.normpath(relative_path)
                file_metadata = {
                    "is_dir": os.path.isdir(full_path),
                    "size": os.path.getsize(full_path) if os.path.isfile(full_path) else 0,
                    "modified_time": os.path.getmtime(full_path),
                }
                file_modified_time = datetime.fromtimestamp(file_metadata["modified_time"])
                if now - file_modified_time > self.FILE_RETENTION_TIME:
                    continue
                self.index[relative_path] = file_metadata
                logger.debug(f"Indexed file: {relative_path}")
        self.save_index()
        logger.info("Full scan update index completed.")

    def incremental_update_index(self):
        """增量更新索引，只处理新增和修改过的文件"""
        now = datetime.now()
        logger.info("Starting incremental update index...")

        # 确保根目录存在于索引中
        if "" not in self.index:
            self.index[""] = {
                "is_dir": True,
                "size": 0,
                "modified_time": os.path.getmtime(self.folder_path),
            }

        updated_files = {}

        # 遍历文件夹，检查新增或修改过的文件
        for root, dirs, files in os.walk(self.folder_path):
            for name in dirs + files:
                full_path = os.path.join(root, name)
                relative_path = os.path.relpath(full_path, self.folder_path)
                relative_path = os.path.normpath(relative_path)

                # 如果文件不在索引中，或者文件的修改时间比索引中的记录新，则更新
                if relative_path not in self.index or os.path.getmtime(full_path) > self.index[relative_path][
                    "modified_time"]:
                    file_metadata = {
                        "is_dir": os.path.isdir(full_path),
                        "size": os.path.getsize(full_path) if os.path.isfile(full_path) else 0,
                        "modified_time": os.path.getmtime(full_path),
                    }
                    updated_files[relative_path] = file_metadata
                    logger.debug(f"Updated file: {relative_path}")

        # 更新索引
        self.index.update(updated_files)

        # 删除六个月前的文件
        for relative_path, metadata in list(self.index.items()):
            file_modified_time = datetime.fromtimestamp(metadata["modified_time"])
            if now - file_modified_time > self.FILE_RETENTION_TIME:
                del self.index[relative_path]
                logger.debug(f"Removed expired file: {relative_path}")

        self.save_index()
        logger.info("Incremental update index completed.")

    def update_index_task(self, full_scan=False):
        """定时任务，根据 full_scan 参数决定是全量更新还是增量更新索引"""
        if full_scan:
            self.full_scan_update_index()
        else:
            self.incremental_update_index()

    def cleanup_old_files(self):
        """清理过期文件"""
        now = datetime.now()
        logger.info("Starting cleanup of old files...")
        for relative_path, metadata in list(self.index.items()):
            file_modified_time = datetime.fromtimestamp(metadata["modified_time"])
            if now - file_modified_time > self.FILE_RETENTION_TIME:
                full_path = os.path.join(self.folder_path, relative_path)
                if os.path.exists(full_path):
                    try:
                        if metadata["is_dir"]:
                            os.rmdir(full_path)
                        else:
                            os.remove(full_path)
                        logger.info(f"Deleted expired file: {full_path}")
                    except Exception as e:
                        logger.error(f"Failed to delete file {full_path}: {e}")
                del self.index[relative_path]
        self.save_index()
        logger.info("Cleanup of old files completed.")

    def serve_path(self, file_path: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   inline: bool = False):
        """根据路径返回文件或目录"""
        # 规范化路径，确保路径格式一致
        file_path = os.path.normpath(file_path)

        # 处理根路径的情况
        if file_path == "" or file_path == ".":
            file_path = ""

        # 验证路径是否在 folder_path 内
        full_path = os.path.join(self.folder_path, file_path)
        if not os.path.commonpath([full_path, self.folder_path]) == self.folder_path:
            raise HTTPException(status_code=403, detail="Access denied")

        # 如果请求的是根路径或子目录，列出目录下的所有文件和子目录
        if file_path == "" or (file_path in self.index and self.index[file_path]["is_dir"]):
            # 获取当前路径下的所有条目
            sub_items = [
                (name, metadata["is_dir"])
                for name, metadata in self.index.items()
                if os.path.dirname(name) == file_path  # 只列出当前路径下的条目
            ]
            sub_items = sorted(sub_items, key=lambda x: x[0])
            start = (page - 1) * page_size
            end = start + page_size
            paginated_files = sub_items[start:end]

            files_html = "\n".join(
                f'<li><a href="/{os.path.join(file_path, name)}">{name}</a></li>' for name, _ in paginated_files
            )
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

        # 如果请求的是非根路径且存在，检查该路径是否存在
        if file_path not in self.index:
            raise HTTPException(status_code=404, detail="File or directory not found")

        metadata = self.index[file_path]
        if metadata["is_dir"]:
            # 列出子目录或文件
            sub_items = [
                (name, metadata["is_dir"])
                for name, metadata in self.index.items()
                if os.path.dirname(name) == file_path  # 只列出当前路径下的条目
            ]
            sub_items = sorted(sub_items, key=lambda x: x[0])
            start = (page - 1) * page_size
            end = start + page_size
            paginated_files = sub_items[start:end]

            files_html = "\n".join(
                f'<li><a href="/{os.path.join(file_path, name)}">{name}</a></li>' for name, _ in paginated_files
            )
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
        else:
            # 返回文件
            full_file_path = os.path.join(self.folder_path, file_path)

            # 检查文件扩展名，判断是否为图像或视频
            file_extension = os.path.splitext(full_file_path)[1].lower()
            image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg']
            video_extensions = ['.mp4', '.avi', '.mov', '.mkv']

            # 如果是图像或视频，默认下载文件
            if file_extension in image_extensions + video_extensions:
                if inline:
                    # 如果用户选择了内联显示，则返回带有 Content-Disposition: inline 的响应
                    return FileResponse(full_file_path, media_type=self.get_media_type(file_extension),
                                        filename=os.path.basename(full_file_path))
                else:
                    # 默认返回带有 Content-Disposition: attachment 的响应，强制下载
                    return FileResponse(full_file_path, media_type=self.get_media_type(file_extension),
                                        filename=os.path.basename(full_file_path),
                                        headers={"Content-Disposition": "attachment"})
            else:
                # 对于其他类型的文件，直接返回文件
                return FileResponse(full_file_path)


    def get_media_type(self, extension: str) -> str:
        """根据文件扩展名返回 MIME 类型"""
        media_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.svg': 'image/svg+xml',
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime',
            '.mkv': 'video/x-matroska'
        }
        return media_types.get(extension, 'application/octet-stream')

    def start(self):
        """启动服务"""
        app = FastAPI()

        @app.get("/{file_path:path}")
        async def serve(request: Request, file_path: str, page: int = Query(1, ge=1),
                        page_size: int = Query(20, ge=1, le=100), inline: Optional[bool] = None):
            """服务文件和目录"""
            return self.serve_path(file_path, page, page_size, inline)

        # 初始化并启动调度器
        scheduler = BackgroundScheduler()
        scheduler.add_job(self.cleanup_old_files, CronTrigger(hour=0, minute=0))  # 每天0点0分执行
        scheduler.add_job(self.update_index_task, CronTrigger(hour=0, minute=5))  # 每天0点5分更新索引
        scheduler.start()

        from uvicorn import run
        run(app, host="127.0.0.1", port=8000)

    def run_in_process(self):
        """在单独的进程中启动文件服务"""
        process = multiprocessing.Process(target=self.start)
        process.start()

# 示例用法
if __name__ == "__main__":
    file_service = FileService(folder_path=r"D:\kend\tests")
    file_service.run_in_process()





"""
代码功能说明：
    load_index：在服务启动时调用，加载之前保存的 file_index.json 索引文件。
    save_index：将当前的索引保存到 file_index.json 文件中。
    update_index：更新索引文件，支持全量更新（full_scan=True）和增量更新（full_scan=False）。增量更新时，检查文件的修改时间，并移除过期文件。
    cleanup_old_files：每天0点清理六个月前的文件，并从索引中移除这些文件。
    serve_path：提供文件夹和文件的访问，支持分页显示文件夹中的内容，如果是文件则直接返回该文件。
"""





