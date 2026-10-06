"""
命令行入库脚本：把文件 / 目录导入向量知识库（chroma_db/）

用途：给 Agent 的 search_knowledge 工具准备数据。
（网页端不再提供上传入口，入库统一走这个脚本。）

用法：
    python import_kb.py 产品手册.pdf
    python import_kb.py "D:\\资料\\公司文档"          # 目录递归导入
    python import_kb.py a.pdf b.docx 资料目录          # 混合多个路径
    python import_kb.py --list                        # 查看知识库现状
    python import_kb.py --reset --yes                 # 清空知识库（谨慎：删掉全部切片）

说明：
    - 首次导入会联网下载本地嵌入模型 BAAI/bge-small-zh-v1.5（几十 MB，
      缓存在 ~/.cache/fastembed），之后离线可用；下载失败可换网络或用代理重试
    - 内容相同的文件重复导入会被 MD5 去重自动跳过
    - 支持格式：txt / md / csv / pdf / doc / docx / xls / xlsx
    - 切片参数在 config_data.py：chunk_size=500、chunk_overlap=100
"""

import argparse
import os
import sys

import config_data as config
from document.parsers import extract_text_from_file
from index.profile_index import SUPPORTED_SUFFIX
from knowledge_base import KnowledgeBaseService
from vector_stores import get_document_count, get_vector_store, list_sources

# 遍历目录时跳过的目录名
_SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", ".venv", "venv", "node_modules", "chroma_db"}


def _skip_file(name: str) -> bool:
    """跳过隐藏文件、Office 临时锁文件、md5 记账文件"""
    return name.startswith((".", "~$")) or name == "md5.txt"


def collect_files(paths: list[str]) -> tuple[list[str], list[str]]:
    """把输入路径展开成待导入的文件列表；返回 (文件列表, 不存在的路径)"""
    files: list[str] = []
    missing: list[str] = []

    for p in paths:
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for cur_dir, dir_names, file_names in os.walk(p):
                dir_names[:] = [d for d in dir_names if d not in _SKIP_DIRS and not d.startswith(".")]
                for name in file_names:
                    if _skip_file(name):
                        continue
                    if os.path.splitext(name)[1].lower() not in SUPPORTED_SUFFIX:
                        continue
                    files.append(os.path.join(cur_dir, name))
        else:
            missing.append(p)

    return files, missing


def show_status() -> None:
    """打印知识库现状"""
    count = get_document_count()
    print(f"向量知识库路径：{os.path.abspath(config.persist_directory)}")
    print(f"切片总数：{count}")
    if count:
        print("已导入的文件：")
        for name in list_sources():
            print(f"  - {name}")
    else:
        print("（空库）可以用 python import_kb.py <文件或目录> 导入资料")


def reset_kb() -> None:
    """清空知识库：删除向量集合 + 清空 MD5 去重记录"""
    if not os.path.isdir(config.persist_directory):
        print("知识库目录不存在，无需清空。")
        return

    before = get_document_count()
    get_vector_store().delete_collection()

    if os.path.exists(config.md5_path):
        open(config.md5_path, "w", encoding="utf-8").close()

    print(f"已清空向量知识库（原有 {before} 个切片），MD5 去重记录同时重置。")
    print("注意：还需要重启正在运行的 Streamlit 应用，索引缓存才会刷新。")


def import_files(paths: list[str]) -> int:
    """把文件导入知识库，返回失败数量"""
    files, missing = collect_files(paths)

    for p in missing:
        print(f"[跳过] 路径不存在：{p}")
    if not files:
        print("没有找到可导入的文件（支持 txt / md / csv / pdf / doc / docx / xls / xlsx）。")
        return 0

    print(f"共找到 {len(files)} 个文件，开始导入……")
    print("（首次运行需要下载本地嵌入模型，可能要等 1-3 分钟，请勿中断）")

    kb = KnowledgeBaseService()  # 这一步会加载/下载嵌入模型

    ok, duplicated, failed = 0, 0, []
    for idx, path in enumerate(files, 1):
        name = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            failed.append((path, f"读取失败：{e}"))
            print(f"[{idx}/{len(files)}] ✗ {name} —— 读取失败：{e}")
            continue

        # 先解析一次，便于把「解析失败」和「重复」区分开，提示更准确
        if not (extract_text_from_file(data, name) or "").strip():
            failed.append((path, "未能提取到可读文本（扫描版/加密/损坏/空文件）"))
            print(f"[{idx}/{len(files)}] ✗ {name} —— 解析不出文本")
            continue

        result = kb.upload_by_file(data, name)
        if "已存在" in result:
            duplicated += 1
            print(f"[{idx}/{len(files)}] = {name} —— 内容重复，已跳过")
        elif "失败" in result:
            failed.append((path, result))
            print(f"[{idx}/{len(files)}] ✗ {name} —— {result}")
        else:
            ok += 1
            print(f"[{idx}/{len(files)}] ✓ {name}")

    print("\n" + "=" * 56)
    print(f"导入完成：成功 {ok} 个，重复跳过 {duplicated} 个，失败 {len(failed)} 个")
    if failed:
        print("失败清单：")
        for path, reason in failed:
            print(f"  - {path}：{reason}")
    print(f"当前知识库切片总数：{get_document_count()}")
    print("提示：如果 Streamlit 正在运行，需要重启它，Agent 才能拿到最新的知识库状态。")
    return len(failed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把文件/目录导入向量知识库（给 Agent 的 search_knowledge 工具用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="*", help="要导入的文件或目录（可多个）")
    parser.add_argument("--list", action="store_true", help="只查看知识库现状，不导入")
    parser.add_argument("--reset", action="store_true", help="清空知识库（必须同时加 --yes）")
    parser.add_argument("--yes", action="store_true", help="确认执行 --reset")
    args = parser.parse_args()

    if args.reset:
        if not args.yes:
            print("--reset 会删除全部切片，请确认后加 --yes 再执行：python import_kb.py --reset --yes")
            return 1
        reset_kb()
        return 0

    if args.list:
        show_status()
        return 0

    if not args.paths:
        parser.print_help()
        show_status()
        return 0

    return 1 if import_files(args.paths) else 0


if __name__ == "__main__":
    sys.exit(main())
