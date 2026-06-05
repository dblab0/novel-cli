# 智谱 AI 基础对话示例

演示如何使用 Zhipu provider 进行基础对话和多轮对话。

## 环境变量

在项目根目录的 `.env` 文件中设置：

```env
ZHIPU_API_KEY=your-api-key
```

## 运行

```bash
# 从 packages/kosong 目录
cd packages/kosong
uv run examples/zhipu-basic/main.py

# 或从项目根目录
uv run packages/kosong/examples/zhipu-basic/main.py
```
