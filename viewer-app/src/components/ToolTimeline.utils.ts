/**
 * ToolTimeline 工具函数模块
 *
 * 提供与后端 report.py 重复检测逻辑对齐的前端工具函数。
 * 后端使用 json.dumps(params, sort_keys=True) 生成去重键，
 * 因此前端需要实现递归排序版本的 stringify 以确保判定一致。
 */

/**
 * 递归排序所有层级的 key 后序列化，与 Python json.dumps(sort_keys=True) 对齐。
 *
 * 注意：JSON.stringify 的数组 replacer 只排序顶层属性，
 * 对于嵌套 params 如 {query: {b: 1, a: 2}}，需要递归处理才能与后端一致。
 *
 * @param obj - 待序列化的值
 * @returns 排序后的 JSON 字符串
 */
export function sortedStringify(obj: unknown): string {
  // null 或非 object 类型直接序列化
  if (obj === null || typeof obj !== "object") return JSON.stringify(obj);

  // 数组：递归处理每个元素，保持数组顺序
  if (Array.isArray(obj)) {
    return "[" + obj.map(sortedStringify).join(",") + "]";
  }

  // 对象：按 key 排序后递归处理每个 value
  const record = obj as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  return (
    "{" +
    keys
      .map((k) => JSON.stringify(k) + ":" + sortedStringify(record[k]))
      .join(",") +
    "}"
  );
}

/**
 * 计算每个工具调用的全局重复标记（与后端 report.py:127-134 对齐）。
 *
 * 后端逻辑：以 (tool_name, json.dumps(params, sort_keys=True)) 为键，
 * 全局统计出现次数，出现超过一次即为重复。
 *
 * @param toolCalls - 工具调用列表，每项包含 tool_name 和可选的 params
 * @returns boolean 数组，true 表示该位置是重复调用
 */
export function computeDuplicateFlags(
  toolCalls: { tool_name: string; params?: Record<string, unknown> }[]
): boolean[] {
  /** 已见过的键及其出现次数 */
  const seen = new Map<string, number>();
  return toolCalls.map((tc) => {
    const key = `${tc.tool_name}::${sortedStringify(tc.params ?? {})}`;
    const count = (seen.get(key) ?? 0) + 1;
    seen.set(key, count);
    return count > 1;
  });
}
