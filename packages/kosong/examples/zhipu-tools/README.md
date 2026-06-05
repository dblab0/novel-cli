# 智谱 AI 工具调用示例

演示如何使用智谱 GLM 模型进行 Function Call。

## 环境变量

在项目根目录的 `.env` 文件中设置：

```env
ZHIPU_API_KEY=your-api-key
```

## 运行

```bash
cd packages/kosong
uv run examples/zhipu-tools/main.py
```

## 说明

1. 定义工具（Tool）
2. 发送用户消息
3. 模型决定调用工具
4. 执行工具并返回结果
5. 模型根据结果生成最终回答
