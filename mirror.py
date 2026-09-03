#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 多目录稀疏镜像同步工具
# 配置方式：优先读取同目录下 .env 文件，无则使用脚本内默认配置

import os
import sys
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# ====================== 内置默认配置（无.env文件时生效） ======================
DEFAULT_CONFIG = {
    # 远程服务器通用配置
    "REMOTE_USER": "your_username",
    "REMOTE_HOST": "192.168.1.100",
    "SSH_PORT": 22,
    
    # 性能配置
    "THREAD_NUM": 8,
    "SET_READONLY": True,
    
    # 编码配置：auto自动容错，也可指定 shift_jis / euc_jp
    "REMOTE_ENCODING": "auto",
    
    # 目录映射（多组，一一对应）
    # 格式：[(远程目录1, 本地镜像1), (远程目录2, 本地镜像2)]
    "PATH_MAPPINGS": [
        # ("/data/source_1", "/data/mirror/source_1"),
        # ("/data/source_2", "/data/mirror/source_2"),
    ]
}
# ==========================================================================

# 尝试加载 dotenv（可选依赖，未安装不影响使用）
try:
    from dotenv import load_dotenv
    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False


def load_config():
    """加载配置：优先.env，兜底用内置默认"""
    config = DEFAULT_CONFIG.copy()
    
    # 加载 .env 文件
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if HAS_DOTENV and os.path.exists(env_path):
        load_dotenv(env_path)
        
        # 覆盖全局配置
        config["REMOTE_USER"] = os.getenv("REMOTE_USER", config["REMOTE_USER"])
        config["REMOTE_HOST"] = os.getenv("REMOTE_HOST", config["REMOTE_HOST"])
        config["SSH_PORT"] = int(os.getenv("SSH_PORT", config["SSH_PORT"]))
        config["THREAD_NUM"] = int(os.getenv("THREAD_NUM", config["THREAD_NUM"]))
        config["SET_READONLY"] = os.getenv("SET_READONLY", "True").lower() in ("true", "1", "yes")
        config["REMOTE_ENCODING"] = os.getenv("REMOTE_ENCODING", config["REMOTE_ENCODING"])
        
        # 解析多组目录映射（自动扫描 REMOTE_PATH_N / LOCAL_PATH_N）
        mappings = []
        index = 1
        while True:
            remote_key = f"REMOTE_PATH_{index}"
            local_key = f"LOCAL_PATH_{index}"
            remote_path = os.getenv(remote_key)
            local_path = os.getenv(local_key)
            
            if remote_path and local_path:
                mappings.append((remote_path.strip(), local_path.strip()))
                index += 1
            else:
                break
        
        if mappings:
            config["PATH_MAPPINGS"] = mappings
    
    # 校验配置
    if not config["PATH_MAPPINGS"]:
        print("错误：未配置任何目录映射，请在.env或脚本内PATH_MAPPINGS中添加")
        sys.exit(1)
    
    return config


def decode_bytes(raw_bytes, encoding="auto"):
    """容错解码字节流"""
    if encoding.lower() != "auto":
        try:
            return raw_bytes.decode(encoding)
        except Exception:
            pass
    
    for enc in ["utf-8", "shift_jis", "euc_jp"]:
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    
    return raw_bytes.decode("utf-8", errors="replace")


class SparseMirrorTask:
    """单个目录同步任务"""
    def __init__(self, remote_path, local_path, global_config):
        self.remote_path = remote_path.rstrip("/")
        self.local_path = local_path.rstrip("/")
        self.state_dir = os.path.join(self.local_path, ".mirror_state")
        
        # 继承全局配置
        self.remote_user = global_config["REMOTE_USER"]
        self.remote_host = global_config["REMOTE_HOST"]
        self.ssh_port = global_config["SSH_PORT"]
        self.thread_num = global_config["THREAD_NUM"]
        self.set_readonly = global_config["SET_READONLY"]
        self.remote_encoding = global_config["REMOTE_ENCODING"]
        
        self.task_name = os.path.basename(self.remote_path)
    
    def log(self, msg):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] [{self.task_name}] {msg}"
        print(line)
        os.makedirs(self.state_dir, exist_ok=True)
        with open(os.path.join(self.state_dir, "sync.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    
    def fetch_remote_meta(self):
        """拉取远程文件元数据"""
        self.log("拉取远程文件元数据...")
        cmd = [
            "ssh", "-p", str(self.ssh_port),
            f"{self.remote_user}@{self.remote_host}",
            f"cd '{self.remote_path}' && find . -type f -printf '%P\\t%s\\t%T@\\n'"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            self.log(f"远程拉取失败：{decode_bytes(e.stderr, self.remote_encoding).strip()}")
            return None
        
        output = decode_bytes(result.stdout, self.remote_encoding)
        meta = {}
        
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            path, size_str, mtime_str = parts
            try:
                meta[path] = (int(size_str), float(mtime_str))
            except ValueError:
                continue
        
        self.log(f"元数据拉取完成，共 {len(meta)} 个文件")
        return meta
    
    def load_local_meta(self):
        """加载本地历史元数据"""
        meta_file = os.path.join(self.state_dir, "last_meta.txt")
        if not os.path.exists(meta_file):
            return {}
        
        meta = {}
        with open(meta_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                path, size, mtime = parts
                try:
                    meta[path] = (int(size), float(mtime))
                except ValueError:
                    continue
        return meta
    
    def calc_diff(self, old_meta, new_meta):
        """计算增量差异"""
        add, modify, delete = {}, {}, []
        
        for path, info in new_meta.items():
            if path not in old_meta:
                add[path] = info
            elif old_meta[path] != info:
                modify[path] = info
        
        for path in old_meta:
            if path not in new_meta:
                delete.append(path)
        
        self.log(f"增量计算：新增 {len(add)}，修改 {len(modify)}，删除 {len(delete)}")
        return add, modify, delete
    
    def create_single_file(self, path, size, mtime):
        """创建单个稀疏文件"""
        target = os.path.join(self.local_path, path)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.truncate(size)
            os.utime(target, (mtime, mtime))
            if self.set_readonly:
                os.chmod(target, 0o444)
            return True
        except Exception as e:
            self.log(f"创建失败 {path}: {str(e)}")
            return False
    
    def process_files(self, add_dict, modify_dict):
        """多线程处理新增/修改文件"""
        work = {**add_dict, **modify_dict}
        total = len(work)
        if total == 0:
            self.log("无新增/修改文件，跳过处理")
            return
        
        self.log(f"开始处理 {total} 个文件，线程数 {self.thread_num}")
        success = 0
        
        with ThreadPoolExecutor(max_workers=self.thread_num) as executor:
            futures = {
                executor.submit(self.create_single_file, path, size, mtime): path
                for path, (size, mtime) in work.items()
            }
            
            for i, future in enumerate(as_completed(futures), 1):
                if future.result():
                    success += 1
                if i % 1000 == 0:
                    self.log(f"已处理 {i}/{total}")
        
        self.log(f"处理完成：成功 {success}，失败 {total - success}")
    
    def process_delete(self, delete_list):
        """处理删除文件并清理空目录"""
        if not delete_list:
            self.log("无删除文件，跳过处理")
            return
        
        self.log(f"删除 {len(delete_list)} 个文件")
        for path in delete_list:
            target = os.path.join(self.local_path, path)
            if os.path.isfile(target):
                os.remove(target)
        
        # 自底向上清理空目录
        for root, dirs, files in os.walk(self.local_path, topdown=False):
            if root == self.state_dir or root.startswith(self.state_dir + os.sep):
                continue
            if not os.listdir(root):
                try:
                    os.rmdir(root)
                except OSError:
                    pass
        
        self.log("删除完成，已清理空目录")
    
    def save_meta(self, meta):
        """保存元数据到本地"""
        os.makedirs(self.state_dir, exist_ok=True)
        meta_file = os.path.join(self.state_dir, "last_meta.txt")
        
        with open(meta_file, "w", encoding="utf-8", errors="replace") as f:
            for path, (size, mtime) in sorted(meta.items()):
                f.write(f"{path}\t{size}\t{mtime}\n")
        
        time_file = os.path.join(self.state_dir, "last_sync_time")
        with open(time_file, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    
    def run(self):
        """执行完整同步流程"""
        self.log("===== 任务开始 =====")
        try:
            os.makedirs(self.local_path, exist_ok=True)
            
            new_meta = self.fetch_remote_meta()
            if new_meta is None:
                return False
            
            old_meta = self.load_local_meta()
            add, modify, delete = self.calc_diff(old_meta, new_meta)
            
            self.process_files(add, modify)
            self.process_delete(delete)
            self.save_meta(new_meta)
            
            self.log("===== 任务完成 =====")
            return True
        except Exception as e:
            self.log(f"任务异常：{str(e)}")
            return False


def main():
    config = load_config()
    
    print("=" * 50)
    print(f"共配置 {len(config['PATH_MAPPINGS'])} 个同步任务")
    print(f"远程服务器：{config['REMOTE_USER']}@{config['REMOTE_HOST']}:{config['SSH_PORT']}")
    print(f"并发线程数：{config['THREAD_NUM']}")
    print("=" * 50)
    print()
    
    success = 0
    failed = 0
    
    for idx, (remote_path, local_path) in enumerate(config["PATH_MAPPINGS"], 1):
        print(f"[{idx}/{len(config['PATH_MAPPINGS'])}] 处理：{remote_path} → {local_path}")
        task = SparseMirrorTask(remote_path, local_path, config)
        if task.run():
            success += 1
        else:
            failed += 1
        print()
    
    print("=" * 50)
    print(f"全部任务执行完毕：成功 {success}，失败 {failed}，总计 {len(config['PATH_MAPPINGS'])}")
    print("=" * 50)


if __name__ == "__main__":
    main()
