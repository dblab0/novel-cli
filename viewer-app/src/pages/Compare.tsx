import { useState } from "react";
import { useBooks, useAgents, useScenarios, useRuns, useCompare } from "@/api/client";
import type { Dimension } from "@/api/client";
import { buildDimensionLabels } from "@/api/useDimensions";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

/** 维度中文映射（动态从 compareData.dimensions 构建） */

/** 对比数据中各 agent 的维度分数 */
interface AgentDimScores {
  [dimension: string]: number;
}

/** 对比 API 返回的 case 行 */
interface CompareCaseRow {
  case_id: string;
  question: string;
  scores: Record<string, number>;
  run_ids: Record<string, string>;
}

/** 对比 API 返回的维度汇总 */
interface AgentDimensionSummary {
  agent: string;
  dimensions: Record<string, number>;
  total_score: number;
}

/** 对比 API 返回的数据结构 */
interface CompareData {
  cases: CompareCaseRow[];
  dimension_summary: AgentDimensionSummary[];
  dimensions?: Dimension[];
}

/** AGENT 颜色列表 */
const AGENT_COLORS = [
  "hsl(220, 70%, 50%)",
  "hsl(160, 60%, 45%)",
  "hsl(30, 80%, 55%)",
  "hsl(280, 60%, 55%)",
  "hsl(0, 70%, 50%)",
];

/** 单个 Agent 的 run 选择器子组件（在组件内调用 hooks，避免循环中违反 hooks 规则） */
function AgentRunSelector({ book, agent, scenario, selectedRunId, onRunChange }: {
  book: string;
  agent: string;
  scenario: string;
  selectedRunId: string;
  onRunChange: (runId: string) => void;
}) {
  const { data: runs } = useRuns(book, agent, scenario);

  if (!runs?.length) return null;

  return (
    <select
      value={selectedRunId}
      onChange={(e) => onRunChange(e.target.value)}
      className="border rounded px-2 py-1 text-xs bg-card ml-2"
    >
      <option value="">最新 run</option>
      {runs.map((r) => (
        <option key={r.run_id} value={r.run_id}>
          {r.timestamp.replace(/(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})/, "$1 $2:$3")}
        </option>
      ))}
    </select>
  );
}

/** 版本对比页面 - 多版本 Agent 评估结果对比 */
export function Compare() {
  const [selectedBook, setSelectedBook] = useState("");
  const [selectedScenario, setSelectedScenario] = useState("");
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  /** 每个选中的 agent 可指定具体 run_id，空字符串表示使用最新 run */
  const [selectedRunIds, setSelectedRunIds] = useState<Record<string, string>>({});

  /** 数据加载 */
  const { data: books } = useBooks();
  const { data: agents } = useAgents(selectedBook);
  /** 用第一个选中的 agent 来加载 scenarios 列表 */
  const scenarioAgent = selectedAgents.length > 0 ? selectedAgents[0] : "";
  const { data: scenariosForSelect } = useScenarios(selectedBook, scenarioAgent);

  const { data: compareData } = useCompare(selectedBook, selectedAgents, selectedScenario, selectedRunIds);

  /** 切换 agent 选中状态 */
  const toggleAgent = (agent: string) => {
    setSelectedAgents((prev) => {
      const next = prev.includes(agent) ? prev.filter((a) => a !== agent) : [...prev, agent];
      // 取消选中时清除该 agent 的 run_id 选择
      if (!next.includes(agent)) {
        setSelectedRunIds((prev) => {
          const copy = { ...prev };
          delete copy[agent];
          return copy;
        });
      }
      return next;
    });
  };

  /** 更新指定 agent 的 run_id 选择 */
  const setRunId = (agent: string, runId: string) => {
    setSelectedRunIds((prev) => ({ ...prev, [agent]: runId }));
  };

  /** 构建维度对比柱状图数据 */
  const buildDimensionChartData = (data: CompareData) => {
    const dimensions: Dimension[] = data.dimensions ?? [];
    const dimensionLabels = buildDimensionLabels(dimensions);
    const dimKeys = dimensions.length > 0
      ? dimensions.map(d => d.key)
      : Object.keys(data.dimension_summary?.[0]?.dimensions ?? {});
    return dimKeys.map((dim) => {
      const item: Record<string, string | number> = {
        dimension: dimensionLabels[dim] ?? dim,
      };
      for (const agent of selectedAgents) {
        const summary = data.dimension_summary?.find((s) => s.agent === agent);
        item[agent] = summary?.dimensions?.[dim] ?? 0;
      }
      return item;
    });
  };

  /** 计算某 case 在各 agent 间的最大分差 */
  const getMaxDiff = (scores: Record<string, number>) => {
    const vals = Object.values(scores);
    if (vals.length < 2) return 0;
    return Math.max(...vals) - Math.min(...vals);
  };

  return (
    <div className="flex-1 p-6 overflow-auto">
      <h1 className="text-2xl font-bold mb-6">版本对比</h1>

      {/* 选择器区域 */}
      <div className="flex flex-wrap gap-4 mb-6">
        {/* 书籍选择 */}
        <div>
          <label className="block text-xs text-muted-foreground mb-1">书籍</label>
          <select
            className="border rounded px-3 py-1.5 text-sm bg-card"
            value={selectedBook}
            onChange={(e) => {
              setSelectedBook(e.target.value);
              setSelectedAgents([]);
              setSelectedScenario("");
              setSelectedRunIds({});
            }}
          >
            <option value="">选择书籍</option>
            {books?.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
        </div>

        {/* Scenario 选择 */}
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Scenario</label>
          <select
            className="border rounded px-3 py-1.5 text-sm bg-card"
            value={selectedScenario}
            onChange={(e) => setSelectedScenario(e.target.value)}
            disabled={!selectedBook}
          >
            <option value="">选择 Scenario</option>
            {scenariosForSelect?.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        {/* Agent 多选 */}
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Agent 版本</label>
          <div className="flex flex-wrap gap-2">
            {agents?.map((agent) => (
              <label
                key={agent}
                className={`flex items-center gap-1.5 border rounded px-3 py-1.5 text-sm cursor-pointer transition-colors ${
                  selectedAgents.includes(agent)
                    ? "bg-accent border-primary"
                    : "bg-card hover:bg-accent/50"
                }`}
              >
                <input
                  type="checkbox"
                  checked={selectedAgents.includes(agent)}
                  onChange={() => toggleAgent(agent)}
                  className="accent-primary"
                />
                {agent}
                {selectedAgents.includes(agent) && selectedBook && selectedScenario && (
                  <AgentRunSelector
                    book={selectedBook}
                    agent={agent}
                    scenario={selectedScenario}
                    selectedRunId={selectedRunIds[agent] || ""}
                    onRunChange={(runId) => setRunId(agent, runId)}
                  />
                )}
              </label>
            ))}
          </div>
        </div>
      </div>

      {/* 未选择时的提示 */}
      {!compareData && (
        <div className="text-muted-foreground text-sm">
          请选择书籍、Scenario 和至少两个 Agent 版本进行对比
        </div>
      )}

      {/* 对比结果 */}
      {compareData && (
        <>
          {/* 维度对比柱状图 */}
          <div className="rounded-lg border bg-card shadow-sm p-4 mb-6">
            <h3 className="text-sm font-medium mb-3">维度对比</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={buildDimensionChartData(compareData)}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="dimension" tick={{ fontSize: 12 }} />
                <YAxis domain={[0, 5]} tick={{ fontSize: 12 }} />
                <Tooltip />
                <Legend />
                {selectedAgents.map((agent, idx) => (
                  <Bar
                    key={agent}
                    dataKey={agent}
                    fill={AGENT_COLORS[idx % AGENT_COLORS.length]}
                    radius={[4, 4, 0, 0]}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* 对比表格 */}
          <div className="rounded-lg border bg-card shadow-sm">
            <div className="p-4 border-b">
              <h3 className="text-sm font-medium">用例得分对比</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left p-3">Case ID</th>
                    <th className="text-left p-3">问题</th>
                    {selectedAgents.map((agent) => (
                      <th key={agent} className="text-right p-3">{agent}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {compareData.cases?.map((row: CompareCaseRow) => {
                    const maxDiff = getMaxDiff(row.scores);
                    return (
                      <tr key={row.case_id} className="border-b last:border-b-0 hover:bg-accent/50">
                        <td className="p-3">{row.case_id}</td>
                        <td className="p-3 max-w-xs truncate">{row.question}</td>
                        {selectedAgents.map((agent) => {
                          const score = row.scores?.[agent];
                          const runId = row.run_ids?.[agent];
                          const values = Object.values(row.scores) as number[];
                          const maxScore = Math.max(...values);
                          const minScore = Math.min(...values);
                          let cellClass = "text-right p-3 font-mono";
                          if (maxDiff > 1.0 && score !== undefined) {
                            if (score === maxScore) {
                              cellClass += " text-green-600 font-bold";
                            } else if (score === minScore) {
                              cellClass += " text-red-600 font-bold";
                            }
                          }
                          return (
                            <td key={agent} className={cellClass}>
                              {runId ? (
                                <a
                                  href={`/case/${encodeURIComponent(runId)}/${encodeURIComponent(row.case_id)}`}
                                  className="hover:underline"
                                >
                                  {score !== undefined ? score.toFixed(2) : "-"}
                                </a>
                              ) : (
                                score !== undefined ? score.toFixed(2) : "-"
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
