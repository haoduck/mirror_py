# 稀疏文件目录镜像工具 (Sparse Mirror)

一款轻量级的远程目录镜像工具，通过 SSH 拉取远程服务器文件元数据，在本地生成**零磁盘占用**的完整目录镜像，保留文件路径、大小、修改时间等全部属性，支持增量更新、多目录批量同步，完全脱机可用。

## 功能特性

- ✅ **零磁盘占用镜像**：基于稀疏文件（Sparse File）特性，文件逻辑大小与源文件完全一致，实际磁盘占用几乎为 0
- ✅ **增量同步更新**：自动对比历史元数据，仅处理新增、修改、删除的文件，同步效率极高
- ✅ **多目录批量支持**：支持配置任意数量的「远程目录→本地目录」映射对，独立维护状态，失败互不影响
- ✅ **双配置模式**：同时支持 `.env` 外部配置文件和脚本内置默认配置，灵活适配不同场景
- ✅ **编码容错兼容**：自动适配 UTF-8/Shift-JIS/EUC-JP 等多语言编码，日文/特殊文件名不崩溃
- ✅ **多线程高性能**：线程池并发处理文件创建，数万文件规模下性能远超传统 Bash 脚本
- ✅ **完全脱机使用**：镜像生成后不依赖远程服务器，本地可直接浏览、排序、查看属性
- ✅ **失败隔离机制**：单个目录同步失败不会中断整体任务，最终统一输出执行结果

## 工作原理

1. **元数据拉取**：通过 SSH 在远程服务器执行 `find` 命令，导出所有文件的相对路径、字节大小、Unix 时间戳
2. **增量计算**：与本地保存的上一次元数据对比，精确计算出新增、修改、删除三类文件
3. **稀疏文件生成**：本地批量创建目录结构，通过系统调用生成对应大小的稀疏文件，同步修改时间和权限
4. **状态持久化**：保存本次元数据和同步时间，作为下次增量更新的基准

## 环境要求

- **本地环境**：Python 3.7+，Linux 系统（ext4/XFS/Btrfs 文件系统）
- **远程环境**：标准 Linux 系统，支持 SSH 登录和 GNU `find` 命令
- **依赖**：可选依赖 `python-dotenv`（用于支持 `.env` 配置文件）

## 快速开始

### 1. 获取脚本
将 `mirror.py` 保存到本地目录。

### 2. （可选）安装依赖
使用 `.env` 配置方式建议安装：
```bash
pip3 install python-dotenv
```
> 不安装也可正常运行，自动使用脚本内置配置。

### 3. 配置
两种方式二选一，推荐使用 `.env` 文件。

#### 方式 A：.env 配置文件（推荐）
在脚本同目录下创建 `.env` 文件：
```env
# 远程服务器信息
REMOTE_USER=root
REMOTE_HOST=192.168.1.100
SSH_PORT=22

# 性能配置
THREAD_NUM=8
SET_READONLY=True
REMOTE_ENCODING=auto

# 目录映射（序号从1开始，可无限追加）
REMOTE_PATH_1=/data/source_files
LOCAL_PATH_1=/data/mirror/source_files

REMOTE_PATH_2=/home/user/documents
LOCAL_PATH_2=/data/mirror/documents
```

#### 方式 B：内置默认配置
直接编辑脚本开头 `DEFAULT_CONFIG` 字典：
```python
DEFAULT_CONFIG = {
    "REMOTE_USER": "root",
    "REMOTE_HOST": "192.168.1.100",
    "SSH_PORT": 22,
    "THREAD_NUM": 8,
    "SET_READONLY": True,
    "REMOTE_ENCODING": "auto",
    "PATH_MAPPINGS": [
        ("/data/source_files", "/data/mirror/source_files"),
        ("/home/user/documents", "/data/mirror/documents"),
    ]
}
```

### 4. 前置准备
配置本地到远程服务器的 SSH 免密登录：
```bash
ssh-keygen
ssh-copy-id root@192.168.1.100
```

### 5. 运行
```bash
python3 mirror.py
```

## 配置说明

### 全局参数

| 参数名 | 说明 | 默认值 |
|--------|------|--------|
| `REMOTE_USER` | 远程服务器 SSH 用户名 | `your_username` |
| `REMOTE_HOST` | 远程服务器 IP 或域名 | `192.168.1.100` |
| `SSH_PORT` | SSH 端口号 | `22` |
| `THREAD_NUM` | 文件创建并发线程数，建议设为 CPU 核心数 1~2 倍 | `8` |
| `SET_READONLY` | 创建后设置文件为只读，防止误写入占用真实磁盘 | `True` |
| `REMOTE_ENCODING` | 远程文件名编码，`auto` 自动容错，可指定 `shift_jis`/`euc_jp` | `auto` |

### 目录映射配置
- 支持任意数量的目录映射对，序号从 `1` 开始连续编号
- `REMOTE_PATH_N`：远程服务器上的源目录绝对路径
- `LOCAL_PATH_N`：本地对应的镜像目录绝对路径
- 每个目录独立维护 `.mirror_state` 状态目录，互不干扰

## 使用示例

### 示例 1：单目录同步
```env
REMOTE_USER=root
REMOTE_HOST=10.0.0.1
REMOTE_PATH_1=/data/asmr
LOCAL_PATH_1=/root/mirror/asmr
```

### 示例 2：多目录批量同步
```env
REMOTE_USER=admin
REMOTE_HOST=172.16.0.5
THREAD_NUM=12

REMOTE_PATH_1=/data/video
LOCAL_PATH_1=/mnt/mirror/video

REMOTE_PATH_2=/data/audio
LOCAL_PATH_2=/mnt/mirror/audio

REMOTE_PATH_3=/data/documents
LOCAL_PATH_3=/mnt/mirror/docs
```

### 示例 3：定时自动同步
添加 crontab 任务，每天凌晨 2 点执行：
```bash
crontab -e
# 加入以下行
0 2 * * * /usr/bin/python3 /opt/sparse_mirror/mirror.py >> /var/log/sparse_mirror.log 2>&1
```

### 示例 4：日文环境远程服务器
```env
REMOTE_ENCODING=shift_jis
```

## 效果验证

```bash
# 查看文件逻辑大小（与远程源文件完全一致）
ls -lh /data/mirror/source_files

# 查看实际磁盘占用（几乎为 0，仅目录项占用少量空间）
du -sh /data/mirror/source_files

# 查看上次同步时间
cat /data/mirror/source_files/.mirror_state/last_sync_time
```

## 常见问题

### Q：为什么同步后显示 0 个文件？
A：请检查远程目录路径是否正确，确认目录内确实存在普通文件（非目录、非软链接）。可执行 `ssh user@host find /path -type f | wc -l` 验证远程文件数量。

### Q：出现 UnicodeDecodeError 编码错误怎么办？
A：远程服务器文件名编码非 UTF-8 导致，可在配置中指定 `REMOTE_ENCODING=shift_jis` 或 `REMOTE_ENCODING=euc_jp`。

### Q：稀疏文件复制后变大了？
A：复制到不支持稀疏文件的文件系统（如 FAT32）时会自动展开占用真实空间，仅在支持稀疏特性的文件系统（ext4/XFS/NTFS）有效。

### Q：如何提升同步速度？
A：适当调大 `THREAD_NUM` 并发数；如果不需要精确修改时间，可注释掉 `os.utime` 相关代码进一步提速。

## 注意事项

1. 仅同步普通文件，软链接、设备文件等特殊文件会被自动忽略
2. 镜像目录建议仅用于浏览索引，避免写入操作，否则会占用真实磁盘空间
3. 远程目录路径建议使用绝对路径，避免相对路径导致的位置错误
4. 首次全量同步后，后续增量同步仅传输元数据，流量极小
5. 每个镜像目录下的 `.mirror_state` 为状态目录，请勿删除，否则会丢失增量基准
