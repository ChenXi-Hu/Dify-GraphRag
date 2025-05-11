from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn

from utils import process_context_data

from pathlib import Path
import graphrag.api as api
from graphrag.config.load_config import load_config
import pandas as pd
from config import PROJECT_DIRECTORY, DESTINATION_DIRECTORY, COMMUNITY_LEVEL, CLAIM_EXTRACTION_ENABLED, RESPONSE_TYPE

import os
import shutil
import time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.config = load_config(Path(PROJECT_DIRECTORY))
    app.state.entities = pd.read_parquet(f"{PROJECT_DIRECTORY}/output/entities.parquet")
    app.state.communities = pd.read_parquet(f"{PROJECT_DIRECTORY}/output/communities.parquet")
    app.state.community_reports = pd.read_parquet(f"{PROJECT_DIRECTORY}/output/community_reports.parquet")
    app.state.text_units = pd.read_parquet(f"{PROJECT_DIRECTORY}/output/text_units.parquet")
    app.state.relationships = pd.read_parquet(f"{PROJECT_DIRECTORY}/output/relationships.parquet")
    app.state.covariates = pd.read_parquet(f"{PROJECT_DIRECTORY}/output/covariates.parquet") if CLAIM_EXTRACTION_ENABLED else None
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://noworneverev.github.io"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/search/global")
async def global_search(query: str = Query(..., description="Global Search")):
    try:
        response, context = await api.global_search(
                                config=app.state.config,
                                entities=app.state.entities,
                                communities=app.state.communities,
                                community_reports=app.state.community_reports,                                
                                community_level=COMMUNITY_LEVEL,
                                dynamic_community_selection=False,
                                response_type=RESPONSE_TYPE,
                                query=query,
                            )
        response_dict = {
            "response": response,
            "context_data": process_context_data(context),
        }
        return JSONResponse(content=response_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search/local")
async def local_search(query: str = Query(..., description="Local Search")):
    try:
        response, context = await api.local_search(
                                config=app.state.config,
                                entities=app.state.entities,
                                communities=app.state.communities,
                                community_reports=app.state.community_reports,
                                text_units=app.state.text_units,
                                relationships=app.state.relationships,
                                covariates=app.state.covariates,
                                community_level=COMMUNITY_LEVEL,                                
                                response_type=RESPONSE_TYPE,
                                query=query,
                            )
        response_dict = {
            "response": response,
            "context_data": process_context_data(context),
        }        
        return JSONResponse(content=response_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search/drift")
async def drift_search(query: str = Query(..., description="DRIFT Search")):
    try:
        response, context = await api.drift_search(
                                config=app.state.config,
                                entities=app.state.entities,
                                communities=app.state.communities,
                                community_reports=app.state.community_reports,
                                text_units=app.state.text_units,
                                relationships=app.state.relationships,
                                community_level=COMMUNITY_LEVEL,                                
                                response_type=RESPONSE_TYPE,
                                query=query,
                            )
        response_dict = {
            "response": response,
            "context_data": process_context_data(context),
        }
        return JSONResponse(content=response_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/search/basic")
async def basic_search(query: str = Query(..., description="Basic Search")):
    try:
        response, context = await api.basic_search(
                                config=app.state.config,
                                text_units=app.state.text_units,                                
                                query=query,
                            )
        response_dict = {
            "response": response,
            "context_data": process_context_data(context),
        }
        return JSONResponse(content=response_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def status():
    return JSONResponse(content={"status": "Server is up and running"})


class FileChangeHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory:  # 仅处理文件，忽略文件夹
            source_file = event.src_path
            target_file = source_file.replace(SOURCE_PATH, TARGET_PATH)
            # 复制更新的文件（覆盖目标文件）
            shutil.copy2(source_file, target_file)
            print(f"更新文件：{source_file} → {target_file}")
    
    def on_created(self, event):
        if not event.is_directory:
            self.on_modified(event)  # 新增文件时视为需要复制
    
    # 如需处理删除操作：
    def on_deleted(self, event):
        if not event.is_directory:
            target_file = event.src_path.replace(SOURCE_PATH, TARGET_PATH)
            if os.path.exists(target_file):
                os.remove(target_file)
                print(f"删除文件：{target_file}")

class SyncHandler(FileSystemEventHandler):
    def __init__(self, source_dir, dest_dir):
        self.source_dir = source_dir
        self.dest_dir = dest_dir

    def on_created(self, event):
        """处理创建事件"""
        if event.is_directory:
            self._copy_dir(event.src_path)
        else:
            self._copy_file(event.src_path)

    def on_modified(self, event):
        """处理修改事件"""
        if not event.is_directory:
            self._copy_file(event.src_path)

    def on_deleted(self, event):
        """处理删除事件"""
        rel_path = os.path.relpath(event.src_path, self.source_dir)
        dest_path = os.path.join(self.dest_dir, rel_path)
        if os.path.exists(dest_path):
            if os.path.isdir(dest_path):
                shutil.rmtree(dest_path)
            else:
                os.remove(dest_path)
        print(f"Deleted {dest_path}")

    def on_moved(self, event):
        """处理移动/重命名事件"""
        src_rel_path = os.path.relpath(event.src_path, self.source_dir)
        dest_src_path = os.path.join(self.dest_dir, src_rel_path)
        
        dest_rel_path = os.path.relpath(event.dest_path, self.source_dir)
        dest_new_path = os.path.join(self.dest_dir, dest_rel_path)
        
        if os.path.exists(dest_src_path):
            os.renames(dest_src_path, dest_new_path)
        print(f"Moved {dest_src_path} to {dest_new_path}")

    def _copy_file(self, src_path):
        """复制文件到目标目录"""
        rel_path = os.path.relpath(src_path, self.source_dir)
        dest_path = os.path.join(self.dest_dir, rel_path)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy2(src_path, dest_path)
        print(f"Copied {src_path} to {dest_path}")

    def _copy_dir(self, src_path):
        """创建对应目录"""
        rel_path = os.path.relpath(src_path, self.source_dir)
        dest_path = os.path.join(self.dest_dir, rel_path)
        os.makedirs(dest_path, exist_ok=True)
        print(f"Created directory {dest_path}")

def initial_sync(source, dest):
    """初始完全同步"""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    print("Initial synchronization completed.")


if __name__ == "__main__":
    # 配置同步路径
    SOURCE_DIR = f"{PROJECT_DIRECTORY}/output"  # 只监控output目录
    DEST_DIR = DESTINATION_DIRECTORY

    # 执行初始同步
    if os.path.exists(SOURCE_DIR):
        initial_sync(SOURCE_DIR, DEST_DIR)
    else:
        os.makedirs(DEST_DIR, exist_ok=True)

    # 启动监控线程
    def start_observer():
        event_handler = SyncHandler(SOURCE_DIR, DEST_DIR)
        observer = Observer()
        observer.schedule(event_handler, SOURCE_DIR, recursive=True)
        observer.start()
        print(f"Starting monitoring on {SOURCE_DIR}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
    
    # 在独立线程中启动监控
    sync_thread = threading.Thread(target=start_observer, daemon=True)
    sync_thread.start()

    # 启动FastAPI服务器
    uvicorn.run(app, host="0.0.0.0", port=8000)