# 智谱 AI 深度思考示例

演示如何启用 GLM 模型的深度思考功能。

## 环境变量

在项目根目录的 `.env` 文件中设置：

```env
ZHIPU_API_KEY=your-api-key
```

## 运行

```bash
cd packages/kosong
uv run examples/zhipu-thinking/main.py
```

## 说明

使用 `with_thinking("high")` 启用深度思考模式，模型会在回答前先展示思考过程。
