#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostgreSQL 统一导入脚本（pgvector 版本）

将实体、关系和向量数据统一导入到 PostgreSQL + pgvector 数据库。

支持命令：
  - tables create  : 创建表结构
  - tables drop    : 删除所有表
  - import         : 导入数据（实体CSV、关系CSV、向量CSV、语料JSON）
  - delete         : 按书名删除数据
"""

import argparse
import asyncio
import csv
import json
import os
import re
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

# 优先加载 docker/.env（含 PostgreSQL 和 Embedding 配置）
load_dotenv(Path(__file__).resolve().parent.parent / "docker" / ".env")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

EMBEDDING_BATCH_SIZE = 50
DEFAULT_DATA_DIR = Path("data/input_data")

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _resolve_uri(args_uri: str | None) -> str:
    """从参数或环境变量获取 PostgreSQL 连接 URI。"""
    uri = args_uri or os.getenv("DATABASE_URL") or os.getenv("PG_URI")
    if not uri:
        # 尝试从 POSTGRES_* 环境变量拼接 URI
        host = os.getenv("POSTGRES_HOST", "")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "")
        user = os.getenv("POSTGRES_USER", "")
        password = os.getenv("POSTGRES_PASSWORD", "")
        if host and db and user:
            uri = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    if not uri:
        print(
            "错误: 未指定数据库连接 URI。\n"
            "请使用 --uri 参数，或在 docker/.env 中设置 POSTGRES_HOST/PORT/DB/USER/PASSWORD。"
        )
        sys.exit(1)
    return uri


def _get_book_dir(book: str) -> Path:
    """获取书籍数据目录路径。"""
    return DEFAULT_DATA_DIR / book


def _get_data_path(book_dir: Path, book: str, suffix: str) -> Path:
    """获取数据文件路径，支持多种命名风格。"""
    candidates = [
        book_dir / f"{book}{suffix}",
        book_dir / f"{book}_{suffix}",
    ]
    for p in candidates:
        if p.exists():
            return p
    # 兜底：返回默认路径（后续读取时会报错）
    return candidates[0]


def _read_csv(path: Path) -> list[dict]:
    """读取 CSV 文件，返回字典列表。"""
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _parse_alias(alias_str: str) -> str:
    """解析 alias 字段为 JSON 字符串。

    CSV 中 alias 可能是以下格式之一：
    - JSON 数组: '["齐天大圣","行者"]'
    - Python 列表字面量: "['齐天大圣', '行者']"
    - 逗号分隔字符串: "齐天大圣,行者"
    """
    if not alias_str or alias_str.strip() == "":
        return "[]"
    stripped = alias_str.strip()
    # 空数组字面量
    if stripped == "[]":
        return "[]"
    try:
        parsed = json.loads(stripped)
        return json.dumps(parsed, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        import ast

        parsed = ast.literal_eval(stripped)
        return json.dumps(parsed, ensure_ascii=False)
    except (ValueError, SyntaxError):
        pass
    # 兜底：当作逗号分隔字符串处理
    items = [item.strip() for item in stripped.split(",") if item.strip()]
    return json.dumps(items, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 数据库操作
# ---------------------------------------------------------------------------


async def create_tables(pool: asyncpg.Pool, embedding_dim: int = 2560) -> None:
    """创建所有表结构、扩展和索引。"""

    # 扩展
    await pool.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    print("[OK] pgvector 扩展已启用")
    await pool.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    print("[OK] pg_trgm 扩展已启用")

    # entities 表
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            book TEXT NOT NULL,
            description TEXT,
            alias JSONB DEFAULT '[]'::jsonb,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_entities_book_type ON entities(book, type);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_entities_name_trgm ON entities USING gin (name gin_trgm_ops);")
    print("[OK] entities 表及索引已创建")

    # relationships 表
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS relationships (
            id SERIAL PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES entities(id),
            target_id TEXT NOT NULL REFERENCES entities(id),
            rel_type TEXT NOT NULL,
            category TEXT,
            description TEXT,
            book TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    await pool.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_unique
        ON relationships(source_id, target_id, rel_type, book);
    """)
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_rel_source_book ON relationships(source_id, book);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_rel_target_book ON relationships(target_id, book);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_rel_source_type_book ON relationships(source_id, rel_type, book);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_rel_target_type_book ON relationships(target_id, rel_type, book);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_rel_book ON relationships(book);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_rel_category_book ON relationships(category, book);")
    print("[OK] relationships 表及索引已创建")

    # entity_embeddings 表（超过 2000 维时使用 halfvec 以支持 HNSW 索引）
    col_type = "halfvec" if embedding_dim > 2000 else "vector"
    await pool.execute(f"""
        CREATE TABLE IF NOT EXISTS entity_embeddings (
            id TEXT PRIMARY KEY REFERENCES entities(id),
            embedding {col_type}({embedding_dim}),
            book TEXT NOT NULL,
            type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    ops = "halfvec_cosine_ops" if embedding_dim > 2000 else "vector_cosine_ops"
    await pool.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_embedding_vector
        ON entity_embeddings USING hnsw (embedding {ops})
        WITH (m = 16, ef_construction = 64);
    """)
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_embedding_book ON entity_embeddings(book);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_embedding_type ON entity_embeddings(type);")
    print("[OK] entity_embeddings 表及索引已创建")

    # documents 表（章节）
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            book TEXT NOT NULL,
            chapter_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(book, chapter_number)
        );
    """)
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_documents_book_chapter ON documents(book, chapter_number);")
    print("[OK] documents 表及索引已创建")

    # sentences 表（句子）
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS sentences (
            id SERIAL PRIMARY KEY,
            book TEXT NOT NULL,
            document_id INTEGER NOT NULL REFERENCES documents(id),
            chapter_number INTEGER NOT NULL,
            sentence_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(book, chapter_number, sentence_index)
        );
    """)
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_sentences_book_chapter_idx ON sentences(book, chapter_number, sentence_index);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_sentences_book_doc ON sentences(book, document_id);")
    await pool.execute("CREATE INDEX IF NOT EXISTS idx_sentences_text_trgm ON sentences USING gin (text gin_trgm_ops);")
    print("[OK] sentences 表及索引已创建")


async def drop_tables(pool: asyncpg.Pool) -> None:
    """按 FK 依赖顺序删除所有表。"""
    tables = ["sentences", "entity_embeddings", "relationships", "documents", "entities"]
    async with pool.acquire() as conn:
        for table in tables:
            await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            print(f"[OK] 已删除 {table} 表")
    print("[OK] 所有表已删除")


async def import_entities(pool: asyncpg.Pool, csv_path: Path) -> int:
    """从 CSV 批量导入实体，ON CONFLICT DO UPDATE。"""
    rows = _read_csv(csv_path)
    if not rows:
        print(f"[跳过] 实体文件为空: {csv_path}")
        return 0

    print(f"  读取到 {len(rows)} 条实体记录")

    data = [
        (
            row["id"],
            row["name"],
            row["type"],
            row["book"],
            row.get("description", ""),
            _parse_alias(row.get("alias", "[]")),
        )
        for row in rows
    ]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                """
                INSERT INTO entities (id, name, type, book, description, alias)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    description = EXCLUDED.description,
                    alias = EXCLUDED.alias
                """,
                data,
            )

    print(f"[OK] 已导入 {len(data)} 条实体")
    return len(data)


async def import_relationships(pool: asyncpg.Pool, csv_path: Path) -> int:
    """从 CSV 批量导入关系。"""
    rows = _read_csv(csv_path)
    if not rows:
        print(f"[跳过] 关系文件为空: {csv_path}")
        return 0

    print(f"  读取到 {len(rows)} 条关系记录")

    data = [
        (
            row["head_id"],
            row["tail_id"],
            row["type"],
            row.get("source_relationship_type", ""),
            row.get("description", ""),
            row["book"],
        )
        for row in rows
    ]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                """
                INSERT INTO relationships (source_id, target_id, rel_type, category, description, book)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (source_id, target_id, rel_type, book) DO UPDATE SET
                    description = EXCLUDED.description
                """,
                data,
            )

    print(f"[OK] 已导入 {len(data)} 条关系")
    return len(data)


async def import_embeddings(
    pool: asyncpg.Pool,
    csv_path: Path,
    config: dict,
) -> int:
    """从 CSV 读取数据，按批次调用 embedding API 生成向量后写入。"""
    rows = _read_csv(csv_path)
    if not rows:
        print(f"[跳过] 向量文件为空: {csv_path}")
        return 0

    total = len(rows)
    batch_size = config.get("batch_size", EMBEDDING_BATCH_SIZE)
    api_url = config["api_url"]
    api_key = config["api_key"]
    model = config.get("model", "Qwen/Qwen3-Embedding-4B")
    embedding_dim = config.get("embedding_dim", 2560)
    col_type = "halfvec" if embedding_dim > 2000 else "vector"
    instruct = config.get(
        "instruct",
        "给定一个实体搜索查询，检索能够回答该查询的相关实体描述",
    )

    print(f"  读取到 {total} 条向量记录")
    print(f"  嵌入模型: {model}")
    print(f"  批处理大小: {batch_size}")

    # 延迟导入 httpx
    import httpx

    inserted = 0
    for i in range(0, total, batch_size):
        batch = rows[i : i + batch_size]
        batch_end = min(i + batch_size, total)
        print(f"  生成嵌入 [{i + 1}-{batch_end}/{total}]...")

        # 构造输入文本
        texts = []
        for row in batch:
            desc = row.get("description", "")
            entity_name = row.get("id", "").split("_")[-1] if "_" in row.get("id", "") else ""
            texts.append(f"{entity_name}是什么\n{desc}" if entity_name else desc)

        # 调用 Embedding API
        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{api_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "input": [f"Instruct: {instruct}\nQuery: {t}" for t in texts],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            # 按 index 排序确保顺序正确
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            embeddings = [item["embedding"] for item in sorted_data]

        # 批量写入数据库
        emb_data = [
            (
                row["id"],
                "[" + ",".join(str(v) for v in emb) + "]",
                row.get("book", ""),
                row.get("type", ""),
            )
            for row, emb in zip(batch, embeddings)
        ]
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    f"""
                    INSERT INTO entity_embeddings (id, embedding, book, type)
                    VALUES ($1, $2::{col_type}, $3, $4)
                    ON CONFLICT (id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        book = EXCLUDED.book,
                        type = EXCLUDED.type
                    """,
                    emb_data,
                )
        inserted += len(emb_data)

    print(f"[OK] 已导入 {inserted} 条向量")
    return inserted


async def import_corpus(pool: asyncpg.Pool, json_path: Path, book: str) -> int:
    """从 JSON 文件导入语料数据（documents + sentences）。

    documents 使用 ON CONFLICT (book, chapter_number) 实现幂等更新。
    sentences 先删除该书已有数据再批量插入。
    """
    if not json_path.exists():
        raise FileNotFoundError(f"文件不存在: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        chapters = json.load(f)

    if not chapters:
        print(f"[跳过] Corpus 文件为空: {json_path}")
        return 0

    print(f"  读取到 {len(chapters)} 个章节")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. 批量 upsert documents
            doc_data = [
                (book, i + 1, chapter.get("title", ""), chapter.get("text", ""))
                for i, chapter in enumerate(chapters)
            ]
            await conn.executemany(
                """
                INSERT INTO documents (book, chapter_number, title, text)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (book, chapter_number) DO UPDATE SET
                    title = EXCLUDED.title, text = EXCLUDED.text
                """,
                doc_data,
            )

            # 2. 获取 document_id 映射
            doc_rows = await conn.fetch(
                "SELECT id, chapter_number FROM documents WHERE book = $1 ORDER BY chapter_number",
                book,
            )
            doc_id_map = {r["chapter_number"]: r["id"] for r in doc_rows}

            # 3. 构建 sentences 数据
            sent_data: list[tuple] = []
            for i, chapter in enumerate(chapters):
                chapter_number = i + 1
                doc_id = doc_id_map[chapter_number]
                text = chapter.get("text", "")
                # 拆分句子：按中文标点分割
                parts = re.split(r"(?<=[。！？\n])", text)
                idx = 0
                for part in parts:
                    sentence = part.strip()
                    if not sentence:
                        continue
                    sent_data.append((book, doc_id, chapter_number, idx, sentence))
                    idx += 1

            # 4. 删除旧 sentences 再批量插入
            if sent_data:
                await conn.execute("DELETE FROM sentences WHERE book = $1", book)
                await conn.executemany(
                    """
                    INSERT INTO sentences (book, document_id, chapter_number, sentence_index, text)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    sent_data,
                )

    print(f"[OK] 已导入 {len(doc_data)} 个章节, {len(sent_data)} 条句子")
    return len(doc_data)


async def delete_data(pool: asyncpg.Pool, book: str) -> None:
    """按书名删除所有相关数据。"""
    print(f"开始删除书籍 '{book}' 的数据...")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 按 FK 依赖顺序删除
            result = await conn.execute("DELETE FROM sentences WHERE book = $1", book)
            print(f"  删除 sentences: {result}")
            result = await conn.execute("DELETE FROM documents WHERE book = $1", book)
            print(f"  删除 documents: {result}")
            result = await conn.execute("DELETE FROM entity_embeddings WHERE book = $1", book)
            print(f"  删除 entity_embeddings: {result}")
            result = await conn.execute("DELETE FROM relationships WHERE book = $1", book)
            print(f"  删除 relationships: {result}")
            result = await conn.execute("DELETE FROM entities WHERE book = $1", book)
            print(f"  删除 entities: {result}")

    print(f"[OK] 书籍 '{book}' 的数据已删除")


# ---------------------------------------------------------------------------
# Embedding 配置获取
# ---------------------------------------------------------------------------


def _get_embedding_config(args: argparse.Namespace) -> dict:
    """从命令行参数或配置文件获取 embedding 配置。"""
    # 尝试从 novel_cli 配置加载
    api_url = args.api_url or os.getenv("EMBEDDING_API_URL", "")
    api_key = args.api_key or os.getenv("EMBEDDING_API_KEY", "")

    if not api_url or not api_key:
        try:
            from novel_cli.config import load_config

            config = load_config()
            novel_db = config.services.novel_db
            if not api_url and novel_db.embedding_api_url:
                api_url = novel_db.embedding_api_url
            if not api_key and novel_db.embedding_api_key:
                api_key = novel_db.embedding_api_key.get_secret_value()
        except Exception:
            pass

    if not api_url or not api_key:
        print(
            "错误: 缺少 Embedding API 配置。\n"
            "请通过以下方式提供：\n"
            "  1. --api-url 和 --api-key 参数\n"
            "  2. EMBEDDING_API_URL 和 EMBEDDING_API_KEY 环境变量\n"
            "  3. 配置文件中的 services.novel_db 配置"
        )
        sys.exit(1)

    return {
        "api_url": api_url,
        "api_key": api_key,
        "model": args.model or os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-4B"),
        "batch_size": args.batch_size or EMBEDDING_BATCH_SIZE,
        "embedding_dim": int(os.getenv("EMBEDDING_DIM", "2560")),
    }


# ---------------------------------------------------------------------------
# 命令处理
# ---------------------------------------------------------------------------


async def _cmd_tables_create(args: argparse.Namespace) -> None:
    """处理 tables create 命令。"""
    uri = _resolve_uri(args.uri)
    embedding_dim = int(os.getenv("EMBEDDING_DIM", "2560"))
    print(f"连接数据库: {uri.split('@')[-1] if '@' in uri else uri}")
    print(f"向量维度: {embedding_dim}")
    pool = await asyncpg.create_pool(uri, min_size=1, max_size=5)
    try:
        await create_tables(pool, embedding_dim=embedding_dim)
        print("\n表结构创建完成！")
    finally:
        await pool.close()


async def _cmd_tables_drop(args: argparse.Namespace) -> None:
    """处理 tables drop 命令。"""
    uri = _resolve_uri(args.uri)
    print(f"连接数据库: {uri.split('@')[-1] if '@' in uri else uri}")
    pool = await asyncpg.create_pool(uri, min_size=1, max_size=5)
    try:
        await drop_tables(pool)
    finally:
        await pool.close()


async def _cmd_import(args: argparse.Namespace) -> None:
    """处理 import 命令。"""
    book = args.book
    uri = _resolve_uri(args.uri)
    book_dir = _get_book_dir(book)

    print(f"书籍: {book}")
    print(f"数据目录: {book_dir}")
    print(f"连接数据库: {uri.split('@')[-1] if '@' in uri else uri}")

    if not book_dir.exists():
        print(f"错误: 数据目录不存在: {book_dir}")
        sys.exit(1)

    pool = await asyncpg.create_pool(uri, min_size=1, max_size=5)
    try:
        # 清除旧数据（默认开启）
        if not args.no_clean:
            print(f"\n--- 清除旧数据 ---")
            await delete_data(pool, book)
        else:
            print(f"\n[跳过] 清除旧数据（--no-clean）")

        # 确保表结构存在
        print("\n--- 检查表结构 ---")
        emb_config = _get_embedding_config(args) if not args.skip_embedding else {}
        embedding_dim = emb_config.get("embedding_dim", 2560)
        await create_tables(pool, embedding_dim=embedding_dim)

        # 导入实体
        print(f"\n--- 导入实体 ---")
        entities_csv = _get_data_path(book_dir, book, "_entities.csv")
        if entities_csv.exists():
            await import_entities(pool, entities_csv)
        else:
            print(f"[跳过] 未找到实体文件: {entities_csv}")

        # 导入关系
        print(f"\n--- 导入关系 ---")
        relationships_csv = _get_data_path(book_dir, book, "_relationships.csv")
        if relationships_csv.exists():
            await import_relationships(pool, relationships_csv)
        else:
            print(f"[跳过] 未找到关系文件: {relationships_csv}")

        # 导入向量（可选）
        if not args.skip_embedding:
            print(f"\n--- 导入向量 ---")
            embed_csv = _get_data_path(book_dir, book, "_entities_embed.csv")
            if embed_csv.exists():
                await import_embeddings(pool, embed_csv, emb_config)
            else:
                print(f"[跳过] 未找到向量文件: {embed_csv}")
        else:
            print(f"\n[跳过] 向量导入（--skip-embedding）")

        # 导入 Corpus（章节+句子）
        print(f"\n--- 导入 Corpus ---")
        corpus_json = _get_data_path(book_dir, book, "_corpus.json")
        if corpus_json.exists():
            await import_corpus(pool, corpus_json, book)
        else:
            print(f"[跳过] 未找到 Corpus 文件: {corpus_json}")

        print("\n数据导入完成！")
    finally:
        await pool.close()


async def _cmd_delete(args: argparse.Namespace) -> None:
    """处理 delete 命令。"""
    uri = _resolve_uri(args.uri)
    pool = await asyncpg.create_pool(uri, min_size=1, max_size=5)
    try:
        await delete_data(pool, args.book)
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# 命令行参数解析
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="PostgreSQL 统一导入工具（pgvector 版本）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 创建表结构
  python scripts/import_pg_data.py tables create

  # 删除所有表
  python scripts/import_pg_data.py tables drop

  # 导入数据（含向量）
  python scripts/import_pg_data.py import --book 凡人修仙传

  # 仅导入实体和关系（不含向量）
  python scripts/import_pg_data.py import --book 凡人修仙传 --skip-embedding

  # 指定 Embedding API 参数
  python scripts/import_pg_data.py import --book 凡人修仙传 \\
      --api-url http://localhost:8000/v1 --api-key your-key

  # 删除数据
  python scripts/import_pg_data.py delete --book 凡人修仙传
        """,
    )

    # 主命令
    subparsers = parser.add_subparsers(dest="command", required=True, help="可用命令")

    # --- tables ---
    tables_parser = subparsers.add_parser("tables", help="表管理")
    tables_subparsers = tables_parser.add_subparsers(
        dest="tables_command", required=True, help="表管理命令"
    )
    tables_subparsers.add_parser("create", help="创建表和索引")
    tables_subparsers.add_parser("drop", help="删除所有表")

    # --- import ---
    import_parser = subparsers.add_parser("import", help="导入数据到 PostgreSQL")
    import_parser.add_argument("--book", required=True, help="书籍名称（如：凡人修仙传）")
    import_parser.add_argument("--no-clean", action="store_true", help="禁用导入前自动清除旧数据（默认自动清除）")
    import_parser.add_argument("--skip-embedding", action="store_true", help="跳过向量导入")
    import_parser.add_argument("--api-url", help="Embedding API 地址")
    import_parser.add_argument("--api-key", help="Embedding API 密钥")
    import_parser.add_argument("--model", default="Qwen/Qwen3-Embedding-4B", help="Embedding 模型名称")
    import_parser.add_argument(
        "--batch-size", type=int, default=EMBEDDING_BATCH_SIZE,
        help=f"Embedding 批处理大小（默认: {EMBEDDING_BATCH_SIZE}）"
    )

    # --- delete ---
    delete_parser = subparsers.add_parser("delete", help="按书名删除数据")
    delete_parser.add_argument("--book", required=True, help="书籍名称")

    # 通用参数
    parser.add_argument("--uri", help="PostgreSQL 连接 URI（如 postgresql://user:pass@host:port/db）")

    return parser.parse_args()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> None:
    """主函数"""
    args = parse_args()

    try:
        if args.command == "tables":
            if args.tables_command == "create":
                asyncio.run(_cmd_tables_create(args))
            elif args.tables_command == "drop":
                asyncio.run(_cmd_tables_drop(args))
        elif args.command == "import":
            asyncio.run(_cmd_import(args))
        elif args.command == "delete":
            asyncio.run(_cmd_delete(args))
    except FileNotFoundError as e:
        print(f"文件错误: {e}")
        sys.exit(1)
    except asyncpg.exceptions.PostgresError as e:
        print(f"数据库错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"执行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
