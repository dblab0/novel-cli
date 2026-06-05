# 智谱 AI Agent 循环示例

演示一个简单的 Agent，可以多次调用工具直到完成任务。

## 环境变量

在项目根目录的 `.env` 文件中设置：

```env
ZHIPU_API_KEY=your-api-key
```

## 运行

```bash
cd packages/kosong
uv run examples/zhipu-agent/main.py
```

## 说明

1. Agent 接收用户任务
2. 模型决定是否调用工具
3. 如果调用工具，执行并返回结果
4. 循环直到模型给出最终答案
5. 最多迭代 5 次
