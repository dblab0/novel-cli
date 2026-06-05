import useSWR from "swr";

const API_BASE = "/api";

/** 维度配置 */
export interface Dimension {
  key: string;
  label: string;
  description: string;
  improvement_suggestion?: string;
}

/** 错误模式汇总 */
export interface ErrorPatternSummary {
  count: number;
  affected_cases: string[];
}

/** 报告数据 */
export interface Report {
  task_type?: string;
  dimensions?: Dimension[];
  total_cases: number;
  total_score: number;
  dimension_scores: Record<string, number>;
  pass_rate: number;
  cases_summary?: {
    case_id: string;
    question: string;
    total_score: number;
    execution_time?: number;
    passed?: boolean;
    error_patterns?: string[];
  }[];
  tool_stats?: Record<string, unknown>;
  error_patterns_summary?: Record<string, ErrorPatternSummary>;
}

/** 错误模式中文映射 */
export const ERROR_PATTERN_LABELS: Record<string, { label: string; color: string }> = {
  infinite_loop:    { label: "无限循环", color: "red" },
  null_params:      { label: "空参数", color: "red" },
  no_tool_use:      { label: "未使用工具", color: "red" },
  wrong_tool_order: { label: "工具顺序错误", color: "yellow" },
  result_ignored:   { label: "结果未利用", color: "yellow" },
};

/** 通用请求函数 */
async function fetcher<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const error = new Error("API 请求失败");
    throw error;
  }
  return res.json();
}

/** 书籍列表 */
export function useBooks() {
  return useSWR<string[]>(`${API_BASE}/books`, fetcher);
}

/** Agent 版本列表 */
export function useAgents(book: string) {
  return useSWR<string[]>(
    book ? `${API_BASE}/books/${encodeURIComponent(book)}/agents` : null,
    fetcher
  );
}

/** Scenario 列表 */
export function useScenarios(book: string, agent: string) {
  return useSWR<string[]>(
    book && agent
      ? `${API_BASE}/books/${encodeURIComponent(book)}/agents/${encodeURIComponent(agent)}/scenarios`
      : null,
    fetcher
  );
}

/** 运行批次列表 */
export function useRuns(book: string, agent: string, scenario: string) {
  return useSWR<{ run_id: string; model: string; timestamp: string }[]>(
    book && agent && scenario
      ? `${API_BASE}/books/${encodeURIComponent(book)}/agents/${encodeURIComponent(agent)}/scenarios/${encodeURIComponent(scenario)}/runs`
      : null,
    fetcher
  );
}

/** 运行报告 */
export function useReport(runId: string) {
  return useSWR<Report>(
    runId ? `${API_BASE}/runs/${encodeURIComponent(runId)}/report` : null,
    fetcher
  );
}

/** Case 列表 */
export function useCases(runId: string) {
  return useSWR<any[]>(
    runId ? `${API_BASE}/runs/${encodeURIComponent(runId)}/cases` : null,
    fetcher
  );
}

/** Run 详情 */
export function useRunInfo(runId: string) {
  return useSWR<any>(
    runId ? `${API_BASE}/runs/${encodeURIComponent(runId)}` : null,
    fetcher
  );
}

/** Case 详情 */
export function useCase(runId: string, caseId: string) {
  return useSWR<any>(
    (runId && caseId) ? `${API_BASE}/runs/${encodeURIComponent(runId)}/cases/${encodeURIComponent(caseId)}` : null,
    fetcher
  );
}

/** Case 消息记录（markdown，用于复制） */
export function useCaseMessages(runId: string, caseId: string) {
  return useSWR<{ content: string }>(
    (runId && caseId) ? `${API_BASE}/runs/${encodeURIComponent(runId)}/cases/${encodeURIComponent(caseId)}/messages` : null,
    fetcher
  );
}

/** Case 消息记录（JSONL 结构化，用于渲染） */
export function useCaseMessagesJsonl(runId: string, caseId: string) {
  return useSWR<MessageItem[]>(
    (runId && caseId) ? `${API_BASE}/runs/${encodeURIComponent(runId)}/cases/${encodeURIComponent(caseId)}/messages/jsonl` : null,
    fetcher
  );
}

/** JSONL 消息项类型 */
export interface ContentBlock {
  type: string;
  think?: string;
  text?: string;
}

export interface ToolCallFunction {
  name: string;
  arguments: string;
}

export interface ToolCallInfo {
  type: string;
  id: string;
  function: ToolCallFunction;
}

export interface MessageItem {
  role: "system" | "user" | "assistant" | "tool";
  content: string | ContentBlock[];
  tool_calls?: ToolCallInfo[];
  tool_call_id?: string;
}

/** 版本对比 */
export function useCompare(
  book: string,
  agents: string[],
  scenario: string,
  runIds?: Record<string, string>
) {
  const buildKey = () => {
    if (!book || agents.length === 0 || !scenario) return null;
    const params = new URLSearchParams({
      book,
      agents: agents.join(","),
      scenario,
    });
    // 将 run_ids 映射编码为 run_id_{agent}=xxx 扁平参数
    if (runIds) {
      for (const [agent, runId] of Object.entries(runIds)) {
        if (runId) {
          params.set(`run_id_${agent}`, runId);
        }
      }
    }
    return `${API_BASE}/compare?${params.toString()}`;
  };
  return useSWR<any>(buildKey(), fetcher);
}

/** 时间趋势 */
export function useTrend(book: string, agent: string, scenario: string) {
  return useSWR<any[]>(
    book && agent && scenario
      ? `${API_BASE}/trend?book=${encodeURIComponent(book)}&agent=${encodeURIComponent(agent)}&scenario=${encodeURIComponent(scenario)}`
      : null,
    fetcher
  );
}

/** 触发索引重建 */
export async function reindex(): Promise<void> {
  await fetch(`${API_BASE}/reindex`, { method: "POST" });
}
