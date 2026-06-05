# OpenRouter Agent 循环示例

演示通过 OpenRouter 调用模型，支持 reasoning（思考）和工具调用的 Agent 循环。

## 环境变量

在项目根目录的 `.env` 文件中设置：

```env
OPENROUTER_API_KEY=your-api-key
```

## 运行

```bash
cd packages/kosong
uv run examples/openrouter-agent/main.py
```

## 说明

1. 使用 OpenRouter 的免费模型 `google/gemma-4-31b-it:free`
2. 开启 reasoning（思考）功能，模型会先思考再回答
3. Agent 循环：模型决定是否调用工具 → 执行工具 → 返回结果 → 循环
4. 最多迭代 5 次
5. 使用 `kosong.step` 高级 API 实现
