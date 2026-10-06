"""
轻量级资料索引层（不使用向量数据库）

启动时扫描 profile/ 目录，为每个资料文件产出一条索引记录：
    文件名 / 文件类型 / 文件路径 / 文件大小 / 修改时间 / 文件描述 / 所属类别

其中「所属类别」直接取自一级子目录名（约定优于配置），
「文件描述」首次由 DeepSeek 生成一句话概括，之后缓存到 profile/_meta.json，
文件内容没变（size + mtime 未变）就不会重复调用大模型。

Agent 的检索工具靠这份索引定位文件，不需要遍历全目录、也不需要读全文。
"""

import os
import json
from datetime import datetime

import config_data as config
from document.parsers import read_document

# 索引在进程内缓存一次，避免每次对话都重新扫描磁盘
_INDEX_CACHE: list[dict] | None = None

# 支持建立索引的文件后缀（与 document.parsers 保持一致）
SUPPORTED_SUFFIX = {".txt", ".md", ".csv", ".pdf", ".doc", ".docx", ".xls", ".xlsx"}

# 兜底类别名：文件直接放在 profile/ 根目录、或放在 config.profile_categories 未登记的目录里时使用
_FALLBACK_CATEGORY = "other"

# 生成描述时喂给大模型的正文长度
_DESCRIBE_EXCERPT_CHARS = 1200

_describer = None  # 生成文件描述用的模型实例（懒加载）


def _get_describer():
    """懒加载一个轻量对话模型实例，用于生成文件描述"""
    global _describer
    if _describer is None:
        from langchain_openai import ChatOpenAI
        _describer = ChatOpenAI(
            model=config.deepseek_model,
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
            temperature=0,
        )
    return _describer


def _profile_root() -> str:
    """资料根目录的绝对路径"""
    return os.path.abspath(config.profile_directory)


def _rel_key(abs_path: str) -> str:
    """文件相对于资料根目录的路径（统一用 / 分隔），用作 _meta.json 的键"""
    rel = os.path.relpath(abs_path, _profile_root())
    return rel.replace(os.sep, "/")


def _load_meta() -> dict:
    """读取描述缓存文件，不存在或损坏时返回空结构"""
    if not os.path.exists(config.profile_meta_path):
        return {"files": {}}
    try:
        with open(config.profile_meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("files"), dict):
            return {"files": {}}
        return data
    except Exception as e:
        print(f"[资料索引] 读取描述缓存失败，将重建：{e}")
        return {"files": {}}


def _save_meta(meta: dict) -> None:
    """把描述缓存写回磁盘"""
    os.makedirs(os.path.dirname(os.path.abspath(config.profile_meta_path)), exist_ok=True)
    with open(config.profile_meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _generate_description(file_name: str, category: str, content: str) -> str:
    """
    调用 DeepSeek 用一句话概括文件内容。
    失败时退化成「类别 + 文件名」，保证索引一定能建起来。
    """
    fallback = f"{category} 类别资料：{file_name}"
    excerpt = content[:_DESCRIBE_EXCERPT_CHARS]
    prompt = (
        "下面是一份个人资料文件的开头部分。请用一句不超过 30 个字的简体中文，"
        "概括这份文件包含哪些个人信息，只输出这一句话本身，不要加任何前缀、引号或换行。\n\n"
        f"文件名：{file_name}\n"
        f"资料类别：{category}\n"
        f"文件内容开头：\n{excerpt}"
    )
    try:
        resp = _get_describer().invoke(prompt)
        desc = (resp.content if hasattr(resp, "content") else str(resp)).strip().strip('"').strip()
        return desc if desc else fallback
    except Exception as e:
        print(f"[资料索引] 生成文件描述失败（{file_name}）：{e}")
        return fallback


def _scan_files() -> list[dict]:
    """扫描 profile/ 目录，产出每个文件的基础信息（不含描述）"""
    root = _profile_root()
    if not os.path.isdir(root):
        raise FileNotFoundError(f"个人资料目录不存在：{root}")

    entries = []
    for cur_dir, _, file_names in os.walk(root):
        for name in file_names:
            # 跳过缓存文件、Word/Excel 临时锁文件、隐藏文件
            if name.startswith((".", "_", "~$")):
                continue
            if os.path.splitext(name)[1].lower() not in SUPPORTED_SUFFIX:
                continue

            abs_path = os.path.join(cur_dir, name)
            size = os.path.getsize(abs_path)
            if size == 0:
                print(f"[资料索引] 跳过空文件（0 字节）：{_rel_key(abs_path)}")
                continue

            # 所属类别 = 相对路径的第一层目录名；直接放在根目录下、或放在未登记目录里的，
            # 归入兜底类别（不再是用户可见的类别，只是保证这类文件也能被索引到）
            rel = _rel_key(abs_path)
            category = rel.split("/")[0] if "/" in rel else _FALLBACK_CATEGORY
            if category not in config.profile_categories:
                category = _FALLBACK_CATEGORY

            entries.append({
                "file": name,
                "path": os.path.abspath(abs_path),
                "rel_path": rel,
                "type": os.path.splitext(name)[1].lstrip(".").lower(),
                "category": category,
                "size": size,
                "mtime": datetime.fromtimestamp(os.path.getmtime(abs_path)).strftime("%Y-%m-%d %H:%M:%S"),
            })
    # 按类别、文件名排序，保证索引顺序稳定
    entries.sort(key=lambda e: (e["category"], e["file"]))
    return entries


def build_index(force: bool = False) -> list[dict]:
    """
    构建（或重建）资料索引。
    :param force: True 时忽略进程内缓存，强制重新扫描磁盘
    :return:      索引记录列表
    """
    global _INDEX_CACHE
    if _INDEX_CACHE is not None and not force:
        return _INDEX_CACHE

    entries = _scan_files()
    meta = _load_meta()
    meta_changed = False

    for entry in entries:
        cached = meta["files"].get(entry["rel_path"])
        # 缓存命中条件：文件大小和修改时间都没变（内容没变就不用再花一次大模型调用）
        if cached and cached.get("size") == entry["size"] and cached.get("mtime") == entry["mtime"]:
            entry["description"] = cached.get("description") or f"{entry['category']} 类别资料"
            continue

        # 首次见到这个文件（或文件被改过）→ 读内容 + 生成描述
        try:
            content = read_document(entry["path"])
        except Exception as e:
            print(f"[资料索引] 读取失败，跳过描述生成（{entry['rel_path']}）：{e}")
            content = ""
        entry["description"] = _generate_description(entry["file"], entry["category"], content) if content \
            else f"{entry['category']} 类别资料：{entry['file']}"

        meta["files"][entry["rel_path"]] = {
            "description": entry["description"],
            "category": entry["category"],
            "size": entry["size"],
            "mtime": entry["mtime"],
        }
        meta_changed = True

    # 清理已被删除文件的缓存记录
    alive_keys = {e["rel_path"] for e in entries}
    for stale_key in [k for k in meta["files"] if k not in alive_keys]:
        del meta["files"][stale_key]
        meta_changed = True

    if meta_changed:
        _save_meta(meta)

    _INDEX_CACHE = entries
    return entries


def get_index() -> list[dict]:
    """获取索引（带进程内缓存）"""
    return build_index(force=False)


def get_categories() -> list[str]:
    """返回当前实际有资料的类别（去重、保持配置顺序）"""
    used = {e["category"] for e in get_index()}
    ordered = [c for c in config.profile_categories if c in used]
    ordered += sorted(used - set(ordered))
    return ordered


if __name__ == "__main__":
    idx = build_index(force=True)
    print(f"\n=== 资料索引构建完成，共 {len(idx)} 个文件 ===")
    for item in idx:
        print(f"\n文件名：{item['file']}")
        print(f"  类别：{item['category']}")
        print(f"  类型：{item['type']}")
        print(f"  大小：{item['size']} 字节")
        print(f"  修改时间：{item['mtime']}")
        print(f"  描述：{item['description']}")
        print(f"  路径：{item['path']}")
