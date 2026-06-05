# 小说知识库数据导入指南

本文档说明如何启动 PostgreSQL 数据库并将小说数据导入到知识库中。

---

## 1. 前置条件

- [Docker](https://www.docker.com/) 已安装并运行
- [uv](https://docs.astral.sh/uv/) 已安装（Python 包管理工具）
- 小说数据文件已准备好（位于 `data/input_data/<书名>/` 目录下）

## 2. 数据文件说明

每本书的数据目录结构如下：

```
data/input_data/凡人修仙传/
├── 凡人修仙传_entities.csv          # 实体数据（人物、法宝、功法等）
├── 凡人修仙传_relationships.csv      # 实体间的关系数据
├── 凡人修仙传_entities_embed.csv     # 需要生成向量的实体列表
└── 凡人修仙传_corpus.json           # 小说原文（章节 + 文本）
```

| 文件 | 必需 | 说明 |
|------|------|------|
| `*_entities.csv` | 是 | 人物/法宝/功法/地点/组织等实体 |
| `*_relationships.csv` | 是 | 实体之间的关系（如师徒、敌对） |
| `*_entities_embed.csv` | 否 | 需要生成语义向量的实体，导入时需调用 Embedding API |
| `*_corpus.json` | 否 | 小说原文章节，支持按章节、关键词检索 |

## 3. 配置环境变量

### 3.1 复制示例配置

```bash
cp docker/.env.example docker/.env
```

### 3.2 编辑 `docker/.env`

```bash
# PostgreSQL 配置
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=novel_graph_search
POSTGRES_USER=novel_admin
POSTGRES_PASSWORD=changeme_pgsql    # ← 请修改为安全密码

# Embedding 模型配置（向量导入时需要）
EMBEDDING_API_URL=https://api.siliconflow.cn/v1
EMBEDDING_API_KEY=sk-xxxx           # ← 填入你的 API Key
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-4B
EMBEDDING_DIM=2560
```

> **关于 EMBEDDING_API_URL**：只需填到 `/v1` 为止，脚本会自动拼接 `/embeddings`。
>
> **关于 EMBEDDING_DIM**：默认 2560（Qwen3-Embedding-4B 的输出维度）。如果维度超过 2000，脚本会自动使用 `halfvec` 类型以支持 HNSW 索引。

## 4. 启动数据库

> **重要**：必须指定 `-p novel-cli` 项目名称，否则会与本机其他 `docker/` 目录下的 compose 项目冲突（Docker Compose 默认以目录名作为 project name，多个项目共用 `docker/` 目录名会导致容器互相覆盖）。

```bash
cd docker
docker compose -p novel-cli up -d
```

确认数据库正常运行：

```bash
docker compose -p novel-cli ps
```

状态应为 `healthy`。

## 5. 导入数据

所有导入命令都在项目根目录下执行。

### 5.1 完整导入（推荐）

包含实体、关系、向量、原文，一条命令搞定：

```bash
uv run python script/import_pg_data.py import --book 凡人修仙传
```

### 5.2 仅导入实体和关系（不需要向量）

如果不需要语义搜索功能，或者暂时没有 Embedding API Key：

```bash
uv run python script/import_pg_data.py import --book 凡人修仙传 --skip-embedding
```

### 5.3 指定 Embedding 参数

如果不使用 `docker/.env` 中的配置，也可以通过命令行参数覆盖：

```bash
uv run python script/import_pg_data.py import --book 凡人修仙传 \
    --api-url https://api.siliconflow.cn/v1 \
    --api-key sk-xxxx \
    --model Qwen/Qwen3-Embedding-4B \
    --batch-size 50
```

参数优先级：**命令行参数 > 环境变量 > 配置文件**

### 5.4 仅创建表结构

不导入任何数据，只创建数据库表和索引：

```bash
uv run python script/import_pg_data.py tables create
```

### 5.5 指定数据库连接

默认从 `docker/.env` 读取 `POSTGRES_*` 变量拼接连接地址。也可以手动指定：

```bash
uv run python script/import_pg_data.py import --book 凡人修仙传 \
    --uri postgresql://novel_admin:yourpassword@localhost:5433/novel_graph_search
```

## 6. 删除数据

按书名删除该书的全部数据（包括实体、关系、向量和原文）：

```bash
uv run python script/import_pg_data.py delete --book 凡人修仙传
```

> **注意**：此操作不可逆，请谨慎使用。

## 7. 重复导入

脚本支持安全重复执行：
- 实体、关系：已存在的记录会被更新（upsert）
- 向量：已存在的记录会被覆盖
- 原文章节：已存在的章节会被跳过

因此可以直接重新运行导入命令来增量更新数据。

## 8. 导入后验证

```bash
# 查看各表数据量
uv run python -c "
import asyncio, asyncpg, os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('docker/.env'))
async def check():
    conn = await asyncpg.connect(
        host=os.getenv('POSTGRES_HOST'), port=int(os.getenv('POSTGRES_PORT','5432')),
        database=os.getenv('POSTGRES_DB'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'))
    for t in ['entities','relationships','entity_embeddings','document_sources','documents','sentences']:
        n = await conn.fetchval(f'SELECT COUNT(*) FROM {t}')
        print(f'{t}: {n}')
    await conn.close()
asyncio.run(check())
"
```

预期输出（以凡人修仙传为例）：

```
entities: 3885
relationships: 12829
entity_embeddings: 3885
document_sources: 1
documents: 2456
sentences: 236513
```

## 9. 停止数据库

```bash
cd docker
docker compose -p novel-cli down
```

数据会保留在 Docker volume `postgres_data` 中。如需彻底清除数据：

```bash
docker compose -p novel-cli down -v
```

## 10. 常见问题

### Q: 向量导入时报 "column cannot have more than 2000 dimensions"

脚本已自动处理。如果你自定义了 `EMBEDDING_DIM` 且超过 2000，请确保数据库中的表是用最新版脚本创建的。可以用 `delete` 命令删除旧数据后重新导入。

### Q: 向量导入速度慢

向量导入需要调用外部 Embedding API 逐批生成，耗时长短取决于 API 服务速度和网络状况。可以通过 `--batch-size` 调大每批数量（默认 50）。

### Q: 连接数据库失败

- 确认 Docker 容器正在运行且状态为 `healthy`
- 检查 `docker/.env` 中的 `POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_PASSWORD` 是否正确
- 如果数据库部署在远程服务器，确认 `POSTGRES_HOST` 填写的是正确的 IP 地址

### Q: 数据文件放在哪里

数据文件统一放在 `data/input_data/<书名>/` 目录下。如果数据目录与书名不完全一致，请确保 CSV 文件名中包含书名（如 `凡人修仙传_entities.csv`）。
